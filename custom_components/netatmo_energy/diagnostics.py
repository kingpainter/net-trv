"""Diagnostics for NET-TRV (Settings > Devices > Download diagnostics)."""
# VERSION = "0.3.1"

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_WEBHOOK_ID
from homeassistant.core import HomeAssistant

from .const import CONF_CLOUDHOOK_URL
from .coordinator import NetatmoEnergyConfigEntry
from .entity import relay_of, valves_in

TO_REDACT = {
    "access_token",
    "refresh_token",
    "token",
    CONF_WEBHOOK_ID,
    CONF_CLOUDHOOK_URL,
    "user_id",
    "email",
    "home_name",
    "coordinates",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: NetatmoEnergyConfigEntry
) -> dict[str, Any]:
    """Return a redacted snapshot, including the last raw webhook payloads."""
    coordinator = entry.runtime_data
    state = coordinator.webhook
    homes = []
    for home in coordinator.homes:
        relay = relay_of(home)
        homes.append(
            {
                "therm_mode": home.therm_mode,
                "relay": relay.entity_id if relay else None,
                "rooms": [
                    {
                        "id": room.entity_id,
                        "name": room.name,
                        "valves": [v.entity_id for v in valves_in(room)],
                        "therm_setpoint_mode": room.therm_setpoint_mode,
                        "heating_power_request": room.heating_power_request,
                        "open_window": room.open_window,
                    }
                    for room in home.rooms.values()
                ],
            }
        )
    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "api_calls_last_hour": coordinator.auth.calls_last_hour,
        "homes": homes,
        "webhook": {
            "registered": state.registered,
            "deleted_by_netatmo": state.deleted_by_netatmo,
            "last_error": state.last_error,
            "events_received": state.events_received,
            "rejected_signatures": state.rejected_signatures,
            "last_event": state.last_event_at.isoformat()
            if state.last_event_at
            else None,
            # Raw payloads are what phase 2 is built on; keep ids, drop
            # personal fields.
            "recent": async_redact_data(list(state.recent), TO_REDACT),
        },
    }
