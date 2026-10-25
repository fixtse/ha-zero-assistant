"""On-demand paginated assistant metadata, never prompts."""
import re
from .client import BridgeError


async def assistants(request):
    items = {}
    offset = 0
    while True:
        page = await request("GET", f"/v1/voice/assistants?limit=50&offset={offset}", None)
        values = page.get("items")
        total = page.get("total")
        if not isinstance(values, list) or not isinstance(total, int) or not 0 <= total <= 10000:
            raise BridgeError("Invalid assistant catalog")
        for value in values:
            if not isinstance(value, dict):
                raise BridgeError("Invalid assistant metadata")
            key, name = value.get("id"), value.get("name")
            if not isinstance(key, str) or not re.fullmatch(r"zero|salem|standard|[a-f0-9-]{36}", key) or key in items:
                raise BridgeError("Invalid or repeated assistant identity")
            if not isinstance(name, str) or not 1 <= len(name) <= 100:
                raise BridgeError("Invalid assistant name")
            items[key] = {"id": key, "name": name}
        offset += len(values)
        if offset >= total:
            break
        if not values:
            raise BridgeError("Assistant pagination did not advance")
    items.setdefault("zero", {"id": "zero", "name": "Zero"})
    return items
