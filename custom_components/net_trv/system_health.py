"""System health for NET-TRV: Netatmo reachability, API budget, webhook."""
# VERSION = "0.11.0"

from __future__ import annotations

from typing import Any

from homeassistant.components import system_health
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN, RATE_LIMIT_PER_HOUR
from .entity import valves_in

NETATMO_API = "https://api.netatmo.com"
# Where the user manages the Netatmo app (client id, webhook URL).
NETATMO_APPS_URL = "https://dev.netatmo.com/apps"


@callback
def async_register(
    hass: HomeAssistant, register: system_health.SystemHealthRegistration
) -> None:
    """Register system health callbacks."""
    register.async_register_info(system_health_info, NETATMO_APPS_URL)


async def system_health_info(hass: HomeAssistant) -> dict[str, Any]:
    """Return the values shown under Settings → System → Repairs → System info."""
    info: dict[str, Any] = {
        "can_reach_server": system_health.async_check_can_reach_url(hass, NETATMO_API)
    }
    # Only loaded entries are guaranteed to carry runtime_data.
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    if not entries:
        return info

    coordinator = entries[0].runtime_data
    webhook = coordinator.webhook
    valves = [
        valve
        for home in coordinator.homes
        for room in home.rooms.values()
        for valve in valves_in(room)
    ]
    info.update(
        {
            "api_calls_last_hour": (
                f"{coordinator.auth.calls_last_hour} / {RATE_LIMIT_PER_HOUR}"
            ),
            "webhook": "active"
            if webhook.fresh
            else ("registered" if webhook.registered else "inactive"),
            "last_webhook_event": (
                webhook.last_event_at.isoformat() if webhook.last_event_at else None
            ),
            "poll_interval": f"{int(coordinator.update_interval.total_seconds())} s"
            if coordinator.update_interval
            else None,
            "valves_online": (
                f"{sum(1 for v in valves if v.reachable)} / {len(valves)}"
            ),
            "room_history": (
                "off"
                if coordinator.history is None
                else coordinator.history.last_error or "ok"
            ),
            "public_weather": (
                "off"
                if coordinator.weather is None
                else ("ok" if coordinator.weather.last_update_success else "failing")
            ),
        }
    )
    return info
