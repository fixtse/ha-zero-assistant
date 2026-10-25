"""HA-initiated, one-use setup. Neither bearer credential passes through the browser."""
from datetime import timedelta
import secrets
from aiohttp import web
from homeassistant.auth.models import TOKEN_TYPE_LONG_LIVED_ACCESS_TOKEN
from homeassistant.components.http import HomeAssistantView
from homeassistant.components.http import KEY_HASS
from homeassistant.helpers.aiohttp_client import async_get_clientsession


async def zero_request(hass, url, path, value):
    async with async_get_clientsession(hass).post(url.rstrip("/") + path, json=value, timeout=15, allow_redirects=False) as response:
        if response.status not in (200, 201):
            raise ValueError("Pairing could not be confirmed; no automatic retry")
        return await response.json()


async def create_ha_token(hass, user_id, flow_id):
    user = await hass.auth.async_get_user(user_id)
    if not user or not user.is_active or user.system_generated:
        raise ValueError("Choose an active Home Assistant user")
    refresh = await hass.auth.async_create_refresh_token(
        user, client_name="Zero " + flow_id,
        token_type=TOKEN_TYPE_LONG_LIVED_ACCESS_TOKEN,
        access_token_expiration=timedelta(days=3650),
    )
    return refresh, hass.auth.async_create_access_token(refresh)


class PairingCallbackView(HomeAssistantView):
    url = "/api/assistant_zero/pairing/{flow_id}"
    name = "api:assistant_zero:pairing"
    requires_auth = False

    async def get(self, request, flow_id):
        # The external step validates its own random state before advancing.
        from homeassistant.data_entry_flow import UnknownFlow
        manager = request.app[KEY_HASS].config_entries.flow
        if not any(flow["flow_id"] == flow_id and flow["step_id"] == "pair" for flow in manager.async_progress_by_handler("assistant_zero")):
            return web.Response(status=400, text="Pairing expired or invalid.")
        try:
            result = await manager.async_configure(flow_id, {"state": request.query.get("state", "")})
        except (UnknownFlow, ValueError):
            return web.Response(status=400, text="Pairing expired or invalid. Return to Home Assistant.")
        if result["type"] != "external_done":
            return web.Response(status=400, text="Pairing was not approved.")
        return web.Response(text="<html><head><meta name='referrer' content='no-referrer'></head><body>Connection approved. Return to the Home Assistant setup dialog.<script>window.close()</script></body></html>", content_type="text/html", headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


def register_callback(hass):
    if not hass.data.get("assistant_zero_pairing_view"):
        hass.http.register_view(PairingCallbackView())
        hass.data["assistant_zero_pairing_view"] = True


def pairing_secret():
    return secrets.token_hex(32)
