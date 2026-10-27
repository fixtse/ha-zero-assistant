"""Import private bridge credentials and expose a dedicated conversation agent."""
import voluptuous as vol
from homeassistant.const import Platform
from homeassistant.helpers import config_validation as cv

DOMAIN = "assistant_zero"
CONFIG_SCHEMA = vol.Schema({
    DOMAIN: vol.Schema({
        vol.Required("url"): cv.url,
        vol.Required("token"): cv.string,
        vol.Optional("satellite_id"): cv.entity_id,
        vol.Optional("settings_token"): cv.string,
        vol.Optional("callback_url"): cv.url,
        vol.Optional("callback_token"): vol.All(str, vol.Length(min=24)),
    })
}, extra=vol.ALLOW_EXTRA)


async def async_setup(hass, config):
    from .recovery import async_setup_recovery
    async_setup_recovery(hass)
    if DOMAIN in config:
        hass.async_create_task(hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "import"}, data=config[DOMAIN]
        ))
    return True


async def async_setup_entry(hass, entry):
    from .rules import async_setup_rules
    if entry.data.get("callback_url") and entry.data.get("callback_token"):
        await async_setup_rules(hass, entry.data)
    await hass.config_entries.async_forward_entry_setups(entry, [Platform.CONVERSATION])
    return True


async def async_unload_entry(hass, entry):
    # Rule listeners belong to this integration; stop before unloading conversation.
    manager = hass.data.pop("assistant_rules", None)
    if manager is not None:
        await manager.stop()
    return await hass.config_entries.async_unload_platforms(entry, [Platform.CONVERSATION])
