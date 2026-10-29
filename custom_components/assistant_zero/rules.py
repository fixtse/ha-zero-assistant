"""HA-owned deadlines and sensor/forecast subscriptions with one-shot callbacks."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import logging
import math
from uuid import UUID

import aiohttp
from aiohttp import web
import voluptuous as vol

from homeassistant.components.http import HomeAssistantView
from homeassistant.components.weather.const import DATA_COMPONENT, WeatherEntityFeature
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED, EVENT_HOMEASSISTANT_STOP
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_point_in_utc_time, async_track_state_change_event
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .engine import RuleEngine, sensor_match
from .location import location_entities, location_match

DOMAIN = "assistant_rules"
_LOGGER = logging.getLogger(__name__)
CONFIG_SCHEMA = vol.Schema({DOMAIN: vol.Schema({vol.Required("callback_url"): cv.url, vol.Required("callback_token"): vol.All(str, vol.Length(min=24))})}, extra=vol.ALLOW_EXTRA)
SENSOR_CONDITION = vol.Schema({vol.Optional("source"): "sensor", vol.Required("entity_id"): vol.Match(r"^(sensor|binary_sensor)\.[a-z0-9_]+$"), vol.Required("operator"): vol.In(["equals", "above", "below"]), vol.Required("value"): vol.Any(str, int, float), vol.Required("hold_seconds"): vol.All(int, vol.Range(min=0, max=86400))})
WEATHER_CONDITIONS = ["clear-night", "cloudy", "fog", "hail", "lightning", "lightning-rainy", "partlycloudy", "pouring", "rainy", "snowy", "snowy-rainy", "sunny", "windy", "windy-variant", "exceptional"]
WEATHER_CONDITION = vol.Schema({vol.Optional("hourly_updates"): bool,vol.Optional("sleep_entity"): vol.Match(r"^input_boolean\.[a-z0-9_]+$"),vol.Optional("mode"): vol.In(["any_change", "conditions", "rain"]), vol.Optional("conditions"): vol.All([vol.In(WEATHER_CONDITIONS)], vol.Length(max=15)),vol.Required("source"): "weather", vol.Required("entity_id"): vol.Match(r"^weather\.[a-z0-9_]+$"), vol.Required("horizon_hours"): vol.All(int, vol.Range(min=1, max=168)), vol.Required("minimum_probability"): vol.All(vol.Coerce(float), vol.Range(min=0, max=100))})
def finite_location_number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise vol.Invalid('Expected a finite location number')
    return value


LOCATION_FIELDS = {vol.Required('entity_id'): vol.Match(r'^(person|device_tracker)\.[a-z0-9_]+$'), vol.Required('target_entity_id'): str, vol.Required('hold_seconds'): vol.All(int, vol.Range(min=0, max=86400)), vol.Required('reset_margin_m'): vol.All(finite_location_number, vol.Range(min=0, max=10000)), vol.Required('max_age_seconds'): vol.All(int, vol.Range(min=60, max=86400)), vol.Required('max_accuracy_m'): vol.All(finite_location_number, vol.Range(min=0.001, max=10000))}
ZONE_CONDITION = vol.Schema({**LOCATION_FIELDS, vol.Required('source'): 'zone', vol.Required('target_entity_id'): vol.Match(r'^zone\.[a-z0-9_]+$'), vol.Required('operator'): vol.In(['enter', 'leave'])})
DISTANCE_CONDITION = vol.Schema({**LOCATION_FIELDS, vol.Required('source'): 'distance', vol.Required('target_entity_id'): vol.Match(r'^(person|device_tracker)\.[a-z0-9_]+$'), vol.Required('operator'): vol.In(['above', 'below']), vol.Required('distance_m'): vol.All(finite_location_number, vol.Range(min=0.001, max=40075000))})
RULE_SCHEMA = vol.Schema({vol.Optional("last_fired", default=None): vol.Any(None, cv.datetime), vol.Required("id"): str, vol.Required("generation"): vol.All(int, vol.Range(min=0)), vol.Required("kind"): vol.In(["reminder", "timer", "alert"]), vol.Required("status"): vol.In(["active", "paused", "cancelled", "completed", "expired"]), vol.Required("next_at"): vol.Any(None, cv.datetime), vol.Required("repeat_seconds"): vol.Any(None, vol.All(int, vol.Range(min=60, max=31536000))), vol.Required("condition"): vol.Any(None, SENSOR_CONDITION, WEATHER_CONDITION, ZONE_CONDITION, DISTANCE_CONDITION), vol.Required("cooldown_seconds"): vol.All(int, vol.Range(min=0, max=31536000)), vol.Required("recurring"): bool})


class Manager:
    def __init__(self, hass, config):
        self.hass, self.config = hass, config
        self.store = Store(hass, 1, DOMAIN)
        self.listeners = {}
        self.ready = False
        self.closed = False
        self.tasks = set()
        def read_state(entity):
            state = hass.states.get(entity)
            return state.state if state else None
        self.engine = RuleEngine(lambda: dt_util.utcnow().timestamp(), self.persist, self.schedule, self.emit, read_state, lambda condition, latched: location_match(condition, hass.states.get, dt_util.utcnow().timestamp(), latched))

    async def persist(self, rules):
        await self.store.async_save({"rules": rules})

    def task(self, coro):
        if self.closed:
            coro.close()
            return
        task = self.hass.async_create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def dispatch(self, rule_id, operation):
        await operation
        rule = self.engine.rules.get(rule_id)
        if rule and rule["status"] != "active":
            remove = self.listeners.pop(rule_id, None)
            if remove:
                remove()

    def schedule(self, at, run):
        @callback
        def due(_now):
            async def execute():
                await run()
                for rule_id, remove in list(self.listeners.items()):
                    if self.engine.rules[rule_id]["status"] != "active":
                        remove()
                        self.listeners.pop(rule_id, None)
            self.task(execute())
        return async_track_point_in_utc_time(self.hass, due, datetime.fromtimestamp(at, timezone.utc))

    async def emit(self, event):
        try:
            async with async_get_clientsession(self.hass).post(self.config["callback_url"], json=event, headers={"Authorization": "Bearer " + self.config["callback_token"]}, timeout=aiohttp.ClientTimeout(total=10), allow_redirects=False) as response:
                if response.status == 202:
                    result = await response.json()
                    return "accepted" if result.get("accepted") is True else "ignored"
                _LOGGER.warning("Assistant rule callback rejected (HTTP %s); it will not be retried", response.status)
                return "rejected"
        except (aiohttp.ClientError, asyncio.TimeoutError):
            _LOGGER.warning("Assistant rule callback delivery uncertain; it will not be retried")
            return "uncertain"

    async def start(self, _event=None):
        saved = await self.store.async_load() or {}
        await self.engine.restore(saved.get("rules", {}))
        for rule in list(self.engine.rules.values()):
            try:
                await self.listen(rule)
            except Exception:
                rule["status"] = "paused"
                _LOGGER.warning("Could not restore assistant rule %s; paused without replay", rule["id"])
        await self.engine.save()
        self.ready = True

    async def listen(self, rule):
        rule_id, generation = rule["id"], rule["generation"]
        remove = self.listeners.pop(rule_id, None)
        if remove:
            remove()
        if rule["status"] != "active" or rule["kind"] != "alert":
            return
        condition = rule["condition"]
        if condition.get("source") == "weather":
            entity = self.hass.data[DATA_COMPONENT].get_entity(condition["entity_id"])
            if entity is None:
                raise ValueError("Configured weather entity does not exist")
            def subscribe(forecast_type):
                @callback
                def changed(forecast):
                    state = self.hass.states.get(condition['entity_id'])
                    attributes = state.attributes if state else {}
                    units = {key: attributes[key + '_unit'] for key in ('temperature', 'precipitation', 'wind_speed', 'pressure') if key + '_unit' in attributes}
                    self.task(self.dispatch(rule_id, self.engine.weather(rule_id, generation, forecast, units, forecast_type)))
                return entity.async_subscribe_forecast(forecast_type, changed)
            types = {"daily"}
            if condition.get("hourly_updates") and (entity.supported_features or 0) & WeatherEntityFeature.FORECAST_HOURLY:
                types.add("hourly")
            removers = []
            try:
                for kind in sorted(types):
                    removers.append(subscribe(kind))
            except Exception:
                for remove in removers:
                    remove()
                raise
            def unsubscribe():
                for remove in removers:
                    remove()
            self.listeners[rule_id] = unsubscribe
            # Each forecast type seeds its own baseline without replaying old changes.
            await entity.async_update_listeners(types)
        elif condition.get('source') in ('zone', 'distance'):
            @callback
            def changed(event):
                # Capture this event before queuing: rapid crossings must retain order.
                observed_at = dt_util.utcnow().timestamp()
                snapshot = {entity: event.data.get('new_state') if entity == event.data.get('entity_id') else self.hass.states.get(entity) for entity in location_entities(condition)}
                reading = lambda latched: location_match(condition, snapshot.get, observed_at, latched)
                self.task(self.dispatch(rule_id, self.engine.sensor(rule_id, generation, reading)))
            self.listeners[rule_id] = async_track_state_change_event(self.hass, location_entities(condition), changed)
            await self.dispatch(rule_id, self.engine.sensor(rule_id, generation, None))
        else:
            @callback
            def changed(event):
                state = event.data.get("new_state")
                self.task(self.dispatch(rule_id, self.engine.sensor(rule_id, generation, state.state if state else None)))
            self.listeners[rule_id] = async_track_state_change_event(self.hass, [condition["entity_id"]], changed)
            state = self.hass.states.get(condition["entity_id"])
            await self.dispatch(rule_id, self.engine.sensor(rule_id, generation, state.state if state else None))

    async def put(self, owner, value):
        if not self.ready:
            raise ValueError("Rule scheduler is starting")
        condition = value.get("condition")
        if value["kind"] == "alert":
            if not condition:
                raise ValueError("Alert condition is required")
            if condition.get("source") == "weather":
                mode = condition.get("mode", "rain")
                if (mode == "conditions") != bool(condition.get("conditions")):
                    raise ValueError("Conditions mode requires selected weather conditions")
                entity = self.hass.data[DATA_COMPONENT].get_entity(condition["entity_id"])
                if entity is None or not (entity.supported_features or 0) & WeatherEntityFeature.FORECAST_DAILY:
                    raise ValueError("Configured weather entity must support daily forecasts")
            elif condition.get('source') in ('zone', 'distance'):
                if condition['entity_id'] == condition['target_entity_id']:
                    raise ValueError('Choose two different people')
                if value['status'] == 'active' and any(self.hass.states.get(entity) is None for entity in location_entities(condition)):
                    raise ValueError('Configured person or zone does not exist')
            elif condition["operator"] != "equals" and (isinstance(condition["value"], bool) or not isinstance(condition["value"], (int, float))):
                raise ValueError("Numeric threshold requires a number")
        elif value["next_at"] is None:
            raise ValueError("Deadline is required")
        if isinstance(value["next_at"], datetime):
            if value["next_at"].tzinfo is None:
                raise ValueError("Deadline requires timezone")
            value["next_at"] = value["next_at"].isoformat()
        if isinstance(value.get("last_fired"), datetime):
            value["last_fired"] = value["last_fired"].isoformat()
        # The authenticated HA user owns the rule; the model never supplies credentials or URLs.
        result = await self.engine.put(owner, value)
        await self.listen(result)
        return self.engine.rules[result["id"]]

    async def stop(self, _event=None):
        self.closed = True
        self.engine.stop()
        for remove in self.listeners.values():
            remove()
        self.listeners.clear()
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)


class RulesView(HomeAssistantView):
    url = "/api/assistant_rules"
    extra_urls = ["/api/assistant_rules/{rule_id}"]
    name = "api:assistant_rules"
    requires_auth = True

    def __init__(self, manager):
        self.manager = manager

    async def get(self, request, rule_id=None):
        if not rule_id:
            return self.json({"protocol": 2, "ready": self.manager.ready, "hourly_updates": True, "location_alerts": True, "device_tracker_location_alerts": True})
        rule = self.manager.engine.rules.get(rule_id)
        if not rule or rule["owner"] != request["hass_user"].id:
            return self.json({"error": "Rule not found"}, status_code=404)
        return self.json(self.receipt(rule))

    async def put(self, request, rule_id=None):
        try:
            value = RULE_SCHEMA(await request.json())
            if str(UUID(value["id"])) != rule_id:
                raise ValueError("Invalid rule ID")
            result = await self.manager.put(request["hass_user"].id, value)
            return self.json(self.receipt(result))
        except PermissionError:
            return self.json({"error": "Rule not found"}, status_code=404)
        except (vol.Invalid, ValueError, TypeError):
            return self.json({"error": "Invalid rule or scheduler unavailable"}, status_code=400)

    def receipt(self, rule):
        return {**{key: deepcopy(rule.get(key)) for key in ("id", "generation", "status", "next_at", "last_fired", "last_event")}, "muted": self.manager.engine.muted(rule)}


async def async_setup_rules(hass, config):
    if DOMAIN in hass.data:
        return hass.data[DOMAIN]
    manager = Manager(hass, dict(config))
    hass.data[DOMAIN] = manager
    hass.http.register_view(RulesView(manager))
    if hass.is_running:
        await manager.start()
    else:
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, manager.start)
    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, manager.stop)
    return manager
