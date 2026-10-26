"""Resolve private recovery destinations from the authenticated user's current person."""
import asyncio

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.components.mobile_app.const import DATA_CONFIG_ENTRIES, DATA_NOTIFY
from homeassistant.core import Context
from homeassistant.helpers import entity_registry as er

MAX_TARGETS = 100


def recovery_targets(hass, user):
    """Use registry identities and the live notify map, never infer services from names."""
    if not user.is_active or user.system_generated:
        raise ValueError("Home Assistant user is unavailable")
    trackers = {
        tracker
        for person in hass.states.async_all("person")
        if person.attributes.get("user_id") == user.id
        for tracker in person.attributes.get("device_trackers", [])
    }
    registry = er.async_get(hass)
    mobile = hass.data.get("mobile_app", {})
    registrations = mobile.get(DATA_CONFIG_ENTRIES, {})
    webhooks = set()
    for entity_id in trackers:
        entity = registry.async_get(entity_id)
        if entity is None or entity.platform != "mobile_app" or entity.disabled_by:
            continue
        entry = hass.config_entries.async_get_entry(entity.config_entry_id)
        if entry is None or entry.domain != "mobile_app":
            continue
        webhook = entry.data.get("webhook_id")
        active = registrations.get(webhook)
        if (active is not None and active.entry_id == entry.entry_id
                and entry.data.get("device_id") == entity.unique_id):
            webhooks.add(webhook)
    notify = mobile.get(DATA_NOTIFY)
    targets = sorted({
        name for name, webhook in getattr(notify, "registered_targets", {}).items()
        if webhook in webhooks and name.startswith("mobile_app_")
        and hass.services.has_service("notify", name)
    })
    if len(targets) > MAX_TARGETS:
        raise ValueError("Too many recovery destinations")
    return targets


async def send_recovery(hass, user, targets, code):
    """Check ownership again and attempt every destination once, without retries."""
    if not targets or targets != recovery_targets(hass, user):
        raise ValueError("Recovery destinations changed")
    results = await asyncio.gather(*(
        hass.services.async_call("notify", target, {
            "title": "Zero · Recuperar contraseña",
            "message": "Código: " + code + ". Caduca en 10 minutos. Si no lo has solicitado, ignora este mensaje.",
            "data": {"visibility": "secret"},
        }, blocking=True, context=Context(user_id=user.id))
        for target in targets
    ), return_exceptions=True)
    if any(isinstance(result, BaseException) for result in results):
        raise ValueError("Recovery delivery could not be confirmed; no retry was made")


def _authorized(connection, msg):
    user = connection.user
    if user is None or user.id != msg["user_id"] or not user.is_active or user.system_generated:
        connection.send_error(msg["id"], "unauthorized", "Use the linked Home Assistant user's credentials")
        return None
    return user


@websocket_api.websocket_command({
    vol.Required("type"): "assistant_zero/recovery/targets",
    vol.Required("user_id"): str,
})
@websocket_api.async_response
async def _targets(hass, connection, msg):
    if (user := _authorized(connection, msg)) is None:
        return
    try:
        connection.send_result(msg["id"], recovery_targets(hass, user))
    except ValueError:
        connection.send_error(msg["id"], "recovery_unavailable", "Recovery destinations are unavailable")


@websocket_api.websocket_command({
    vol.Required("type"): "assistant_zero/recovery/send",
    vol.Required("user_id"): str,
    vol.Required("targets"): vol.All([vol.Match(r"^mobile_app_[a-z0-9_]+$")], vol.Length(min=1, max=MAX_TARGETS)),
    vol.Required("code"): vol.Match(r"^[0-9]{8}$"),
})
@websocket_api.async_response
async def _send(hass, connection, msg):
    if (user := _authorized(connection, msg)) is None:
        return
    try:
        await send_recovery(hass, user, msg["targets"], msg["code"])
        connection.send_result(msg["id"])
    except ValueError:
        connection.send_error(msg["id"], "recovery_unavailable", "Recovery delivery was not confirmed; no retry was made")


def async_setup_recovery(hass):
    websocket_api.async_register_command(hass, _targets)
    websocket_api.async_register_command(hass, _send)
