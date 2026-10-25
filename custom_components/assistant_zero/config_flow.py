"""Optional HA client for Zero; prompt and access policy remain owned by Zero."""
import aiohttp
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
            try:
                await settings_request(self.hass, user_input)
            except (BridgeError, aiohttp.ClientError, TimeoutError, ValueError, KeyError):
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(DOMAIN)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title="Zero", data=user_input)
        return self.async_show_form(step_id="user", data_schema=connection_schema(getattr(self, "_discovered_url", None)), errors=errors)

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
        try:
            if user_input is not None:
                value = {**user_input, "revision": self._revision}
                value["services"] = [line.strip() for line in value["services"].splitlines() if line.strip()]
                await settings_request(self.hass, self.config_entry.data, "PUT", value)
                # No prompt or policy snapshot is stored in config entry options.
                return self.async_create_entry(title="", data={})
            current = await settings_request(self.hass, self.config_entry.data)
            self._revision = current["revision"]
        except (BridgeError, aiohttp.ClientError, TimeoutError, ValueError, KeyError) as error:
            # A failed write is never repeated. Reopen to fetch the current revision.
            return self.async_abort(reason="conflict" if str(error) == "conflict" else "cannot_connect")
        schema = vol.Schema({
            vol.Required("prompt", default=current["prompt"]): selector.TextSelector(selector.TextSelectorConfig(multiline=True)),
            vol.Required("homeAssistant", default=current["homeAssistant"]): bool,
            vol.Required("assist", default=current["assist"]): bool,
            vol.Required("frigate", default=current["frigate"]): bool,
            vol.Required("services", default="\n".join(current["services"])): selector.TextSelector(selector.TextSelectorConfig(multiline=True)),
        })
        return self.async_show_form(step_id="assistant", data_schema=schema, errors=errors)
