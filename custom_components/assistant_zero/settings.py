"""Read and write Zero's authoritative settings without retaining a prompt in HA."""
import json
import aiohttp
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from .client import BridgeError


async def settings_request(hass, connection, method="GET", value=None, path=""):
    token = connection.get("settings_token")
    if not token:
        raise BridgeError("A dedicated Zero settings token is required")
    async with async_get_clientsession(hass).request(
        method, connection["url"].rstrip("/") + "/v1/integrations/home-assistant/settings" + path,
        headers={"Authorization": "Bearer " + token}, json=value,
        timeout=aiohttp.ClientTimeout(total=15), allow_redirects=False,
    ) as response:
        if response.status == 409:
            raise BridgeError("conflict")
        if response.status == 400:
            raise BridgeError("invalid_user")
        if response.status != 200:
            raise BridgeError("Cannot access Zero settings")
        payload = bytearray()
        async for chunk in response.content.iter_chunked(16384):
            payload.extend(chunk)
            if len(payload) > 1_000_000:
                raise BridgeError("Response too large")
        return json.loads(payload)
