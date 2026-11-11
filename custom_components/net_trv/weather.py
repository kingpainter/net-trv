"""Public weather from nearby Netatmo stations (``/getpublicdata``).

Runs on its own coordinator so a weather outage never touches heating. The
request is built here rather than through pyatmo, because pyatmo 9.9.0 sends
``filtering`` while the API expects ``filter``.

Every station in the area reports its own value; we show the median, which
ignores a station standing in the sun or against a wall. Wind direction uses
a circular mean, since a median of angles breaks around north.
"""
# VERSION = "0.11.2"

from __future__ import annotations

from dataclasses import dataclass, field
import logging
import math
from statistics import median
from typing import TYPE_CHECKING, Any

from aiohttp import ClientError
from homeassistant.const import CONF_LATITUDE, CONF_LONGITUDE, CONF_RADIUS
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from pyatmo.exceptions import ApiError

from .api import NetatmoEnergyAuth
from .const import (
    CONF_WEATHER_ENABLED,
    CONF_WEATHER_LOCATION,
    DEFAULT_WEATHER_RADIUS,
    DOMAIN,
    WEATHER_INTERVAL,
)

if TYPE_CHECKING:
    from .coordinator import NetatmoEnergyConfigEntry

_LOGGER = logging.getLogger(__name__)

PUBLIC_DATA_ENDPOINT = "api/getpublicdata"
METERS_PER_DEGREE = 111_320.0

# Values read from each station's "res" block (timestamp -> list of values).
STATION_MEASURES = ("temperature", "humidity", "pressure")
# Values read directly from rain gauge and anemometer modules.
ACCESSORY_MEASURES = (
    "rain_live",
    "rain_60min",
    "rain_24h",
    "wind_strength",
    "gust_strength",
)


@dataclass(frozen=True)
class Area:
    """Bounding box sent to Netatmo."""

    lat_ne: float
    lon_ne: float
    lat_sw: float
    lon_sw: float

    @classmethod
    def around(cls, latitude: float, longitude: float, radius: float) -> Area:
        """Return the square that encloses a circle of ``radius`` meters."""
        dlat = radius / METERS_PER_DEGREE
        cos_lat = max(math.cos(math.radians(latitude)), 0.01)
        dlon = radius / (METERS_PER_DEGREE * cos_lat)
        return cls(
            lat_ne=round(min(latitude + dlat, 85.0), 6),
            lon_ne=round(min(longitude + dlon, 180.0), 6),
            lat_sw=round(max(latitude - dlat, -85.0), 6),
            lon_sw=round(max(longitude - dlon, -180.0), 6),
        )

    def size_km(self) -> str:
        """Return the box size as "height x width" in km."""
        mid = math.radians((self.lat_ne + self.lat_sw) / 2)
        height = (self.lat_ne - self.lat_sw) * METERS_PER_DEGREE / 1000
        width = (self.lon_ne - self.lon_sw) * METERS_PER_DEGREE * math.cos(mid) / 1000
        return f"{height:.1f} x {width:.1f}"

    def params(self) -> dict[str, str]:
        """Return the query parameters for ``/getpublicdata``."""
        return {
            "lat_ne": str(self.lat_ne),
            "lon_ne": str(self.lon_ne),
            "lat_sw": str(self.lat_sw),
            "lon_sw": str(self.lon_sw),
            "filter": "true",
        }


@dataclass
class PublicWeather:
    """Aggregated values for the area, plus how many stations fed each."""

    stations: int = 0
    values: dict[str, float] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _station_samples(station: dict[str, Any]) -> dict[str, list[float]]:
    """Collect one station's latest readings, keyed by measure."""
    samples: dict[str, list[float]] = {}
    measures = station.get("measures")
    if not isinstance(measures, dict):
        return samples
    for module in measures.values():
        if not isinstance(module, dict):
            continue
        types, res = module.get("type"), module.get("res")
        if isinstance(types, list) and isinstance(res, dict) and res:
            latest = res[max(res, key=int)]
            if isinstance(latest, list):
                for name, raw in zip(types, latest, strict=False):
                    if name in STATION_MEASURES and (v := _number(raw)) is not None:
                        samples.setdefault(name, []).append(v)
        for name in ACCESSORY_MEASURES:
            if (v := _number(module.get(name))) is not None:
                samples.setdefault(name, []).append(v)
        if (v := _number(module.get("wind_angle"))) is not None:
            samples.setdefault("wind_angle", []).append(v)
    return samples


def _circular_mean(angles: list[float]) -> float:
    x = sum(math.cos(math.radians(a)) for a in angles)
    y = sum(math.sin(math.radians(a)) for a in angles)
    return math.degrees(math.atan2(y, x)) % 360


def aggregate(body: list[dict[str, Any]]) -> PublicWeather:
    """Turn the ``/getpublicdata`` body into one value per measure."""
    per_measure: dict[str, list[float]] = {}
    stations = 0
    for station in body:
        if not isinstance(station, dict):
            continue
        stations += 1
        for name, values in _station_samples(station).items():
            # One value per station: a station never outvotes the others.
            per_measure.setdefault(name, []).append(values[0])

    result = PublicWeather(stations=stations)
    for name, values in per_measure.items():
        result.counts[name] = len(values)
        if name == "wind_angle":
            result.values[name] = round(_circular_mean(values), 1)
        else:
            result.values[name] = round(median(values), 1)
    return result


def weather_area(hass: HomeAssistant, entry: NetatmoEnergyConfigEntry) -> Area | None:
    """Return the configured area, defaulting to Home Assistant's location."""
    options = entry.options
    if not options.get(CONF_WEATHER_ENABLED, True):
        return None
    location = options.get(CONF_WEATHER_LOCATION) or {}
    return Area.around(
        float(location.get(CONF_LATITUDE, hass.config.latitude)),
        float(location.get(CONF_LONGITUDE, hass.config.longitude)),
        float(location.get(CONF_RADIUS, DEFAULT_WEATHER_RADIUS)),
    )


class PublicWeatherCoordinator(DataUpdateCoordinator[PublicWeather]):
    """Poll ``/getpublicdata`` every few minutes."""

    config_entry: NetatmoEnergyConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: NetatmoEnergyConfigEntry,
        auth: NetatmoEnergyAuth,
        area: Area,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_public_weather",
            update_interval=WEATHER_INTERVAL,
        )
        self.auth = auth
        self.area = area

    async def _async_update_data(self) -> PublicWeather:
        try:
            resp = await self.auth.async_post_api_request(
                endpoint=PUBLIC_DATA_ENDPOINT, params=self.area.params()
            )
            payload = await resp.json()
        except (ApiError, ClientError, TimeoutError) as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="public_weather_failed",
                translation_placeholders={"error": str(err)},
            ) from err
        body = payload.get("body") if isinstance(payload, dict) else None
        return aggregate(body if isinstance(body, list) else [])
