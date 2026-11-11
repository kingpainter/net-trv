"""A room with a valve added in the Netatmo app appears without a reload."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.net_trv.const import DOMAIN

from .test_sensor import _patched_fixtures

pytestmark = pytest.mark.usefixtures("credentials")

BAD_VALVE = "09:00:00:00:00:02"


def _without_bad(name: str, data: dict[str, Any]) -> None:
    """Topology and status as if the bathroom valve were not installed yet."""
    if name == "homesdata.json":
        for home in data["body"]["homes"]:
            home["modules"] = [m for m in home["modules"] if m["id"] != BAD_VALVE]
            for room in home["rooms"]:
                if room["id"] == "1002":
                    room["module_ids"] = []
    elif name == "homestatus.json":
        home = data["body"]["home"]
        home["modules"] = [m for m in home["modules"] if m["id"] != BAD_VALVE]


async def test_new_valve_room_is_added(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_api: AsyncMock
) -> None:
    config_entry.add_to_hass(hass)
    with _patched_fixtures(_without_bad):
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()
    assert hass.states.get("climate.bad") is None
    assert hass.states.get("climate.stuen") is not None

    # Next topology read sees the new valve (normally every 3 h).
    coordinator = config_entry.runtime_data
    coordinator._topology_at = None
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert hass.states.get("climate.bad") is not None
    assert hass.states.get("sensor.bad_battery_state") is not None
    assert hass.states.get("binary_sensor.bad_connectivity") is not None
    device = dr.async_get(hass).async_get_devices(
        identifiers={(DOMAIN, "home1-1002")}, config_entry_id=config_entry.entry_id
    )
    assert device

    # A further refresh without topology change adds nothing twice.
    before = len(hass.states.async_all())
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert len(hass.states.async_all()) == before
