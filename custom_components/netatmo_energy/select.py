"""Heating schedule selection on the relay device."""
# VERSION = "0.7.0"

from __future__ import annotations

from typing import override

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from pyatmo.enums import ScheduleType
from pyatmo.exceptions import ApiError
from pyatmo.home import Home
from pyatmo.modules.module import Module

from .const import DOMAIN
from .coordinator import (
    NetatmoEnergyConfigEntry,
    NetatmoEnergyCoordinator,
    selected_therm_schedule,
)
from .entity import NetatmoEnergyRelayEntity, relay_of

PARALLEL_UPDATES = 1


def therm_schedules(home: Home) -> dict[str, str]:
    """Return heating schedules as {name: schedule id}, in Netatmo order."""
    return {
        str(schedule.name): schedule.entity_id
        for schedule in home.schedules.values()
        if schedule.type == ScheduleType.THERM and schedule.name
    }


async def async_switch_schedule(home: Home, name: str) -> None:
    """Switch the home to the heating schedule called ``name``."""
    schedule_id = therm_schedules(home).get(name)
    if schedule_id is None:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="unknown_schedule",
            translation_placeholders={"schedule": name},
        )
    try:
        ok = await home.async_switch_schedule(schedule_id)
    except ApiError as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="api_error",
            translation_placeholders={"error": str(err)},
        ) from err
    if not ok:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="api_error",
            translation_placeholders={"error": "switchhomeschedule was not accepted"},
        )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NetatmoEnergyConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create one schedule select per relay."""
    coordinator = entry.runtime_data
    async_add_entities(
        NetatmoScheduleSelect(coordinator, home, relay)
        for home in coordinator.homes
        if (relay := relay_of(home)) is not None and therm_schedules(home)
    )


class NetatmoScheduleSelect(NetatmoEnergyRelayEntity, SelectEntity):
    """The home's active heating schedule (Netatmo "Sommer", "Vinter", ...)."""

    _attr_translation_key = "schedule"

    def __init__(
        self, coordinator: NetatmoEnergyCoordinator, home: Home, relay: Module
    ) -> None:
        """Initialize the select."""
        super().__init__(coordinator, home, relay)
        self._attr_unique_id = f"{home.entity_id}-schedule"

    @property
    @override
    def available(self) -> bool:
        # Switching is a cloud call; it works while the relay is offline.
        return self.coordinator_available

    @property
    @override
    def options(self) -> list[str]:
        return list(therm_schedules(self.home))

    @property
    @override
    def current_option(self) -> str | None:
        schedule = selected_therm_schedule(self.home)
        return str(schedule.name) if schedule and schedule.name else None

    @override
    async def async_select_option(self, option: str) -> None:
        """Switch schedule; pyatmo marks it selected, then confirm from Netatmo."""
        await async_switch_schedule(self.home, option)
        self.coordinator.async_update_listeners()
        self.coordinator.async_schedule_confirm()
