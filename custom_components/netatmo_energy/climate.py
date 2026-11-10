"""Climate entities for NET-TRV: one per Netatmo room."""
# VERSION = "0.8.0"

from __future__ import annotations

from collections.abc import Awaitable
from datetime import UTC, datetime, timedelta
from typing import Any, override

from homeassistant.components.climate import ClimateEntity
from homeassistant.components.climate.const import (
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util
from pyatmo.exceptions import ApiError
from pyatmo.room import Room

from .const import (
    DOMAIN,
    MAX_TEMP,
    MIN_TEMP,
    NA_MODE_AWAY,
    NA_MODE_FROSTGUARD,
    NA_MODE_HOME,
    NA_MODE_MANUAL,
    NA_MODE_MAX,
    NA_MODE_OFF,
    NA_MODE_SCHEDULE,
    PRESET_AWAY,
    PRESET_BOOST,
    PRESET_FROST_GUARD,
    PRESET_SCHEDULE,
    PRESETS,
    TEMP_STEP,
)
from .coordinator import NetatmoEnergyConfigEntry, NetatmoEnergyCoordinator
from .entity import NetatmoEnergyRoomEntity, valves_in
from .schedules import async_edit_schedule, set_zone_temperature
from .select import async_switch_schedule

PARALLEL_UPDATES = 1

# Used when switching to HEAT from a room that has no usable setpoint
# (e.g. it was "off" at 7 C). A plain comfort value; adjust after testing.
DEFAULT_HEAT_TEMP = 20.0

# HA 2026.11 renamed the climate temperature attributes to native_* (the old
# names stop working in 2027.11). The test package only ships HA 2026.10 so
# far, so we pick the names at import time and support both. Drop the old
# branch once tests run on 2026.11+.
_NATIVE_TEMPS = hasattr(ClimateEntity, "native_temperature_unit")
_UNIT_ATTR = (
    "_attr_native_temperature_unit" if _NATIVE_TEMPS else "_attr_temperature_unit"
)
_CURRENT_ATTR = (
    "_attr_native_current_temperature" if _NATIVE_TEMPS else "_attr_current_temperature"
)
_TARGET_ATTR = (
    "_attr_native_target_temperature" if _NATIVE_TEMPS else "_attr_target_temperature"
)

# Home-level modes that a room in "schedule" follows.
_HOME_MODE_TO_PRESET = {
    NA_MODE_AWAY: PRESET_AWAY,
    NA_MODE_FROSTGUARD: PRESET_FROST_GUARD,
}


def resolve_state(
    room_mode: str | None,
    home_mode: str | None,
    target: float | None,
) -> tuple[HVACMode, str | None]:
    """Map Netatmo room/home modes to (hvac_mode, preset).

    NRV valves have no real "off": Netatmo (and the official app) treat a
    manual setpoint at the minimum as off, so we do the same.
    """
    if room_mode == NA_MODE_MAX:
        return HVACMode.HEAT, PRESET_BOOST
    if room_mode == NA_MODE_MANUAL:
        if target is not None and target <= MIN_TEMP:
            return HVACMode.OFF, None
        return HVACMode.HEAT, None
    if room_mode == NA_MODE_OFF:
        return HVACMode.OFF, None
    if room_mode == NA_MODE_FROSTGUARD:
        return HVACMode.AUTO, PRESET_FROST_GUARD
    if room_mode == NA_MODE_AWAY:
        return HVACMode.AUTO, PRESET_AWAY
    # "schedule", "home" or unknown: the room follows the home.
    return HVACMode.AUTO, _HOME_MODE_TO_PRESET.get(home_mode or "", PRESET_SCHEDULE)


def resolve_action(
    hvac_mode: HVACMode,
    heating_power_request: int | None,
    open_window: bool | None,
) -> HVACAction:
    """Derive hvac_action from the valve's heating demand.

    There is no boiler behind the relay on district heating, so
    boiler_status says nothing; heating_power_request (NRV only) does.
    """
    if hvac_mode == HVACMode.OFF:
        return HVACAction.OFF
    if open_window:
        return HVACAction.IDLE
    if heating_power_request:
        return HVACAction.HEATING
    return HVACAction.IDLE


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NetatmoEnergyConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create one climate entity per room with at least one valve."""
    coordinator = entry.runtime_data
    async_add_entities(
        NetatmoEnergyClimate(coordinator, room)
        for home in coordinator.homes
        for room in home.rooms.values()
        if valves_in(room)
    )


class NetatmoEnergyClimate(NetatmoEnergyRoomEntity, ClimateEntity):
    """A Netatmo room as a thermostat."""

    _attr_name = None
    _attr_translation_key = "room"
    _attr_hvac_modes = [HVACMode.AUTO, HVACMode.HEAT, HVACMode.OFF]
    _attr_preset_modes = list(PRESETS)
    _attr_min_temp = MIN_TEMP
    _attr_max_temp = MAX_TEMP
    _attr_target_temperature_step = TEMP_STEP
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.PRESET_MODE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )

    def __init__(self, coordinator: NetatmoEnergyCoordinator, room: Room) -> None:
        """Initialize the climate entity."""
        super().__init__(coordinator, room)
        self._attr_unique_id = f"{room.home.entity_id}-{room.entity_id}-climate"
        setattr(self, _UNIT_ATTR, UnitOfTemperature.CELSIUS)
        self._update_attrs()

    @callback
    @override
    def _handle_coordinator_update(self) -> None:
        self._update_attrs()
        super()._handle_coordinator_update()

    @callback
    def _update_attrs(self) -> None:
        room = self.room
        setattr(self, _CURRENT_ATTR, room.therm_measured_temperature)
        setattr(self, _TARGET_ATTR, room.therm_setpoint_temperature)
        self._attr_hvac_mode, self._attr_preset_mode = resolve_state(
            room.therm_setpoint_mode,
            room.home.therm_mode,
            room.therm_setpoint_temperature,
        )
        self._attr_hvac_action = resolve_action(
            self._attr_hvac_mode, room.heating_power_request, room.open_window
        )
        end = room.therm_setpoint_end_time
        self._attr_extra_state_attributes = {
            "heating_power_request": room.heating_power_request,
            "open_window": room.open_window,
            "anticipating": room.anticipating,
            "netatmo_setpoint_mode": room.therm_setpoint_mode,
            "setpoint_end_time": (
                datetime.fromtimestamp(end, UTC).isoformat() if end else None
            ),
        }

    # --- Commands -----------------------------------------------------------

    @override
    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Manual setpoint; Netatmo applies the home's default duration."""
        if (temp := kwargs.get(ATTR_TEMPERATURE)) is None:
            return
        temp = min(max(float(temp), MIN_TEMP), MAX_TEMP)
        await self._async_write(
            self.room.async_therm_set(NA_MODE_MANUAL, temp), NA_MODE_MANUAL, temp
        )

    @override
    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """AUTO follows the schedule, HEAT is manual, OFF is manual at minimum."""
        if hvac_mode == HVACMode.AUTO:
            await self._async_write(
                self.room.async_therm_set(NA_MODE_HOME), NA_MODE_SCHEDULE, None
            )
        elif hvac_mode == HVACMode.HEAT:
            current = self.room.therm_setpoint_temperature
            temp = current if current and current > MIN_TEMP else DEFAULT_HEAT_TEMP
            await self._async_write(
                self.room.async_therm_set(NA_MODE_MANUAL, temp), NA_MODE_MANUAL, temp
            )
        elif hvac_mode == HVACMode.OFF:
            await self._async_write(
                self.room.async_therm_set(NA_MODE_MANUAL, MIN_TEMP),
                NA_MODE_MANUAL,
                MIN_TEMP,
            )
        else:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unsupported_hvac_mode",
                translation_placeholders={"mode": str(hvac_mode)},
            )

    @override
    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Boost is per room; away and frost guard apply to the whole home."""
        home = self.room.home
        if preset_mode == PRESET_BOOST:
            await self._async_write(
                self.room.async_therm_set(NA_MODE_MAX), NA_MODE_MAX, None
            )
        elif preset_mode == PRESET_SCHEDULE:
            if home.therm_mode != NA_MODE_SCHEDULE:
                await self._async_call(home.async_set_thermmode(NA_MODE_SCHEDULE))
                home.therm_mode = NA_MODE_SCHEDULE
            await self._async_write(
                self.room.async_therm_set(NA_MODE_HOME), NA_MODE_SCHEDULE, None
            )
        elif preset_mode in (PRESET_AWAY, PRESET_FROST_GUARD):
            mode = NA_MODE_AWAY if preset_mode == PRESET_AWAY else NA_MODE_FROSTGUARD
            await self._async_call(home.async_set_thermmode(mode))
            home.therm_mode = mode
            self.coordinator.async_update_listeners()
            self.coordinator.async_schedule_confirm()
        else:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unsupported_preset",
                translation_placeholders={"preset": preset_mode},
            )

    @override
    async def async_turn_on(self) -> None:
        """Turn on = follow the schedule."""
        await self.async_set_hvac_mode(HVACMode.AUTO)

    @override
    async def async_turn_off(self) -> None:
        """Turn off = manual at the minimum setpoint."""
        await self.async_set_hvac_mode(HVACMode.OFF)

    # --- Actions (services.py) ------------------------------------------------

    async def async_service_set_schedule(self, schedule_name: str) -> None:
        """Switch the home's heating schedule (affects every room)."""
        await async_switch_schedule(self.room.home, schedule_name)
        self.coordinator.async_update_listeners()
        self.coordinator.async_schedule_confirm()

    async def async_service_set_preset_with_end(
        self, preset_mode: str, end_datetime: datetime
    ) -> None:
        """Away / frost guard for the home, or boost for the room, until a time."""
        end = _end_timestamp(end_datetime)
        if preset_mode == PRESET_BOOST:
            await self._async_write(
                self.room.async_therm_set(NA_MODE_MAX, end_time=end),
                NA_MODE_MAX,
                None,
                end,
            )
            return
        mode = NA_MODE_AWAY if preset_mode == PRESET_AWAY else NA_MODE_FROSTGUARD
        home = self.room.home
        await self._async_call(home.async_set_thermmode(mode, end_time=end))
        home.therm_mode = mode
        self.coordinator.async_update_listeners()
        self.coordinator.async_schedule_confirm()

    async def async_service_set_temperature_with_end(
        self, target_temperature: float, end_datetime: datetime
    ) -> None:
        """Manual setpoint until a given time."""
        end = _end_timestamp(end_datetime)
        await self._async_write(
            self.room.async_therm_set(NA_MODE_MANUAL, target_temperature, end),
            NA_MODE_MANUAL,
            target_temperature,
            end,
        )

    async def async_service_set_temperature_with_period(
        self, target_temperature: float, time_period: timedelta
    ) -> None:
        """Manual setpoint for a duration."""
        await self.async_service_set_temperature_with_end(
            target_temperature, dt_util.utcnow() + time_period
        )

    async def async_service_set_schedule_temperature(
        self, zone_name: str, temperature: float, schedule_name: str | None = None
    ) -> None:
        """Change this room's temperature in one zone of a schedule."""
        room_id = self.room.entity_id
        await async_edit_schedule(
            self.coordinator.auth,
            self.room.home.entity_id,
            schedule_name,
            lambda schedule: set_zone_temperature(
                schedule, zone_name, room_id, temperature
            ),
        )
        self.coordinator.async_refresh_topology_soon()

    async def async_service_clear_temperature_setting(self) -> None:
        """Drop the manual setpoint; the room follows the home again."""
        await self._async_write(
            self.room.async_therm_set(NA_MODE_HOME), NA_MODE_SCHEDULE, None
        )

    # --- Helpers ------------------------------------------------------------

    async def _async_call(self, call: Awaitable[Any]) -> None:
        try:
            await call
        except ApiError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="api_error",
                translation_placeholders={"error": str(err)},
            ) from err

    async def _async_write(
        self,
        call: Awaitable[Any],
        mode: str,
        temp: float | None,
        end: int | None = None,
    ) -> None:
        """Send a room write, update optimistically, then confirm from Netatmo."""
        await self._async_call(call)
        self.room.therm_setpoint_mode = mode
        if temp is not None:
            self.room.therm_setpoint_temperature = temp
        if end is not None:
            self.room.therm_setpoint_end_time = end
        self._update_attrs()
        self.async_write_ha_state()
        self.coordinator.async_schedule_confirm()


def _end_timestamp(end: datetime) -> int:
    """Return a Netatmo end time; naive datetimes are Home Assistant local time."""
    if end.tzinfo is None:
        end = end.replace(tzinfo=dt_util.get_default_time_zone())
    timestamp = int(end.timestamp())
    if timestamp <= int(dt_util.utcnow().timestamp()):
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="end_in_past",
            translation_placeholders={"end": end.isoformat()},
        )
    return timestamp
