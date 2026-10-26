"""Optional HA client for Zero; prompt and access policy remain owned by Zero."""
import aiohttp
import secrets
import re
from homeassistant.helpers.network import get_url, NoURLAvailableError
from .pairing import zero_request, create_ha_token, register_callback, pairing_secret
import voluptuous as vol
from yarl import URL
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector
from . import DOMAIN
from .client import BridgeError
from .settings import settings_request


def connection_schema(discovered_url=None):
    return vol.Schema({
        vol.Required("url", **({"default": discovered_url} if discovered_url else {})): selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.URL)),
        vol.Required("token"): selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)),
        vol.Required("satellite_id"): selector.EntitySelector(selector.EntitySelectorConfig(domain="assist_satellite")),
        vol.Optional("callback_url"): selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.URL)),
        vol.Optional("callback_token"): selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)),
        vol.Required("settings_token"): selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)),
    })


class ZeroConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_zeroconf(self, discovery_info):
        # Zero is a singleton. Discovery must not overwrite an installed connection.
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        try:
            advertised = discovery_info.properties.get("url")
            url = URL(advertised) if advertised else URL.build(scheme="http", host=discovery_info.host, port=discovery_info.port)
            if url.scheme not in ("http", "https") or not url.host or url.user or url.password or url.query_string or url.fragment:
                raise ValueError("Invalid discovery URL")
            self._discovered_url = str(url).rstrip("/")
        except (TypeError, ValueError):
            return self.async_abort(reason="invalid_discovery")
        self.context["title_placeholders"] = {"name": "Zero"}
        return await self.async_step_user()

    async def async_step_import(self, data):
        await self.async_set_unique_id(DOMAIN)
        # Do not replace UI-managed connection settings on every YAML import.
        existing = next((entry for entry in self._async_current_entries() if entry.unique_id == DOMAIN), None)
        if existing and not existing.data.get("settings_token") and data.get("settings_token"):
            self.hass.config_entries.async_update_entry(existing, data={**existing.data, "settings_token": data["settings_token"]})
        if existing:
            additions = {key: data[key] for key in ("callback_url", "callback_token") if key in data and key not in existing.data}
            if additions:
                self.hass.config_entries.async_update_entry(existing, data={**existing.data, **additions})
        self._abort_if_unique_id_configured()
        return self.async_create_entry(title="Zero", data=data)

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            if user_input.get("manual"):
                self._discovered_url = user_input["url"]
                return await self.async_step_manual()
            try:
                if any(not user_input.get(key) for key in ("home_assistant_url", "user_id", "satellite_id")):
                    raise ValueError("Complete the guided connection fields")
                url = URL(user_input["url"])
                ha_url = URL(user_input["home_assistant_url"])
                for candidate in (url, ha_url):
                    if candidate.scheme not in ("http", "https") or not candidate.host or candidate.user or candidate.password or candidate.query_string or candidate.fragment or candidate.path not in ("", "/"):
                        raise ValueError("Invalid server origin")
                user = await self.hass.auth.async_get_user(user_input["user_id"])
                if not user or not user.is_active or user.system_generated:
                    raise ValueError("Invalid user")
                self._connection = {"url": str(url).rstrip("/"), "satellite_id": user_input["satellite_id"]}
                self._ha_user_id = user.id
                self._pairing_secret = pairing_secret()
                self._pairing_state = pairing_secret()
                self._pairing = await zero_request(self.hass, self._connection["url"], "/v1/pairing/home-assistant", {
                    "secret": self._pairing_secret, "state": self._pairing_state,
                    "homeAssistantUrl": str(ha_url.origin()), "flowId": self.flow_id, "userId": user.id,
                })
                approval = URL(self._pairing["approvalUrl"])
                if approval.origin() != url.origin() or approval.path != "/admin/":
                    raise ValueError("Invalid approval destination")
                register_callback(self.hass)
                return await self.async_step_pair()
            except (aiohttp.ClientError, TimeoutError, ValueError, KeyError):
                errors["base"] = "pairing_failed"
        try:
            ha_url = get_url(self.hass, allow_external=False)
        except NoURLAvailableError:
            ha_url = ""
        users = [user for user in await self.hass.auth.async_get_users() if user.is_active and not user.system_generated]
        schema = vol.Schema({
            vol.Required("url", default=getattr(self, "_discovered_url", "")): selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.URL)),
            vol.Optional("home_assistant_url", default=ha_url): selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.URL)),
            vol.Optional("user_id"): selector.SelectSelector(selector.SelectSelectorConfig(options=[{"value": user.id, "label": user.name or user.id} for user in users], mode=selector.SelectSelectorMode.DROPDOWN)),
            vol.Optional("satellite_id"): selector.EntitySelector(selector.EntitySelectorConfig(domain="assist_satellite")),
            vol.Optional("manual", default=False): bool,
        })
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_pair(self, user_input=None):
        if user_input is not None:
            if not re.fullmatch(r"[a-f0-9]{64}", str(user_input.get("state", ""))) or not secrets.compare_digest(str(user_input["state"]), self._pairing_state):
                raise ValueError("Invalid pairing state")
            if getattr(self, "_pairing_approved", False):
                raise ValueError("Pairing callback already consumed")
            self._pairing_approved = True
            return self.async_external_step_done(next_step_id="finish_pairing")
        return self.async_external_step(step_id="pair", url=self._pairing["approvalUrl"])

    async def async_step_finish_pairing(self, user_input=None):
        if not getattr(self, "_pairing_approved", False) or getattr(self, "_pairing_consumed", False):
            return self.async_abort(reason="pairing_failed")
        self._pairing_consumed = True
        refresh = None
        try:
            await self.async_set_unique_id(DOMAIN)
            self._abort_if_unique_id_configured()
            refresh, token = await create_ha_token(self.hass, self._ha_user_id, self.flow_id)
            credentials = await zero_request(self.hass, self._connection["url"], "/v1/pairing/home-assistant/exchange", {
                "id": self._pairing["id"], "secret": self._pairing_secret, "homeAssistantToken": token,
            })
            connection = {**self._connection, **{key: credentials[key] for key in ("token", "settings_token", "callback_token", "callback_url")}}
            return self.async_create_entry(title="Zero", data=connection)
        except (aiohttp.ClientError, TimeoutError, ValueError, KeyError):
            if refresh is not None:
                self.hass.auth.async_remove_refresh_token(refresh)
            return self.async_abort(reason="pairing_failed")

    async def async_step_manual(self, user_input=None):

        errors = {}
        if user_input is not None:
            try:
                await settings_request(self.hass, user_input)
            except (BridgeError, aiohttp.ClientError, TimeoutError, ValueError, KeyError):
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(DOMAIN)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title="Zero", data=user_input)
        return self.async_show_form(step_id="manual", data_schema=connection_schema(getattr(self, "_discovered_url", None)), errors=errors)

    async def async_step_reconfigure(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                await settings_request(self.hass, user_input)
            except (BridgeError, aiohttp.ClientError, TimeoutError, ValueError, KeyError):
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(self._get_reconfigure_entry(), data_updates=user_input)
        return self.async_show_form(step_id="reconfigure", data_schema=connection_schema(), errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return ZeroOptionsFlow()


class ZeroOptionsFlow(config_entries.OptionsFlow):
    async def async_step_init(self, user_input=None):
        return self.async_show_menu(step_id="init", menu_options=["assistant", "users", "delivery"])

    async def async_step_users(self, user_input=None):
        errors = {}
        try:
            if user_input is not None:
                user = await self.hass.auth.async_get_user(user_input["user_id"])
                if not user or not user.is_active or user.system_generated:
                    raise BridgeError("invalid_user")
                await settings_request(self.hass, self.config_entry.data, "PUT", {
                    "revision": self._users_revision,
                    "userId": user.id,
                    "chatId": user_input.get("chat_id", "").strip() or None,
                }, path="/users")
                return self.async_create_entry(title="", data={})
            self._users_settings = await settings_request(self.hass, self.config_entry.data)
            self._users_revision = self._users_settings["revision"]
        except (BridgeError, aiohttp.ClientError, TimeoutError, ValueError, KeyError) as error:
            if str(error) != "invalid_user":
                return self.async_abort(reason="conflict" if str(error) == "conflict" else "cannot_connect")
            errors["base"] = "invalid_user"
        users = [user for user in await self.hass.auth.async_get_users() if user.is_active and not user.system_generated]
        bindings = self._users_settings.get("homeAssistantUsers", {})
        choices = [{"value": user.id, "label": (user.name or user.id) + (" → " + bindings[user.id] if user.id in bindings else "")} for user in users]
        if not choices:
            return self.async_abort(reason="no_users")
        schema = vol.Schema({
            vol.Required("user_id"): selector.SelectSelector(selector.SelectSelectorConfig(options=choices, mode=selector.SelectSelectorMode.DROPDOWN)),
            vol.Optional("chat_id", default=""): selector.TextSelector(),
        })
        return self.async_show_form(step_id="users", data_schema=schema, errors=errors)

    async def async_step_delivery(self, user_input=None):
        if user_input is not None:
            if len(user_input["callback_token"]) < 24:
                return self.async_show_form(step_id="delivery", data_schema=self._delivery_schema(), errors={"base": "invalid_token"})
            self.hass.config_entries.async_update_entry(self.config_entry, data={**self.config_entry.data, **user_input})
            from .rules import async_setup_rules
            manager = await async_setup_rules(self.hass, self.config_entry.data)
            manager.config = dict(self.config_entry.data)
            return self.async_create_entry(title="", data={})
        return self.async_show_form(step_id="delivery", data_schema=self._delivery_schema())

    def _delivery_schema(self):
        return vol.Schema({
            vol.Required("callback_url", default=self.config_entry.data.get("callback_url", "")): selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.URL)),
            vol.Required("callback_token"): selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)),
        })

    async def async_step_assistant(self, user_input=None):
        errors = {}
        available = self.hass.services.async_services()
        frigate_available = bool(available.get("frigate"))
        try:
            if user_input is not None:
                value = {**user_input, "revision": self._revision}
                raw = value.get("services", [])
                # Accept older clients too; split comma-separated custom values.
                if isinstance(raw, str):
                    raw = raw.splitlines()
                value["services"] = list(dict.fromkeys(
                    service.strip() for item in raw for service in item.split(",") if service.strip()
                ))
                if value.get("assist") and "conversation.process" not in value["services"]:
                    value["services"].append("conversation.process")
                value["frigate"] = value.get("frigate", self._current_frigate) if frigate_available else self._current_frigate
                await settings_request(self.hass, self.config_entry.data, "PUT", value)
                # No prompt or policy snapshot is stored in config entry options.
                return self.async_create_entry(title="", data={})
            current = await settings_request(self.hass, self.config_entry.data)
            self._revision = current["revision"]
            self._current_frigate = current["frigate"]
        except (BridgeError, aiohttp.ClientError, TimeoutError, ValueError, KeyError) as error:
            # A failed write is never repeated. Reopen to fetch the current revision.
            return self.async_abort(reason="conflict" if str(error) == "conflict" else "cannot_connect")
        services = sorted({f"{domain}.{service}" for domain, entries in available.items() for service in entries} | set(current["services"]))
        fields = {
            vol.Required("prompt", default=current["prompt"]): selector.TextSelector(selector.TextSelectorConfig(multiline=True)),
            vol.Required("homeAssistant", default=current["homeAssistant"]): bool,
            vol.Required("assist", default=current["assist"]): bool,
        }
        if frigate_available:
            fields[vol.Required("frigate", default=current["frigate"])] = bool
        fields[vol.Required("services", default=current["services"])] = selector.SelectSelector(
            selector.SelectSelectorConfig(options=services, multiple=True, custom_value=True, mode=selector.SelectSelectorMode.DROPDOWN)
        )
        return self.async_show_form(step_id="assistant", data_schema=vol.Schema(fields), errors=errors)
