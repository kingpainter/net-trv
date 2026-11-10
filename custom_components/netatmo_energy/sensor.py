"""Diagnostic sensors for NET-TRV valves (on room devices) and the relay."""
# VERSION = "0.5.0"

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, override

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    DEGREE,
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfPrecipitationDepth,
    UnitOfPressure,
    UnitOfSpeed,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from pyatmo.home import Home
from pyatmo.modules.module import Module

from .coordinator import NetatmoEnergyConfigEntry, NetatmoEnergyCoordinator
from .entity import (
    NetatmoEnergyRelayEntity,
    NetatmoEnergyValveEntity,
    relay_of,
    valves_in,
    weather_device_info,
)
from .weather import PublicWeatherCoordinator

PARALLEL_UPDATES = 0

# Netatmo reports the valve battery as a coarse state; pyatmo's own mapping
# to percent is used for the battery sensor (approximate by nature).
BATTERY_STATES = ["max", "full", "high", "medium", "low", "very_low"]
_BATTERY_PERCENT = {
    "max": 100,
    "full": 90,
    "high": 75,
    "medium": 50,
    "low": 25,
    "very_low": 10,
}


def _battery_percent(module: Module) -> int | None:
    percent = getattr(module, "battery_percent", None)
    if percent is not None:
        return int(percent)
    state = getattr(module, "battery_state", None)
    return _BATTERY_PERCENT.get(state) if state else None


def _battery_state(module: Module) -> str | None:
    state = getattr(module, "battery_state", None)
    return state if state in BATTERY_STATES else None


def _dbm(value: Any) -> int | None:
    """Netatmo reports signal as a positive RSSI magnitude (60 good, 90 bad)."""
    return -int(value) if isinstance(value, int | float) else None


def _firmware(module: Module) -> str | None:
    firmware = getattr(module, "firmware_revision", None)
    return None if firmware is None else str(firmware)


@dataclass(frozen=True, kw_only=True)
class NetatmoModuleSensorDescription(SensorEntityDescription):
    """Sensor description reading one value from a pyatmo module."""

    value_fn: Callable[[Module], StateType]
    # Keep the last value while the module is unreachable (battery,
    # firmware); live readings (signal) go unavailable instead.
    sticky: bool = False


VALVE_SENSORS: tuple[NetatmoModuleSensorDescription, ...] = (
    NetatmoModuleSensorDescription(
        key="battery",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_battery_percent,
        sticky=True,
    ),
    NetatmoModuleSensorDescription(
        key="battery_state",
        translation_key="battery_state",
        device_class=SensorDeviceClass.ENUM,
        options=BATTERY_STATES,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_battery_state,
        sticky=True,
    ),
    NetatmoModuleSensorDescription(
        key="rf_strength",
        translation_key="rf_strength",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda m: _dbm(getattr(m, "rf_strength", None)),
    ),
    NetatmoModuleSensorDescription(
        key="firmware",
        translation_key="firmware",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_firmware,
        sticky=True,
    ),
)

# Raw motor position from motor_data_event (webhook only). The scale is not
# documented by Netatmo; observed values ~1700-2300, lower = more open.
VALVE_POSITION = SensorEntityDescription(
    key="valve_position",
    translation_key="valve_position",
    state_class=SensorStateClass.MEASUREMENT,
    entity_category=EntityCategory.DIAGNOSTIC,
)

RELAY_SENSORS: tuple[NetatmoModuleSensorDescription, ...] = (
    NetatmoModuleSensorDescription(
        key="wifi_strength",
        translation_key="wifi_strength",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda m: _dbm(getattr(m, "wifi_strength", None)),
    ),
    NetatmoModuleSensorDescription(
        key="firmware",
        translation_key="firmware",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_firmware,
        sticky=True,
    ),
)


def _weather(
    key: str,
    *,
    translation_key: str | None = None,
    device_class: SensorDeviceClass | None = None,
    unit: str | None = None,
    state_class: SensorStateClass = SensorStateClass.MEASUREMENT,
) -> SensorEntityDescription:
    return SensorEntityDescription(
        key=key,
        translation_key=translation_key,
        device_class=device_class,
        native_unit_of_measurement=unit,
        state_class=state_class,
    )


# Netatmo public data units: C, %, mbar (= hPa), mm, km/h, degrees.
WEATHER_SENSORS: tuple[SensorEntityDescription, ...] = (
    _weather(
        "temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        unit=UnitOfTemperature.CELSIUS,
    ),
    _weather("humidity", device_class=SensorDeviceClass.HUMIDITY, unit=PERCENTAGE),
    _weather(
        "pressure",
        device_class=SensorDeviceClass.ATMOSPHERIC_PRESSURE,
        unit=UnitOfPressure.HPA,
    ),
    _weather(
        "rain_live",
        translation_key="rain_live",
        device_class=SensorDeviceClass.PRECIPITATION,
        unit=UnitOfPrecipitationDepth.MILLIMETERS,
    ),
    _weather(
        "rain_60min",
        translation_key="rain_60min",
        device_class=SensorDeviceClass.PRECIPITATION,
        unit=UnitOfPrecipitationDepth.MILLIMETERS,
    ),
    _weather(
        "rain_24h",
        translation_key="rain_24h",
        device_class=SensorDeviceClass.PRECIPITATION,
        unit=UnitOfPrecipitationDepth.MILLIMETERS,
    ),
    _weather(
        "wind_strength",
        translation_key="wind_strength",
        device_class=SensorDeviceClass.WIND_SPEED,
        unit=UnitOfSpeed.KILOMETERS_PER_HOUR,
    ),
    _weather(
        "gust_strength",
        translation_key="gust_strength",
        device_class=SensorDeviceClass.WIND_SPEED,
        unit=UnitOfSpeed.KILOMETERS_PER_HOUR,
    ),
    _weather(
        "wind_angle",
        translation_key="wind_angle",
        device_class=SensorDeviceClass.WIND_DIRECTION,
        unit=DEGREE,
        state_class=SensorStateClass.MEASUREMENT_ANGLE,
    ),
)

WEATHER_STATIONS = SensorEntityDescription(
    key="stations",
    translation_key="weather_stations",
    state_class=SensorStateClass.MEASUREMENT,
    entity_category=EntityCategory.DIAGNOSTIC,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NetatmoEnergyConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create valve sensors on room devices and relay sensors on the relay."""
    coordinator = entry.runtime_data
    entities: list[SensorEntity] = []
    for home in coordinator.homes:
        for room in home.rooms.values():
            valves = valves_in(room)
            entities.extend(
                NetatmoValveSensor(coordinator, room, valve, len(valves) > 1, desc)
                for valve in valves
                for desc in VALVE_SENSORS
            )
            entities.extend(
                NetatmoValvePositionSensor(
                    coordinator, room, valve, len(valves) > 1, VALVE_POSITION
                )
                for valve in valves
            )
        if (relay := relay_of(home)) is not None:
            entities.extend(
                NetatmoRelaySensor(coordinator, home, relay, desc)
                for desc in RELAY_SENSORS
            )
            entities.append(NetatmoValvesOnlineSensor(coordinator, home, relay))
    if (weather := coordinator.weather) is not None:
        entities.extend(
            NetatmoPublicWeatherSensor(weather, desc) for desc in WEATHER_SENSORS
        )
        entities.append(NetatmoWeatherStationsSensor(weather, WEATHER_STATIONS))
    async_add_entities(entities)


class NetatmoValveSensor(NetatmoEnergyValveEntity, SensorEntity):
    """A diagnostic value of one valve, shown on its room device."""

    entity_description: NetatmoModuleSensorDescription

    @property
    @override
    def available(self) -> bool:
        if self.entity_description.sticky:
            return self.coordinator_available
        return super().available

    @property
    @override
    def native_value(self) -> StateType:
        return self.entity_description.value_fn(self.valve)


class NetatmoValvePositionSensor(NetatmoEnergyValveEntity, SensorEntity):
    """Raw valve motor position, pushed by the webhook."""

    @property
    @override
    def available(self) -> bool:
        return self.coordinator_available

    @property
    @override
    def native_value(self) -> int | None:
        report = self.coordinator.valve_positions.get(self.valve.entity_id)
        return report.position if report else None

    @property
    @override
    def extra_state_attributes(self) -> dict[str, Any]:
        report = self.coordinator.valve_positions.get(self.valve.entity_id)
        return {
            "motor_cmd": report.motor_cmd if report else None,
            "netatmo_ts": report.ts if report else None,
        }


class NetatmoRelaySensor(NetatmoEnergyRelayEntity, SensorEntity):
    """A diagnostic value of the relay."""

    entity_description: NetatmoModuleSensorDescription

    def __init__(
        self,
        coordinator: NetatmoEnergyCoordinator,
        home: Home,
        relay: Module,
        description: NetatmoModuleSensorDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, home, relay)
        self.entity_description = description
        self._attr_unique_id = f"{relay.entity_id}-{description.key}"

    @property
    @override
    def native_value(self) -> StateType:
        return self.entity_description.value_fn(self.relay)


class NetatmoValvesOnlineSensor(NetatmoEnergyRelayEntity, SensorEntity):
    """How many of the relay's valves are reachable right now."""

    _attr_translation_key = "valves_online"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self, coordinator: NetatmoEnergyCoordinator, home: Home, relay: Module
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, home, relay)
        self._attr_unique_id = f"{relay.entity_id}-valves_online"

    def _valves(self) -> list[Module]:
        return [
            module for room in self.home.rooms.values() for module in valves_in(room)
        ]

    @property
    @override
    def available(self) -> bool:
        # Shows 0/N while the relay is down; that is the point of the sensor.
        return self.coordinator_available

    @property
    @override
    def native_value(self) -> int:
        return sum(1 for valve in self._valves() if valve.reachable)

    @property
    @override
    def extra_state_attributes(self) -> dict[str, Any]:
        valves = self._valves()
        return {
            "total": len(valves),
            "offline": sorted(v.name for v in valves if not v.reachable),
        }


class NetatmoPublicWeatherSensor(
    CoordinatorEntity[PublicWeatherCoordinator], SensorEntity
):
    """One aggregated value from nearby public Netatmo stations."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: PublicWeatherCoordinator,
        description: SensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.entry_id}-public_weather-{description.key}"
        self._attr_device_info = weather_device_info(entry)

    @property
    @override
    def available(self) -> bool:
        return super().available and self.native_value is not None

    @property
    @override
    def native_value(self) -> float | int | None:
        data = self.coordinator.data
        return None if data is None else data.values.get(self.entity_description.key)

    @property
    @override
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data
        return {
            "stations": 0
            if data is None
            else data.counts.get(self.entity_description.key, 0)
        }


class NetatmoWeatherStationsSensor(NetatmoPublicWeatherSensor):
    """How many public stations answered for the area."""

    @property
    @override
    def available(self) -> bool:
        return self.coordinator.last_update_success

    @property
    @override
    def native_value(self) -> int | None:
        data = self.coordinator.data
        return None if data is None else data.stations

    @property
    @override
    def extra_state_attributes(self) -> dict[str, Any]:
        # Size only; the corners would reveal the home's location.
        return {"area_km": self.coordinator.area.size_km()}
