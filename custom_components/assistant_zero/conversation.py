"""Route Assist speech to the existing Zero worker, returning speech to Assist TTS."""
import json
import uuid

import aiohttp
from homeassistant.components import conversation
from homeassistant.helpers import entity_registry as er, intent
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from . import DOMAIN
from .client import AccessDenied, BridgeError, converse


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([ZeroConversation(entry)])


class ZeroConversation(conversation.ConversationEntity):
    _attr_name = "Zero"
    _attr_supported_features = conversation.ConversationEntityFeature.CONTROL

    def __init__(self, entry):
        self._entry = entry
        self._attr_unique_id = "assistant_zero"

    @property
    def device_info(self):
        return DeviceInfo(
            identifiers={(DOMAIN, DOMAIN)},
            name="Zero",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url=self._entry.data["url"].rstrip("/") + "/admin",
        )

    @property
    def supported_languages(self):
        return "*"

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        conversation.async_set_agent(self.hass, self._entry, self)

    async def async_will_remove_from_hass(self):
        conversation.async_unset_agent(self.hass, self._entry)
        await super().async_will_remove_from_hass()

    async def async_process(self, user_input):
        response = intent.IntentResponse(language=user_input.language)
        conversation_id = user_input.conversation_id or str(uuid.uuid4())
        settings = self._entry.data
        satellite = er.async_get(self.hass).async_get(settings["satellite_id"])
        # HA authenticates the user; Zero resolves the binding and permissions.
        # A device-only satellite retains the legacy owner route.
        user_id = user_input.context.user_id
        user = await self.hass.auth.async_get_user(user_id) if user_id else None
        if user_id:
            allowed = bool(user and user.is_active and not user.system_generated)
        else:
            allowed = satellite is not None and (
                user_input.satellite_id == satellite.entity_id
                or (user_input.device_id is not None and user_input.device_id == satellite.device_id)
            )
        if not allowed:
            response.async_set_error(intent.IntentResponseErrorCode.UNKNOWN, "Este dispositivo no tiene acceso a Zero.")
            return conversation.ConversationResult(response=response, conversation_id=conversation_id)

        async def request(method, path, data):
            async with async_get_clientsession(self.hass).request(
                method, settings["url"].rstrip("/") + path,
                headers={"Authorization": "Bearer " + settings["token"]},
                json=data, timeout=aiohttp.ClientTimeout(total=15),
                allow_redirects=False,
            ) as result:
                if result.status == 403:
                    raise AccessDenied("User is not bound to an enabled Zero profile")
                if result.status not in (200, 202):
                    raise BridgeError("Bridge unavailable")
                payload = bytearray()
                async for chunk in result.content.iter_chunked(16384):
                    payload.extend(chunk)
                    if len(payload) > 1_000_000:
                        raise BridgeError("Response too large")
                return json.loads(payload)

        try:
            output = await converse(request, user_input.text, settings["satellite_id"], ha_user_id=user_id)
            response.async_set_speech(output)
        except AccessDenied:
            response.async_set_error(
                intent.IntentResponseErrorCode.UNKNOWN,
                "Tu usuario de Home Assistant no está vinculado a un usuario activo de Zero. Configura el vínculo en la integración Zero Assistant.",
            )
        except (BridgeError, TimeoutError, aiohttp.ClientError, ValueError, KeyError, TypeError):
            response.async_set_error(
                intent.IntentResponseErrorCode.UNKNOWN,
                "No pude confirmar la respuesta de Zero. Si pediste una acción, comprueba su resultado antes de repetirla.",
            )
        return conversation.ConversationResult(response=response, conversation_id=conversation_id)
