"""Room history from Netatmo (``/getroommeasure``) as long-term statistics.

Netatmo keeps hourly room temperatures and setpoints in the cloud. We import
them as external statistics (``netatmo_energy:<room_id>_temperature`` and
``..._setpoint``), so graphs have no gaps when Home Assistant was down and
start with up to ``BACKFILL_DAYS`` of history from before NET-TRV.

Once an hour each room is read from the last imported hour onward: one call
per room, so 5 calls/hour for five rooms.
"""
# VERSION = "0.8.1"

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
import logging
from typing import TYPE_CHECKING, Any

from aiohttp import ClientError
from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_last_statistics,
)
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.recorder import get_instance
from homeassistant.util import dt as dt_util
from pyatmo.exceptions import ApiError
from pyatmo.room import Room

from .const import DOMAIN
from .entity import valves_in

if TYPE_CHECKING:
    from .coordinator import NetatmoEnergyConfigEntry

_LOGGER = logging.getLogger(__name__)

ROOM_MEASURE_ENDPOINT = "api/getroommeasure"
HISTORY_INTERVAL = timedelta(hours=1)
BACKFILL_DAYS = 30  # 720 hourly values, below Netatmo's limit of 1024
HOUR = 3600
# One call returns all four series; values come in this order.
MEASURE_TYPES = ("temperature", "min_temp", "max_temp", "sp_temperature")


def statistic_id(room: Room, kind: str) -> str:
    """Return the external statistic id for a room series."""
    return f"{DOMAIN}:{room.entity_id}_{kind}".lower()


def _metadata(room: Room, kind: str, label: str) -> StatisticMetaData:
    return StatisticMetaData(
        mean_type=StatisticMeanType.ARITHMETIC,
        has_sum=False,
        name=f"{room.name} {label} (Netatmo)",
        source=DOMAIN,
        statistic_id=statistic_id(room, kind),
        unit_class="temperature",
        unit_of_measurement=UnitOfTemperature.CELSIUS,
    )


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def parse_room_measure(body: Any) -> dict[int, list[float | None]]:
    """Return {hour start (unix): [values in MEASURE_TYPES order]}.

    Netatmo answers ``{"<ts>": [..]}`` with ``optimize=false`` and
    ``[{"beg_time", "step_time", "value": [[..], ..]}]`` with
    ``optimize=true``; both are accepted. Timestamps are centred in the hour
    unless ``real_time`` is set, so they are floored to the hour.
    """
    rows: dict[int, list[float | None]] = {}

    def _add(ts: Any, values: Any) -> None:
        if not isinstance(values, list):
            return
        try:
            start = int(ts) // HOUR * HOUR
        except TypeError, ValueError:
            return
        rows[start] = [_number(v) for v in values]

    if isinstance(body, dict):
        for ts, values in body.items():
            _add(ts, values)
    elif isinstance(body, list):
        for block in body:
            if not isinstance(block, dict):
                continue
            begin, step = block.get("beg_time"), block.get("step_time") or HOUR
            for index, values in enumerate(block.get("value") or []):
                if isinstance(begin, int | float):
                    _add(begin + index * step, values)
    return rows


def to_statistics(
    rows: dict[int, list[float | None]],
) -> tuple[list[StatisticData], list[StatisticData]]:
    """Split parsed rows into temperature and setpoint statistics."""
    temperature: list[StatisticData] = []
    setpoint: list[StatisticData] = []
    for start in sorted(rows):
        values = [*rows[start], None, None, None, None][: len(MEASURE_TYPES)]
        mean, low, high, target = values
        when = dt_util.utc_from_timestamp(start)
        if mean is not None:
            temperature.append(
                StatisticData(
                    start=when,
                    mean=mean,
                    min=low if low is not None else mean,
                    max=high if high is not None else mean,
                )
            )
        if target is not None:
            setpoint.append(
                StatisticData(start=when, mean=target, min=target, max=target)
            )
    return temperature, setpoint


class RoomHistoryImporter:
    """Import room history every hour."""

    def __init__(self, hass: HomeAssistant, entry: NetatmoEnergyConfigEntry) -> None:
        """Initialize the importer."""
        self.hass = hass
        self.entry = entry
        self.last_run: datetime | None = None
        self.last_error: str | None = None
        self.imported_hours = 0
        self._unsub: Callable[[], None] | None = None

    @callback
    def async_start(self) -> None:
        """Run now and then every hour."""
        self.entry.async_create_background_task(
            self.hass, self.async_run(), "netatmo_energy_room_history"
        )

        async def _tick(_now: datetime) -> None:
            await self.async_run()

        self._unsub = async_track_time_interval(self.hass, _tick, HISTORY_INTERVAL)
        self.entry.async_on_unload(self.async_stop)

    @callback
    def async_stop(self) -> None:
        """Stop the hourly import."""
        if self._unsub is not None:
            self._unsub()
            self._unsub = None

    async def async_run(self) -> None:
        """Import every room with a valve."""
        coordinator = self.entry.runtime_data
        for home in coordinator.homes:
            for room in home.rooms.values():
                if valves_in(room):
                    await self._async_import_room(room)
        self.last_run = dt_util.utcnow()

    async def _async_last_start(self, room: Room) -> int | None:
        sid = statistic_id(room, "temperature")
        last = await get_instance(self.hass).async_add_executor_job(
            get_last_statistics, self.hass, 1, sid, False, {"mean"}
        )
        rows = last.get(sid)
        return int(rows[0]["start"]) if rows else None

    async def _async_import_room(self, room: Room) -> None:
        now = int(dt_util.utcnow().timestamp())
        end = now // HOUR * HOUR  # complete hours only
        last = await self._async_last_start(room)
        begin = last + HOUR if last is not None else end - BACKFILL_DAYS * 86400
        if begin >= end:
            return

        auth = self.entry.runtime_data.auth
        try:
            resp = await auth.async_post_api_request(
                endpoint=ROOM_MEASURE_ENDPOINT,
                params={
                    "home_id": room.home.entity_id,
                    "room_id": room.entity_id,
                    "scale": "1hour",
                    "type": ",".join(MEASURE_TYPES),
                    "date_begin": str(begin),
                    "date_end": str(end),
                    "optimize": "false",
                },
            )
            payload = await resp.json()
        except (ApiError, ClientError, TimeoutError) as err:
            if self.last_error != str(err):
                _LOGGER.warning("Could not read room history from Netatmo: %s", err)
            self.last_error = str(err)
            return
        self.last_error = None

        body = payload.get("body") if isinstance(payload, dict) else None
        rows = {
            start: values
            for start, values in parse_room_measure(body).items()
            if begin <= start < end
        }
        temperature, setpoint = to_statistics(rows)
        if temperature:
            async_add_external_statistics(
                self.hass, _metadata(room, "temperature", "temperature"), temperature
            )
        if setpoint:
            async_add_external_statistics(
                self.hass, _metadata(room, "setpoint", "setpoint"), setpoint
            )
        self.imported_hours += len(rows)
