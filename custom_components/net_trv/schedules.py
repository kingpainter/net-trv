"""Edit Netatmo heating schedules (``synchomeschedule`` / ``createnewhomeschedule``).

``synchomeschedule`` replaces the whole schedule, so a partial or stale copy
would erase zones or the timetable. Every edit therefore starts from a fresh
``/homesdata`` read of that one schedule and changes only the requested
fields. pyatmo's ``Home.async_sync_schedule`` is not used: it always sends
``name="Default"`` and would rename the schedule.
"""
# VERSION = "0.9.0"

from __future__ import annotations

from collections.abc import Callable
import copy
from typing import Any

from aiohttp import ClientError
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from pyatmo.exceptions import ApiError

from .api import NetatmoEnergyAuth
from .const import DOMAIN

HOMESDATA_ENDPOINT = "api/homesdata"
SYNC_ENDPOINT = "api/synchomeschedule"
CREATE_ENDPOINT = "api/createnewhomeschedule"
THERM = "therm"


def _api_error(err: Exception | str) -> HomeAssistantError:
    return HomeAssistantError(
        translation_domain=DOMAIN,
        translation_key="api_error",
        translation_placeholders={"error": str(err)},
    )


async def _post(
    auth: NetatmoEnergyAuth, endpoint: str, params: dict[str, Any]
) -> dict[str, Any]:
    try:
        resp = await auth.async_post_api_request(endpoint=endpoint, params=params)
        payload = await resp.json()
    except (ApiError, ClientError, TimeoutError) as err:
        raise _api_error(err) from err
    if not isinstance(payload, dict) or payload.get("status") != "ok":
        raise _api_error(f"{endpoint} was not accepted: {payload}")
    return payload


async def async_fetch_schedules(
    auth: NetatmoEnergyAuth, home_id: str
) -> list[dict[str, Any]]:
    """Return the raw heating schedules of a home, fresh from Netatmo."""
    payload = await _post(auth, HOMESDATA_ENDPOINT, {"home_id": home_id})
    homes = (payload.get("body") or {}).get("homes") or []
    for home in homes:
        if isinstance(home, dict) and home.get("id") == home_id:
            return [
                s
                for s in home.get("schedules") or []
                if isinstance(s, dict) and s.get("type", THERM) == THERM
            ]
    return []


def find_schedule(schedules: list[dict[str, Any]], name: str | None) -> dict[str, Any]:
    """Return the schedule called ``name``, or the selected one for None."""
    for schedule in schedules:
        if (name is None and schedule.get("selected")) or (
            name is not None and schedule.get("name") == name
        ):
            return schedule
    raise ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key="unknown_schedule",
        translation_placeholders={"schedule": name or "?"},
    )


def schedule_body(schedule: dict[str, Any]) -> dict[str, Any]:
    """Build the JSON body Netatmo expects, keeping every zone and slot."""
    return {
        "away_temp": schedule.get("away_temp"),
        "hg_temp": schedule.get("hg_temp"),
        "timetable": [
            {"zone_id": slot.get("zone_id"), "m_offset": slot.get("m_offset")}
            for slot in schedule.get("timetable") or []
        ],
        "zones": [
            {
                "id": zone.get("id"),
                "name": zone.get("name"),
                "type": zone.get("type"),
                "rooms": [
                    {
                        "id": room.get("id"),
                        "therm_setpoint_temperature": room.get(
                            "therm_setpoint_temperature"
                        ),
                    }
                    for room in zone.get("rooms") or []
                ],
            }
            for zone in schedule.get("zones") or []
        ],
    }


def set_zone_temperature(
    schedule: dict[str, Any], zone_name: str, room_id: str, temperature: float
) -> None:
    """Change one room's temperature in one zone of a raw schedule."""
    for zone in schedule.get("zones") or []:
        if zone.get("name") != zone_name:
            continue
        for room in zone.get("rooms") or []:
            if str(room.get("id")) == room_id:
                room["therm_setpoint_temperature"] = temperature
                return
        zone.setdefault("rooms", []).append(
            {"id": room_id, "therm_setpoint_temperature": temperature}
        )
        return
    raise ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key="unknown_zone",
        translation_placeholders={
            "zone": zone_name,
            "schedule": str(schedule.get("name")),
            "zones": ", ".join(str(z.get("name")) for z in schedule.get("zones") or []),
        },
    )


async def async_edit_schedule(
    auth: NetatmoEnergyAuth,
    home_id: str,
    schedule_name: str | None,
    change: Callable[[dict[str, Any]], None],
    *,
    new_name: str | None = None,
) -> None:
    """Read a schedule fresh, apply ``change`` to a copy, and sync it back."""
    schedule = copy.deepcopy(
        find_schedule(await async_fetch_schedules(auth, home_id), schedule_name)
    )
    change(schedule)
    await _post(
        auth,
        SYNC_ENDPOINT,
        {
            "params": {
                "home_id": home_id,
                "schedule_id": schedule["id"],
                "name": new_name or schedule.get("name") or "",
            },
            "json": schedule_body(schedule),
        },
    )


async def async_create_schedule(
    auth: NetatmoEnergyAuth, home_id: str, name: str, copy_from: str | None
) -> None:
    """Create a new heating schedule as a copy of an existing one."""
    schedules = await async_fetch_schedules(auth, home_id)
    if any(s.get("name") == name for s in schedules):
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="schedule_exists",
            translation_placeholders={"schedule": name},
        )
    source = find_schedule(schedules, copy_from)
    await _post(
        auth,
        CREATE_ENDPOINT,
        {
            "params": {"home_id": home_id, "name": name},
            "json": schedule_body(source),
        },
    )
