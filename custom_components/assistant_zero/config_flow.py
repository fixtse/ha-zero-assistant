"""Optional HA client for Zero; prompt and access policy remain owned by Zero."""
import aiohttp
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv, selector
from . import DOMAIN
from .client import BridgeError
from .settings import settings_request


def connection_schema():
    return vol.Schema({
        vol.Required("url"): cv.url,
        vol.Required("token"): selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)),
        vol.Required("satellite_id"): selector.EntitySelector(selector.EntitySelectorConfig(domain="assist_satellite")),
        vol.Required("settings_token"): selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)),
    })


class ZeroConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_import(self, data):
        await self.async_set_unique_id(DOMAIN)
        # Do not replace UI-managed connection settings on every YAML import.
        existing = next((entry for entry in self._async_current_entries() if entry.unique_id == DOMAIN), None)
        if existing and not existing.data.get("settings_token") and data.get("settings_token"):
            self.hass.config_entries.async_update_entry(existing, data={**existing.data, "settings_token": data["settings_token"]})
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
        return self.async_show_form(step_id="user", data_schema=connection_schema(), errors=errors)

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
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)
