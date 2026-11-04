"""Discover and control entities using Assist's effective exposure policy."""
import re

import voluptuous as vol
from homeassistant.auth.permissions.const import POLICY_CONTROL, POLICY_READ
from homeassistant.components import websocket_api
from homeassistant.components.homeassistant.exposed_entities import async_should_expose
from homeassistant.core import Context
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.service import async_get_all_descriptions

ENTITY = re.compile(r"^[a-z_]+\.[a-z0-9_]+$")
SERVICE = re.compile(r"^[a-z_]+\.[a-z0-9_]+$")
BROAD_TARGETS = {"area_id", "device_id", "floor_id", "label_id", "target"}


def permitted(hass, user, entity_id, policy=POLICY_READ):
    return (user is not None and user.is_active and not user.system_generated
            and hass.states.get(entity_id) is not None
            and async_should_expose(hass, "conversation", entity_id)
            and user.permissions.check_entity(entity_id, policy))


def entity_ids(data):
    raw = data.get("entity_id")
    if isinstance(raw, str):
        raw = [item.strip() for item in raw.split(",")]
    if (not isinstance(raw, list) or not raw or len(raw) > 100
            or any(not isinstance(item, str) or not ENTITY.fullmatch(item) for item in raw)):
        raise ValueError("Use explicit entity IDs exposed to Assist")
    return list(dict.fromkeys(raw))


def check_references(hass, user, value):
    """Do not let secondary entity fields or broad targets bypass exposure checks."""
    if isinstance(value, dict):
        if BROAD_TARGETS.intersection(value):
            raise ValueError("Use explicit entity IDs, not area/device/floor/label targets")
        for item in value.values():
            check_references(hass, user, item)
    elif isinstance(value, list):
        for item in value:
            check_references(hass, user, item)
    elif isinstance(value, str):
        for item in value.split(","):
            if ENTITY.fullmatch(item.strip()) and not permitted(hass, user, item.strip(), POLICY_CONTROL):
                raise ValueError("Entity is not exposed to Assist or is not permitted")


def service_targets(hass, user, service, description, data):
    domain, name = service.split(".", 1)
    # A named script is itself an exposed capability. Its inputs belong to that script.
    script = service if domain == "script" and name not in {"turn_on", "turn_off", "toggle", "reload"} else None
    if script:
        if not permitted(hass, user, script, POLICY_CONTROL):
            raise ValueError("Script is not exposed to Assist or is not permitted")
        return data
    if "target" not in description or (domain == "homeassistant" and name not in {"turn_on", "turn_off", "toggle"}):
        raise ValueError("Service requires an exposed entity target")
    ids = entity_ids(data)
    for entity_id in ids:
        if not permitted(hass, user, entity_id, POLICY_CONTROL):
            raise ValueError("Entity is not exposed to Assist or is not permitted")
        if domain != "homeassistant" and entity_id.split(".", 1)[0] != domain:
            raise ValueError("Service domain must match its exposed entity targets")
    check_references(hass, user, data)
    return {**data, "entity_id": ids}


async def exposed_services(hass, user):
    descriptions = await async_get_all_descriptions(hass)
    domains = {state.domain for state in hass.states.async_all()
               if permitted(hass, user, state.entity_id, POLICY_CONTROL)}
    items = []
    for domain, services in descriptions.items():
        if domain not in domains and domain != "homeassistant":
            continue
        for name, description in services.items():
            service = f"{domain}.{name}"
            if domain == "script" and name not in {"turn_on", "turn_off", "toggle", "reload"}:
                if permitted(hass, user, service, POLICY_CONTROL):
                    items.append({"service": service, "description": description})
            elif "target" in description and domains and (domain != "homeassistant" or name in {"turn_on", "turn_off", "toggle"}):
                items.append({"service": service, "description": description})
    return sorted(items, key=lambda item: item["service"])


@websocket_api.websocket_command({
    vol.Required("type"): "assistant_zero/assist/entities",
    vol.Optional("search", default=""): vol.All(str, vol.Length(max=200)),
    vol.Optional("offset", default=0): vol.All(int, vol.Range(min=0)),
    vol.Optional("limit", default=20): vol.All(int, vol.Range(min=1, max=50)),
})
@websocket_api.async_response
async def _entities(hass, connection, msg):
    search = msg["search"].lower()
    items = [{"entity_id": state.entity_id,
              "name": str(state.attributes.get("friendly_name", state.entity_id))[:200]}
             for state in hass.states.async_all()
             if permitted(hass, connection.user, state.entity_id)
             and search in (state.entity_id + " " + str(state.attributes.get("friendly_name", ""))).lower()]
    items.sort(key=lambda item: item["entity_id"])
    offset, limit = msg["offset"], msg["limit"]
    connection.send_result(msg["id"], {"items": items[offset:offset + limit], "total": len(items),
                                      "next_offset": offset + limit if offset + limit < len(items) else None})


@websocket_api.websocket_command({
    vol.Required("type"): "assistant_zero/assist/entity",
    vol.Required("entity_id"): vol.Match(ENTITY),
})
@websocket_api.async_response
async def _entity(hass, connection, msg):
    entity_id = msg["entity_id"]
    if not permitted(hass, connection.user, entity_id):
        connection.send_error(msg["id"], "not_exposed", "Entity is not exposed to Assist or is not permitted")
        return
    state = hass.states.get(entity_id)
    connection.send_result(msg["id"], {"entity_id": entity_id, "state": state.state,
                                      "attributes": dict(state.attributes), "last_updated": state.last_updated.isoformat()})


@websocket_api.websocket_command({
    vol.Required("type"): "assistant_zero/assist/services",
    vol.Optional("search", default=""): vol.All(str, vol.Length(max=200)),
    vol.Optional("offset", default=0): vol.All(int, vol.Range(min=0)),
    vol.Optional("limit", default=10): vol.All(int, vol.Range(min=1, max=20)),
})
@websocket_api.async_response
async def _services(hass, connection, msg):
    items = [item for item in await exposed_services(hass, connection.user) if msg["search"].lower() in item["service"]]
    offset, limit = msg["offset"], msg["limit"]
    connection.send_result(msg["id"], {"items": items[offset:offset + limit], "total": len(items),
                                      "next_offset": offset + limit if offset + limit < len(items) else None})


@websocket_api.websocket_command({
    vol.Required("type"): "assistant_zero/assist/call",
    vol.Required("service"): vol.Match(SERVICE),
    vol.Required("data"): dict,
})
@websocket_api.async_response
async def _call(hass, connection, msg):
    try:
        domain, name = msg["service"].split(".", 1)
        descriptions = await async_get_all_descriptions(hass)
        description = descriptions.get(domain, {}).get(name)
        if description is None or not hass.services.has_service(domain, name):
            raise ValueError("Service is unavailable")
        # No awaits between the final exposure check and dispatch, and no retry on failure.
        data = service_targets(hass, connection.user, msg["service"], description, msg["data"])
        await hass.services.async_call(domain, name, data, blocking=True,
                                       context=Context(user_id=connection.user.id),
                                       return_response=bool(description.get("response")))
        connection.send_result(msg["id"], {"accepted": True, "service": msg["service"]})
    except (ValueError, vol.Invalid, HomeAssistantError):
        connection.send_error(msg["id"], "assist_action_failed", "Action rejected or completion uncertain; inspect before retrying")


def async_setup_exposure(hass):
    for command in (_entities, _entity, _services, _call):
        websocket_api.async_register_command(hass, command)
