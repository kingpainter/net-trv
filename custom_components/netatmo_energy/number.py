"""Away and frost guard temperatures of the active heating schedule."""
# VERSION = "0.8.1"

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, override

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.const import EntityCategory, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from pyatmo.home import Home
from pyatmo.modules.module import Module

from .const import MAX_TEMP, MIN_TEMP, TEMP_STEP
from .coordinator import (
    NetatmoEnergyConfigEntry,
    NetatmoEnergyCoordinator,
    selected_therm_schedule,
)
from .entity import NetatmoEnergyRelayEntity, relay_of
from .schedules import async_edit_schedule

PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class ScheduleTempDescription(NumberEntityDescription):
    """A temperature stored on the Netatmo schedule (``away_temp``/``hg_temp``)."""

    field: str


SCHEDULE_TEMPS: tuple[ScheduleTempDescription, ...] = (
    ScheduleTempDescription(
        key="away_temp",
        translation_key="away_temp",
        field="away_temp",
    ),
    ScheduleTempDescription(
        key="hg_temp",
        translation_key="frost_guard_temp",
        field="hg_temp",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NetatmoEnergyConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create the schedule temperature numbers on each relay."""
    coordinator = entry.runtime_data
    async_add_entities(
        NetatmoScheduleTemperature(coordinator, home, relay, desc)
        for home in coordinator.homes
        if (relay := relay_of(home)) is not None
        for desc in SCHEDULE_TEMPS
    )


class NetatmoScheduleTemperature(NetatmoEnergyRelayEntity, NumberEntity):
    """Away or frost guard temperature; follows the active schedule."""

    entity_description: ScheduleTempDescription
    _attr_device_class = NumberDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_native_min_value = MIN_TEMP
    _attr_native_max_value = MAX_TEMP
    _attr_native_step = TEMP_STEP
    _attr_mode = NumberMode.BOX
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: NetatmoEnergyCoordinator,
        home: Home,
        relay: Module,
        description: ScheduleTempDescription,
    ) -> None:
        """Initialize the number."""
        super().__init__(coordinator, home, relay)
        self.entity_description = description
        self._attr_unique_id = f"{home.entity_id}-{description.key}"

    @property
    @override
    def available(self) -> bool:
        # A cloud setting: editable while the relay is offline.
        return self.coordinator_available and self.native_value is not None

    @property
    @override
    def native_value(self) -> float | None:
        schedule = selected_therm_schedule(self.home)
        value = getattr(schedule, self.entity_description.field, None)
        return float(value) if isinstance(value, int | float) else None

    @property
    @override
    def extra_state_attributes(self) -> dict[str, Any]:
        schedule = selected_therm_schedule(self.home)
        return {"schedule": schedule.name if schedule else None}

    @override
    async def async_set_native_value(self, value: float) -> None:
        """Write the value to the active schedule in Netatmo."""
        field = self.entity_description.field
        await async_edit_schedule(
            self.coordinator.auth,
            self.home.entity_id,
            None,
            lambda schedule: schedule.__setitem__(field, value),
        )
        if (schedule := selected_therm_schedule(self.home)) is not None:
            setattr(schedule, field, value)
        self.async_write_ha_state()
        self.coordinator.async_refresh_topology_soon()
