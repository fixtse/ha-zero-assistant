"""Import private bridge credentials and expose a dedicated conversation agent."""
import voluptuous as vol
from homeassistant.const import Platform
from homeassistant.helpers import config_validation as cv

DOMAIN = "assistant_zero"
CONFIG_SCHEMA = vol.Schema({
    DOMAIN: vol.Schema({
        vol.Required("url"): cv.url,
        vol.Required("token"): cv.string,
        vol.Required("satellite_id"): cv.entity_id,
        vol.Optional("settings_token"): cv.string,
    })
}, extra=vol.ALLOW_EXTRA)


async def async_setup(hass, config):
    if DOMAIN in config:
        hass.async_create_task(hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "import"}, data=config[DOMAIN]
        ))
    return True


async def async_setup_entry(hass, entry):
    await hass.config_entries.async_forward_entry_setups(entry, [Platform.CONVERSATION])
    return True


async def async_unload_entry(hass, entry):
    return await hass.config_entries.async_unload_platforms(entry, [Platform.CONVERSATION])
