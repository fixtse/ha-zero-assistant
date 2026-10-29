"""Bounded public callbacks. Never proxy administration, cookies or arbitrary URLs."""
import asyncio
import logging
import re

import aiohttp
from aiohttp import web
from yarl import URL
from homeassistant.components.http import HomeAssistantView, KEY_HASS
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.network import get_url, NoURLAvailableError

_LOGGER = logging.getLogger(__name__)
_KEY = "assistant_zero_relays"
_PREFIX = "/api/assistant_zero/relay/"
_CALLBACK = "/v1/admin/google/oauth/callback"
_TELEGRAM = re.compile(r"/telegram/assistants/[a-z0-9][a-z0-9_-]{0,99}/webhook")
_LIMIT = 1_000_000


async def _control(hass, connection, method="GET", value=None):
    token = connection.get("settings_token")
    if not token:
        raise ValueError("Relay requires a dedicated integration credential")
    async with async_get_clientsession(hass).request(
        method, connection["url"].rstrip("/") + "/v1/integrations/home-assistant/relay",
        headers={"Authorization": "Bearer " + token}, json=value,
        timeout=aiohttp.ClientTimeout(total=10), allow_redirects=False,
    ) as response:
        if response.status != 200:
            raise ValueError("Relay control rejected")
        data = await response.content.read(16385)
        if len(data) > 16384:
            raise ValueError("Relay control response too large")
        import json
        return json.loads(data)


class ZeroRelayView(HomeAssistantView):
    url = _PREFIX + "{entry_id}/{path:.*}"
    name = "api:assistant_zero:relay"
    requires_auth = False

    async def get(self, request, entry_id, path):
        if "/" + path != _CALLBACK or len(request.query_string) > 8192:
            return web.Response(status=404)
        connection = request.app[KEY_HASS].data.get(_KEY, {}).get(entry_id)
        if not connection:
            return web.Response(status=404)
        try:
            # Check revocation before redirecting. Browser cookies remain exclusively at Zero.
            control = await _control(request.app[KEY_HASS], connection)
            target = URL(control["adminUrl"])
            if target.scheme not in ("http", "https") or not target.host or target.user or target.password or target.query_string or target.fragment or target.path not in ("", "/"):
                raise ValueError("Invalid callback destination")
            query = {key: request.query[key] for key in ("state", "code", "error") if key in request.query}
            target = target.with_path(_CALLBACK).with_query(query)
            return web.Response(status=303, headers={"Location": str(target), "Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})
        except (aiohttp.ClientError, TimeoutError, ValueError, KeyError):
            return web.Response(status=503, headers={"Cache-Control": "no-store"})

    async def post(self, request, entry_id, path):
        target_path = "/" + path
        if (not _TELEGRAM.fullmatch(target_path) and target_path != "/v1/triggers/rule-events") or request.query_string:
            return web.Response(status=404)
        hass = request.app[KEY_HASS]
        connection = hass.data.get(_KEY, {}).get(entry_id)
        if not connection:
            return web.Response(status=404)
        headers = {"Content-Type": "application/json"}
        if _TELEGRAM.fullmatch(target_path):
            secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", secret):
                return web.Response(status=401)
            headers["X-Telegram-Bot-Api-Secret-Token"] = secret
        else:
            authorization = request.headers.get("Authorization", "")
            if not re.fullmatch(r"Bearer [A-Za-z0-9_-]{24,8192}", authorization):
                return web.Response(status=401)
            headers["Authorization"] = authorization
        if request.content_length is not None and request.content_length > _LIMIT:
            return web.Response(status=413)
        payload = bytearray()
        try:
            async with asyncio.timeout(10):
                async for chunk in request.content.iter_chunked(16384):
                    payload.extend(chunk)
                    if len(payload) > _LIMIT:
                        return web.Response(status=413)
        except TimeoutError:
            return web.Response(status=408)
        try:
            await _control(hass, connection)
            async with async_get_clientsession(hass).post(
                connection["url"].rstrip("/") + target_path,
                data=bytes(payload), headers=headers,
                timeout=aiohttp.ClientTimeout(total=15), allow_redirects=False,
            ) as response:
                # Preserve delivery status without exposing provider bodies, cookies or redirects.
                status = response.status if not 300 <= response.status < 400 else 502
                return web.Response(status=status, headers={"Cache-Control": "no-store"})
        except (aiohttp.ClientError, TimeoutError, ValueError):
            # An uncertain POST is never replayed by this integration.
            return web.Response(status=503, headers={"Cache-Control": "no-store"})


async def async_setup_relay(hass, entry):
    if _KEY not in hass.data:
        hass.data[_KEY] = {}
        hass.http.register_view(ZeroRelayView())
    hass.data[_KEY][entry.entry_id] = dict(entry.data)
    try:
        external = URL(get_url(hass, allow_internal=False, allow_cloud=True, require_ssl=True, prefer_external=True))
        if external.scheme != "https" or external.user or external.password or external.query_string or external.fragment or external.path not in ("", "/"):
            return
        public_url = str(external.origin()) + _PREFIX + entry.entry_id
        result = await _control(hass, entry.data, "POST", {"url": public_url})
        if result.get("configured") and entry.data.get("callback_url"):
            hass.config_entries.async_update_entry(entry, data={**entry.data, "callback_url": public_url + "/v1/triggers/rule-events"})
            hass.data[_KEY][entry.entry_id] = dict(entry.data)
    except (NoURLAvailableError, aiohttp.ClientError, TimeoutError, ValueError, KeyError):
        _LOGGER.warning("Zero public relay could not be announced; check the external HTTPS URL and integration access. No automatic retry.")


def async_unload_relay(hass, entry):
    hass.data.get(_KEY, {}).pop(entry.entry_id, None)
