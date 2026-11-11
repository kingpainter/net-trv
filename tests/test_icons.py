"""icons.json loads in Home Assistant and matches strings.json."""

from __future__ import annotations

import json
from pathlib import Path

from homeassistant.core import HomeAssistant
from homeassistant.helpers.icon import async_get_icons
import pytest

from custom_components.net_trv.const import DOMAIN

COMPONENT = Path(__file__).parent.parent / "custom_components" / DOMAIN


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_icons_load(hass: HomeAssistant) -> None:
    entity = await async_get_icons(hass, "entity", [DOMAIN])
    services = await async_get_icons(hass, "services", [DOMAIN])
    assert entity[DOMAIN]["sensor"]["valve_position"]["default"] == "mdi:valve"
    assert services[DOMAIN]["create_schedule"]["service"] == "mdi:calendar-plus"


def test_icon_keys_exist_in_strings() -> None:
    icons = json.loads((COMPONENT / "icons.json").read_text())
    strings = json.loads((COMPONENT / "strings.json").read_text())
    for platform, keys in icons["entity"].items():
        for key in keys:
            assert key in strings["entity"][platform], (platform, key)
    assert set(icons["services"]) == set(strings["services"])


def _state_icons_differ_from_default(node: object, path: str) -> list[str]:
    """Paths where a state icon equals its default (hassfest rejects these)."""
    if not isinstance(node, dict):
        return []
    found = [
        f"{path}.{state}"
        for state, icon in node.get("state", {}).items()
        if icon == node.get("default")
    ]
    for key, child in node.items():
        if key != "state":
            found += _state_icons_differ_from_default(child, f"{path}.{key}")
    return found


def test_no_state_icon_equals_default() -> None:
    icons = json.loads((COMPONENT / "icons.json").read_text())
    assert _state_icons_differ_from_default(icons, "icons") == []
