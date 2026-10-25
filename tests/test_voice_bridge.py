"""Protocol verification without requiring Home Assistant on the test host."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, patch

spec = importlib.util.spec_from_file_location("zero_client", Path(__file__).parents[1] / "custom_components/assistant_zero/client.py")
client = importlib.util.module_from_spec(spec)
spec.loader.exec_module(client)
JOB = "00000000-0000-4000-8000-000000000001"


class VoiceBridgeTests(unittest.IsolatedAsyncioTestCase):
    async def test_selected_assistant_is_sent_once(self):
        request = AsyncMock(side_effect=[{"jobId": JOB}, {"status": "done", "output": "Salem reply"}])
        self.assertEqual(await client.converse(request, "Hi", "assist_satellite.test", assistant_id="salem"), "Salem reply")
        self.assertEqual(request.call_args_list[0].args[2]["assistant_id"], "salem")
        self.assertEqual([call.args[0] for call in request.call_args_list], ["POST", "GET"])

    async def test_submit_once_then_poll(self):
        request = AsyncMock(side_effect=[{"jobId": JOB}, {"status": "pending"}, {"status": "running"}, {"status": "done", "output": "Hello"}])
        with patch.object(client.asyncio, "sleep", new=AsyncMock()):
            self.assertEqual(await client.converse(request, "Hi", "assist_satellite.test"), "Hello")
        self.assertEqual([c.args[0] for c in request.call_args_list], ["POST", "GET", "GET", "GET"])
        self.assertEqual(request.call_args_list[0].args[2]["satellite_id"], "assist_satellite.test")

    async def test_user_identity_is_preserved_for_submission_and_result(self):
        user_id = "a" * 32
        request = AsyncMock(side_effect=[{"jobId": JOB}, {"status": "done", "output": "Personal reply"}])
        self.assertEqual(await client.converse(request, "Hi", "assist_satellite.test", ha_user_id=user_id), "Personal reply")
        self.assertEqual(request.call_args_list[0].args[2]["ha_user_id"], user_id)
        self.assertEqual(request.call_args_list[1].args[1], "/v1/voice/turns/" + JOB + "?ha_user_id=" + user_id)

    async def test_uncertain_submission_is_not_retried(self):
        request = AsyncMock(side_effect=TimeoutError())
        with self.assertRaises(TimeoutError):
            await client.converse(request, "Action", "assist_satellite.test")
        self.assertEqual(request.await_count, 1)

    async def test_failed_turn_is_not_replayed(self):
        request = AsyncMock(side_effect=[{"jobId": JOB}, {"status": "failed"}])
        with self.assertRaises(client.BridgeError):
            await client.converse(request, "Action", "assist_satellite.test")
        self.assertEqual(request.await_count, 2)

    async def test_poll_timeout_does_not_resubmit(self):
        request = AsyncMock(side_effect=[{"jobId": JOB}] + [{"status": "running"}] * 10)
        with self.assertRaises(TimeoutError):
            await client.converse(request, "Action", "assist_satellite.test", timeout=0.02)
        self.assertEqual(request.await_count, 2)
