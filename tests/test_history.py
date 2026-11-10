"""Room history import into long-term statistics (0.7.0)."""

from __future__ import annotations

from unittest.mock import AsyncMock

from homeassistant.components.recorder import Recorder
from homeassistant.components.recorder.statistics import (
    list_statistic_ids,
    statistics_during_period,
)
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.net_trv.history import (
    BACKFILL_DAYS,
    parse_room_measure,
    to_statistics,
)

pytestmark = pytest.mark.usefixtures("credentials")


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(
    recorder_mock: Recorder, enable_custom_integrations: None
) -> None:
    """Start the recorder before hass (overrides the conftest fixture)."""


STUEN_TEMP = "net_trv:1001_temperature"
STUEN_SETPOINT = "net_trv:1001_setpoint"


def test_parse_both_formats() -> None:
    flat = parse_room_measure({"5400": [20.1, 19.8, 20.4, 21], "9000": [20.3]})
    assert flat == {3600: [20.1, 19.8, 20.4, 21.0], 7200: [20.3]}

    blocks = parse_room_measure(
        [{"beg_time": 3600, "step_time": 3600, "value": [[20.1, 1, 2, 21], [20.2]]}]
    )
    assert sorted(blocks) == [3600, 7200]
    assert parse_room_measure(None) == {}
    assert parse_room_measure({"bad": [1], "3600": "x"}) == {}


def test_to_statistics_fills_min_max() -> None:
    temperature, setpoint = to_statistics({3600: [20.3], 7200: [20.0, 19, 21, 22]})
    assert temperature[0]["min"] == temperature[0]["max"] == 20.3
    assert temperature[1]["min"] == 19
    assert [s["mean"] for s in setpoint] == [22]


async def test_history_backfill_then_incremental(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_api: AsyncMock,
) -> None:
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    history = config_entry.runtime_data.history
    assert history is not None

    await history.async_run()
    await async_wait_recording_done(hass)

    ids = {
        row["statistic_id"]
        for row in await recorder_mock.async_add_executor_job(list_statistic_ids, hass)
    }
    assert {STUEN_TEMP, STUEN_SETPOINT} <= ids

    start = dt_util.utcnow() - dt_util.dt.timedelta(days=BACKFILL_DAYS + 1)
    stats = await recorder_mock.async_add_executor_job(
        statistics_during_period,
        hass,
        start,
        None,
        {STUEN_TEMP},
        "hour",
        None,
        {"mean", "min", "max"},
    )
    rows = stats[STUEN_TEMP]
    assert len(rows) == BACKFILL_DAYS * 24
    assert rows[-1]["mean"] == 20.0
    assert rows[-1]["min"] == 19.5

    # Second run in the same hour: nothing new to ask Netatmo for.
    calls = [c for c in mock_api.call_args_list if "getroommeasure" in c.kwargs["url"]]
    before = len(calls)
    await history.async_run()
    calls = [c for c in mock_api.call_args_list if "getroommeasure" in c.kwargs["url"]]
    assert len(calls) == before
