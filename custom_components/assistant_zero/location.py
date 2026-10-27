"""Pure, metre-based location comparisons; no polling or external services."""
from __future__ import annotations

import math
from datetime import datetime


def location_entities(condition):
    return [condition['entity_id'], condition['target_entity_id']]


def coordinates(state, now, condition, person=True):
    if state is None or state.state in ('unknown', 'unavailable', ''):
        return None
    attributes = state.attributes
    lat, lon = attributes.get('latitude'), attributes.get('longitude')
    def number(value):
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    if not number(lat) or not number(lon) or abs(lat) > 90 or abs(lon) > 180:
        return None
    if person:
        reported = getattr(state, 'last_reported', None) or getattr(state, 'last_updated', None)
        if not isinstance(reported, datetime):
            return None
        age = now - reported.timestamp()
        if age < -30 or age > condition['max_age_seconds']:
            return None
        accuracy = attributes.get('gps_accuracy')
        if accuracy is not None and (not number(accuracy) or accuracy < 0 or accuracy > condition['max_accuracy_m']):
            return None
    return lat, lon


def distance_m(first, second):
    lat1, lon1, lat2, lon2 = map(math.radians, (*first, *second))
    value = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6371008.8 * 2 * math.asin(math.sqrt(min(1, max(0, value))))


def location_match(condition, read_state, now, latched=False):
    """None breaks a hold but never rearms. Hysteresis applies only while latched."""
    first = coordinates(read_state(condition['entity_id']), now, condition)
    target = read_state(condition['target_entity_id'])
    second = coordinates(target, now, condition, condition['source'] == 'distance')
    if first is None or second is None:
        return None
    distance = distance_m(first, second)
    margin = condition['reset_margin_m'] if latched else 0
    if condition['source'] == 'zone':
        radius = target.attributes.get('radius')
        if isinstance(radius, bool) or not isinstance(radius, (int, float)) or not math.isfinite(radius) or radius < 0:
            return None
        if condition['operator'] == 'enter':
            return distance <= radius + margin
        return distance > max(0, radius - margin)
    threshold = condition['distance_m']
    return distance < threshold + margin if condition['operator'] == 'below' else distance > max(0, threshold - margin)
