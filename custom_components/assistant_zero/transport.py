"""Private bounded bridge transport; never retry a submission."""
import json
import aiohttp
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from .client import AccessDenied, BridgeError


class CatalogUnsupported(BridgeError):
    """An older Zero server has no assistant discovery endpoint."""


async def request(hass, settings, method, path, data=None):
    async with async_get_clientsession(hass).request(
        method, settings["url"].rstrip("/") + path,
        headers={"Authorization": "Bearer " + settings["token"]},
        json=data, timeout=aiohttp.ClientTimeout(total=15), allow_redirects=False,
    ) as result:
        if result.status == 404 and path.startswith("/v1/voice/assistants"):
            raise CatalogUnsupported("Assistant catalog is not supported by this Zero version")
        if result.status in (401, 403):
            raise AccessDenied("User is not bound to an enabled Zero profile")
        if result.status not in (200, 202):
            raise BridgeError("Bridge unavailable")
        payload = bytearray()
        async for chunk in result.content.iter_chunked(16384):
            payload.extend(chunk)
            if len(payload) > 1_000_000:
                raise BridgeError("Response too large")
        return json.loads(payload)
