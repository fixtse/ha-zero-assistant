"""Location math, GPS validation and event-driven alert lifecycle."""
import importlib.util
from pathlib import Path
from datetime import datetime, timezone
from types import SimpleNamespace
import unittest

root = Path(__file__).parents[1] / 'custom_components/assistant_zero'
def load(name):
    spec = importlib.util.spec_from_file_location(name, root / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
location, engine = load('location'), load('engine')

class LocationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.now = 1792152000.0
        self.states, self.events, self.pending = {}, [], {}
        self.saved = {}
        async def save(rules):
            self.saved = rules
        async def emit(event):
            self.events.append(event)
            return 'accepted'
        def schedule(at, run):
            key = object()
            self.pending[key] = at, run
            return lambda: self.pending.pop(key, None)
        self.engine = engine.RuleEngine(lambda: self.now, save, schedule, emit,
            lambda entity: self.states[entity].state if entity in self.states else None,
            lambda condition, latched: location.location_match(condition, self.states.get, self.now, latched))
        self.condition = dict(source='distance', entity_id='person.a', target_entity_id='person.b', operator='below', distance_m=3200, reset_margin_m=100, hold_seconds=0, max_age_seconds=3600, max_accuracy_m=200)
        self.set_state('person.a', 0, 0)
        self.set_state('person.b', 0, .04)

    def set_state(self, entity, lat=0, lon=0, **extra):
        self.states[entity] = SimpleNamespace(state='not_home', last_reported=datetime.fromtimestamp(self.now, timezone.utc), attributes=dict(latitude=lat, longitude=lon, **extra))

    async def put(self, **extra):
        await self.engine.put('owner', dict(id='r', generation=0, kind='alert', status='active', next_at=None, repeat_seconds=None, condition=self.condition, cooldown_seconds=60, recurring=True, **extra))
        await self.engine.sensor('r', 0, None)

    async def feed(self, lon):
        self.set_state('person.b', 0, lon)
        await self.engine.sensor('r', 0, None)

    async def advance(self, seconds):
        self.now += seconds
        for key, (at, run) in list(self.pending.items()):
            if key in self.pending and at <= self.now:
                self.pending.pop(key)
                await run()

    async def test_crossing_only_and_hysteresis(self):
        await self.put()
        self.assertEqual(self.events, [])
        await self.feed(.02)
        self.assertEqual(len(self.events), 1)
        await self.advance(60)
        await self.feed(.029)
        await self.feed(.02)
        self.assertEqual(len(self.events), 1)
        await self.feed(.04)
        await self.feed(.02)
        self.assertEqual(len(self.events), 2)

    async def test_creation_inside_and_restart_do_not_replay(self):
        self.set_state('person.b', 0, .02)
        await self.put()
        await self.feed(.021)
        self.assertEqual(self.events, [])
        await self.feed(.04)
        await self.engine.restore(self.saved)
        await self.feed(.02)
        self.assertEqual(self.events, [])
        await self.feed(.04)
        await self.feed(.02)
        self.assertEqual(len(self.events), 1)

    async def test_hold_rechecks_stale_gps_at_deadline(self):
        self.condition.update(hold_seconds=90, max_age_seconds=60)
        await self.put()
        await self.feed(.02)
        self.assertTrue(self.pending)
        await self.advance(90)
        self.assertEqual(self.events, [])
        self.assertFalse(self.pending)

    async def test_invalid_gps_never_rearms(self):
        await self.put()
        await self.feed(.02)
        await self.advance(60)
        for attributes in [dict(latitude=float('nan'), longitude=0), dict(latitude=91, longitude=0), dict(latitude=0, longitude=.04, gps_accuracy=201)]:
            self.states['person.b'].attributes = attributes
            await self.engine.sensor('r', 0, None)
            await self.feed(.02)
        self.assertEqual(len(self.events), 1)

    async def test_zone_enter_leave_and_zone_move(self):
        self.condition.update(source='zone', target_entity_id='zone.office', operator='enter', reset_margin_m=0)
        self.set_state('zone.office', 0, .04, radius=100)
        await self.put()
        self.set_state('person.a', 0, .04)
        await self.engine.sensor('r', 0, None)
        self.assertEqual(len(self.events), 1)
        self.condition['operator'] = 'leave'
        self.assertFalse(location.location_match(self.condition, self.states.get, self.now))
        self.set_state('zone.office', 0, 0, radius=100)
        self.assertTrue(location.location_match(self.condition, self.states.get, self.now))

    async def test_above_threshold_and_hold_cancellation_on_pause(self):
        self.condition.update(operator='above', hold_seconds=20)
        self.set_state('person.b', 0, .02)
        await self.put()
        await self.feed(.04)
        self.assertTrue(self.pending)
        paused = dict(self.saved['r'], generation=1, status='paused')
        await self.engine.put('owner', paused)
        await self.advance(20)
        self.assertEqual(self.events, [])
        self.assertFalse(self.pending)

    async def test_queued_crossings_use_event_snapshots(self):
        await self.put()
        await self.engine.sensor('r', 0, lambda latched: True)
        await self.engine.sensor('r', 0, lambda latched: False)
        self.assertEqual(len(self.events), 1)
        self.assertFalse(self.saved['r']['latched'])

    def test_metres_and_antimeridian(self):
        self.assertAlmostEqual(location.distance_m((0, 0), (0, 1)), 111195, delta=1)
        self.assertAlmostEqual(location.distance_m((0, 179.99), (0, -179.99)), 2223.9, delta=1)

    async def test_gps_tracker_and_person_mixed_distance_and_zone(self):
        for first, second in [('device_tracker.a', 'person.b'), ('person.a', 'device_tracker.b'), ('device_tracker.a', 'device_tracker.b')]:
            condition = {**self.condition, 'entity_id': first, 'target_entity_id': second, 'distance_m': 300}
            self.set_state(first, 0, 0, gps_accuracy=5)
            self.set_state(second, 0, .001, gps_accuracy=5)
            self.assertTrue(location.location_match(condition, self.states.get, self.now))
            self.states[second].last_reported = datetime.fromtimestamp(self.now - 4000, timezone.utc)
            self.assertIsNone(location.location_match(condition, self.states.get, self.now))
            self.set_state(second, 0, .001, gps_accuracy=500)
            self.assertIsNone(location.location_match(condition, self.states.get, self.now))
            self.set_state(second, 0, .001, gps_accuracy=5)
            self.states[second].attributes.pop('latitude')
            self.assertIsNone(location.location_match(condition, self.states.get, self.now))
        self.set_state('device_tracker.a', 0, 0)
        self.set_state('zone.home', 0, 0, radius=100)
        zone = {**self.condition, 'source': 'zone', 'entity_id': 'device_tracker.a', 'target_entity_id': 'zone.home', 'operator': 'enter'}
        self.assertTrue(location.location_match(zone, self.states.get, self.now))
