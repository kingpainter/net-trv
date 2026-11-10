"""Public weather from nearby stations (0.5.0)."""

from __future__ import annotations

from unittest.mock import AsyncMock

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_LATITUDE, CONF_LONGITUDE, CONF_RADIUS
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from pyatmo.exceptions import ApiError
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.net_trv.const import (
    CONF_WEATHER_ENABLED,
    CONF_WEATHER_LOCATION,
)
from custom_components.net_trv.weather import Area, aggregate

from .conftest import load_fixture

pytestmark = pytest.mark.usefixtures("credentials")

TEMPERATURE = "sensor.public_weather_temperature"


def test_aggregate_median_and_circular_wind() -> None:
    weather = aggregate(load_fixture("publicdata.json")["body"])

    assert weather.stations == 3
    # Latest sample per station (6.8, not the older 5.0), then the median.
    assert weather.values["temperature"] == 6.8
    assert weather.values["humidity"] == 92
    assert weather.values["pressure"] == 1008.4
    assert weather.counts["temperature"] == 3
    # Only one station has a rain gauge.
    assert weather.values["rain_24h"] == 1.1
    assert weather.counts["rain_24h"] == 1
    # 350 and 10 degrees average to north, not 180.
    assert weather.values["wind_angle"] in (0.0, 360.0)
    assert weather.values["wind_strength"] == 2.5


def test_aggregate_tolerates_junk() -> None:
    junk: list = [{"_id": "x", "measures": {"m": {"res": {}, "type": []}}}, "no"]  # type: ignore[type-arg]
    weather = aggregate(junk)
    assert weather.stations == 1
    assert weather.values == {}


def test_area_around() -> None:
    area = Area.around(56.0, 10.0, 1000)
    assert area.lat_ne > 56.0 > area.lat_sw
    assert area.lon_ne > 10.0 > area.lon_sw
    # Longitude span is wider than latitude span at 56 N.
    assert (area.lon_ne - area.lon_sw) > (area.lat_ne - area.lat_sw)
    assert area.size_km() == "2.0 x 2.0"
    assert area.params()["filter"] == "true"


async def test_sensors_and_request(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_api: AsyncMock
) -> None:
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get(TEMPERATURE).state == "6.8"
    assert hass.states.get(TEMPERATURE).attributes["stations"] == 3
    assert hass.states.get("sensor.public_weather_wind_direction").state in (
        "0.0",
        "360.0",
    )
    assert hass.states.get("sensor.public_weather_stations_in_area").state == "3"

    calls = [c for c in mock_api.call_args_list if "getpublicdata" in c.kwargs["url"]]
    assert len(calls) == 1
    params = calls[0].kwargs["params"]
    assert params["filter"] == "true"
    assert float(params["lat_ne"]) > hass.config.latitude > float(params["lat_sw"])


async def test_weather_failure_does_not_block_heating(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_api: AsyncMock
) -> None:
    original = mock_api.side_effect

    async def _post(url: str, params: dict | None = None):  # type: ignore[no-untyped-def,type-arg]
        if url.endswith("api/getpublicdata"):
            raise ApiError("403 - Missing scope")
        return await original(url=url, params=params)

    mock_api.side_effect = _post
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.LOADED
    assert hass.states.get("climate.stuen").state != "unavailable"
    assert hass.states.get(TEMPERATURE).state == "unavailable"


async def test_options_disable_and_move_area(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_api: AsyncMock
) -> None:
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    # Move the area: saving reloads the entry with the new box.
    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    location = {CONF_LATITUDE: 56.2, CONF_LONGITUDE: 10.2, CONF_RADIUS: 3000}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_WEATHER_ENABLED: True, CONF_WEATHER_LOCATION: location},
    )
    await hass.async_block_till_done()
    assert config_entry.options[CONF_WEATHER_LOCATION] == location
    params = [
        c.kwargs["params"]
        for c in mock_api.call_args_list
        if "getpublicdata" in c.kwargs["url"]
    ][-1]
    assert float(params["lat_ne"]) > 56.2 > float(params["lat_sw"])

    # Switch it off: the service device goes away.
    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_WEATHER_ENABLED: False, CONF_WEATHER_LOCATION: location},
    )
    await hass.async_block_till_done()
    assert hass.states.get(TEMPERATURE) is None or (
        hass.states.get(TEMPERATURE).state == "unavailable"
    )
    registry = dr.async_get(hass)
    assert not registry.async_get_devices(
        identifiers={("net_trv", f"{config_entry.entry_id}-public_weather")}
    )
