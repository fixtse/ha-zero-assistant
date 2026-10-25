"""One submission per utterance, followed only by bounded read-only status checks."""
import asyncio
import uuid


class BridgeError(Exception):
    """The result is uncertain: never automatically repeat an action."""


class AccessDenied(BridgeError):
    """Zero rejected the authenticated user or device before submission."""


async def converse(request, text, satellite_id, timeout=290, ha_user_id=None):
    identity = {"ha_user_id": ha_user_id} if ha_user_id else {}
    query = "?ha_user_id=" + ha_user_id if ha_user_id else ""
    async with asyncio.timeout(timeout):
        accepted = await request("POST", "/v1/voice/turns", {
            "request_id": str(uuid.uuid4()), "text": text, "satellite_id": satellite_id, **identity,
        })
        job_id = str(uuid.UUID(accepted["jobId"]))
        while True:
            result = await request("GET", "/v1/voice/turns/" + job_id + query, None)
            if result["status"] == "done":
                output = result.get("output", "").strip()
                if not output:
                    raise BridgeError("Empty response")
                return output
            if result["status"] not in ("pending", "running"):
                raise BridgeError("Turn failed")
            await asyncio.sleep(0.5)
