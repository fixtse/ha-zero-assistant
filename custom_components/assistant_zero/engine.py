"""Persistent, event-driven rules. No polling, model calls, or delivery retries."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import json
import math
from uuid import uuid4

FORECAST_FIELDS = ('condition', 'temperature', 'templow', 'apparent_temperature', 'dew_point', 'precipitation', 'precipitation_probability', 'wind_speed', 'wind_gust_speed', 'wind_bearing', 'humidity', 'pressure', 'cloud_coverage', 'uv_index')


def weather_change(forecast, previous, condition, now, units):
    """Only bounded, typed weather facts cross the callback boundary."""
    def periods(values):
        result = {}
        for period in forecast_window(values, condition, now):
            fields = {}
            for key in FORECAST_FIELDS:
                if key not in period:
                    continue
                value = period.get(key)
                if value is None or (isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)) or (isinstance(value, str) and len(value) <= 40):
                    fields[key] = value
            result[iso(timestamp(period['datetime']))] = fields
        return result
    before, after = periods(previous), periods(forecast)
    changes = []
    for at in sorted(before.keys() | after.keys()):
        old, new = before.get(at), after.get(at)
        if old == new:
            continue
        keys = [key for key in FORECAST_FIELDS if (key in (old or {}) or key in (new or {})) and (old is None or new is None or old.get(key) != new.get(key))]
        if keys:
            changes.append({'datetime': at, 'before': {key: old.get(key) for key in keys} if old is not None else None, 'after': {key: new.get(key) for key in keys} if new is not None else None})
    return {'entity_id': condition['entity_id'], 'units': {key: value for key, value in (units or {}).items() if key in ('temperature', 'precipitation', 'wind_speed', 'pressure') and isinstance(value, str) and len(value) <= 16}, 'changes': changes[:48 if condition.get('_forecast_type') == 'hourly' else 12], 'truncated': len(changes) > (48 if condition.get('_forecast_type') == 'hourly' else 12), **({'forecast_type': 'hourly'} if condition.get('_forecast_type') == 'hourly' else {})}


def timestamp(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def iso(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


def sensor_match(condition, state):
    if state is None or str(state).strip().lower() in ("", "unknown", "unavailable"):
        return None
    if condition["operator"] == "equals":
        return str(state) == str(condition["value"])
    try:
        number = float(state)
    except (ValueError, TypeError):
        return None
    if not math.isfinite(number):
        return None
    return number > condition["value"] if condition["operator"] == "above" else number < condition["value"]


def forecast_rain(forecast, condition, now):
    for period in forecast:
        try:
            at = timestamp(period["datetime"])
        except (ValueError, KeyError, TypeError):
            continue
        if not now - 86400 <= at <= now + condition["horizon_hours"] * 3600:
            continue
        probability = period.get("precipitation_probability")
        wet = period.get("condition") in ("rainy", "pouring", "lightning-rainy", "snowy-rainy")
        try:
            wet = wet or float(period.get("precipitation") or 0) > 0
            chance = float(probability or 0)
        except (ValueError, TypeError):
            continue
        if (wet and (probability is None or chance >= condition["minimum_probability"])) or (chance > 0 and chance >= condition["minimum_probability"]):
            return True
    return False


def forecast_window(forecast, condition, now):
    """Filter both snapshots at the same instant; time alone must not create changes."""
    periods = []
    for period in forecast:
        try:
            at = timestamp(period["datetime"])
        except (KeyError, TypeError, ValueError):
            continue
        if now - (3600 if condition.get("_forecast_type") == "hourly" else 86400) <= at <= now + condition["horizon_hours"] * 3600:
            periods.append(period)
    return sorted(periods, key=lambda period: period["datetime"])


def forecast_matches(forecast, previous, condition, now):
    # Persisted rain-only rules keep their meaning; new general alerts select any_change.
    mode = condition.get("mode", "rain")
    if mode == "rain":
        return forecast_rain(forecast, condition, now)
    current = forecast_window(forecast, condition, now)
    before = forecast_window(previous, condition, now)
    if current == before:
        return False
    if mode == "any_change":
        return True
    return any(period.get("condition") in condition.get("conditions", []) for period in current)


class RuleEngine:
    def __init__(self, now, persist, schedule, emit, read_state=None):
        self.now, self.persist, self.schedule, self.emit = now, persist, schedule, emit
        self.read_state = read_state
        self.rules = {}
        self.timers = {}
        self.lock = asyncio.Lock()

    def muted(self, rule):
        condition = rule.get("condition") or {}
        entity = condition.get("sleep_entity") if condition.get("source") == "weather" else None
        return bool(entity) and (self.read_state is None or self.read_state(entity) != "off")

    async def save(self):
        await self.persist(deepcopy(self.rules))

    def arm(self, rule):
        cancel = self.timers.pop(rule["id"], None)
        if cancel:
            cancel()
        if rule["status"] != "active":
            return
        at = rule.get("hold_until") if rule["kind"] == "alert" else rule.get("next_at")
        if at:
            rule_id, generation = rule["id"], rule["generation"]
            self.timers[rule_id] = self.schedule(timestamp(at), lambda: self.deadline(rule_id, generation))

    async def restore(self, rules):
        async with self.lock:
            self.rules = deepcopy(rules)
            for rule in self.rules.values():
                if rule.get("last_event", {}).get("outcome") == "pending":
                    rule["last_event"]["outcome"] = "uncertain"
                # A restart breaks proof of a continuous sensor hold. Do not replay it.
                rule["hold_until"] = None
                if rule["status"] == "active" and rule["kind"] != "alert" and timestamp(rule["next_at"]) <= self.now():
                    self.advance(rule, expired=True)
                self.arm(rule)
            await self.save()

    def advance(self, rule, expired=False):
        interval = rule.get("repeat_seconds")
        if interval:
            due = timestamp(rule["next_at"])
            rule["next_at"] = iso(due + (max(0, math.floor((self.now() - due) / interval)) + 1) * interval)
        else:
            rule["status"] = "expired" if expired else "completed"

    async def put(self, owner, value):
        async with self.lock:
            old = self.rules.get(value["id"])
            if old and old["owner"] != owner:
                raise PermissionError("Rule belongs to another Home Assistant user")
            if old and value["generation"] <= old["generation"]:
                if value["generation"] == old["generation"]:
                    return deepcopy(old)
                raise ValueError("Stale rule generation")
            rule = {**deepcopy(value), "owner": owner, "hold_until": None, "latched": False, "last_fired": value.get("last_fired"), "forecast_signature": None, "last_hourly_fired": old.get("last_hourly_fired") if old else None}
            self.rules[rule["id"]] = rule
            if rule["status"] == "active" and rule["kind"] != "alert" and timestamp(rule["next_at"]) <= self.now():
                self.advance(rule, expired=True)
            await self.save()
            self.arm(rule)
            return deepcopy(rule)

    def current(self, rule_id, generation):
        rule = self.rules.get(rule_id)
        return rule if rule and rule["generation"] == generation and rule["status"] == "active" else None

    def fire(self, rule):
        rule["last_fired"] = iso(self.now())
        event = {"id": rule["id"], "generation": rule["generation"], "event_id": str(uuid4()), "fired_at": rule["last_fired"]}
        if rule["kind"] == "alert":
            rule["latched"] = True
            rule["hold_until"] = None
            if not rule["recurring"]:
                rule["status"] = "completed"
        else:
            self.advance(rule)
            event["next_at"] = rule["next_at"] if rule["status"] == "active" else None
        rule["last_event"] = {**event, "outcome": "pending"}
        return event

    async def deliver(self, event):
        if not event:
            return
        # Recheck sleep at the last local boundary before the single HTTP attempt.
        async with self.lock:
            rule = self.rules.get(event["id"])
            if not rule or rule["generation"] != event["generation"] or self.muted(rule):
                if rule and rule.get("last_event", {}).get("event_id") == event["event_id"]:
                    rule["last_event"]["outcome"] = "suppressed"
                    await self.save()
                return
        # State was persisted before this single HTTP attempt. Never replay on recovery.
        try:
            outcome = await self.emit(event)
        except Exception:
            outcome = "uncertain"
        async with self.lock:
            rule = self.rules.get(event["id"])
            if rule and rule.get("last_event", {}).get("event_id") == event["event_id"]:
                rule["last_event"]["outcome"] = outcome
                await self.save()

    async def deadline(self, rule_id, generation):
        event = None
        async with self.lock:
            rule = self.current(rule_id, generation)
            if not rule:
                return
            at = rule.get("hold_until") if rule["kind"] == "alert" else rule.get("next_at")
            if not at or timestamp(at) > self.now():
                return
            if self.now() - timestamp(at) > 60:
                if rule["kind"] == "alert":
                    rule["hold_until"] = None
                else:
                    self.advance(rule, expired=True)
            elif rule["kind"] == "alert" and self.read_state is not None and sensor_match(rule["condition"], self.read_state(rule["condition"]["entity_id"])) is not True:
                rule["hold_until"] = None
            else:
                event = self.fire(rule)
            await self.save()
            self.arm(rule)
        await self.deliver(event)

    async def sensor(self, rule_id, generation, state):
        event = None
        async with self.lock:
            rule = self.current(rule_id, generation)
            if not rule:
                return
            before = deepcopy(rule)
            matches = sensor_match(rule["condition"], state)
            if matches is not True:
                rule["hold_until"] = None
                if matches is False:
                    rule["latched"] = False
            elif not rule["latched"] and not rule.get("hold_until"):
                cooldown_at = timestamp(rule["last_fired"]) + rule["cooldown_seconds"] if rule.get("last_fired") else self.now()
                at = max(self.now() + rule["condition"]["hold_seconds"], cooldown_at)
                if at > self.now():
                    rule["hold_until"] = iso(at)
                else:
                    event = self.fire(rule)
            if before != rule:
                await self.save()
                self.arm(rule)
        await self.deliver(event)

    async def weather(self, rule_id, generation, forecast, units=None, forecast_type="daily"):
        if not forecast:
            return
        event = None
        async with self.lock:
            rule = self.current(rule_id, generation)
            if not rule:
                return
            signature = json.dumps(forecast, sort_keys=True, separators=(",", ":"))
            if forecast_type == "hourly" and not rule["condition"].get("hourly_updates"):
                return
            signature_key = "hourly_forecast_signature" if forecast_type == "hourly" else "forecast_signature"
            fired_key = "last_hourly_fired" if forecast_type == "hourly" else "last_fired"
            condition = {**rule["condition"], "_forecast_type": forecast_type}
            previous = rule.get(signature_key)
            if previous == signature:
                return
            rule[signature_key] = signature
            cooled = not rule.get(fired_key) or self.now() - timestamp(rule[fired_key]) >= rule["cooldown_seconds"]
            if previous is not None and cooled and not self.muted(rule) and forecast_matches(forecast, json.loads(previous), condition, self.now()):
                evidence = weather_change(forecast, json.loads(previous), condition, self.now(), units)
                if evidence['changes']:
                    daily_fired = rule.get("last_fired")
                    event = self.fire(rule)
                    if forecast_type == "hourly":
                        rule["last_hourly_fired"] = event["fired_at"]
                        rule["last_fired"] = daily_fired
                    event['weather'] = evidence
                    rule['last_event']['weather'] = deepcopy(evidence)
            await self.save()
        await self.deliver(event)

    def stop(self):
        for cancel in self.timers.values():
            cancel()
        self.timers.clear()
