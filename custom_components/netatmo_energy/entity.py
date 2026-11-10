"""Base entities and device info for NET-TRV.

Device tree in Home Assistant:

    Relay (NAPlug, hub; area = the Netatmo room it sits in)
    └── Room (one per Netatmo room WITH a valve; holds the climate entity and,
        from step 1b, the diagnostics of the valve(s) in that room)
    Public weather (service device, nearby stations; 0.7.0)

Valves are not separate devices: with one valve per room the room *is* the
valve for the user, and separate devices would leave rooms or valves without
entities.
"""
# VERSION = "0.7.0"

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from pyatmo.home import Home
from pyatmo.modules.module import Module
from pyatmo.room import Room

from .const import DOMAIN, GATEWAY_TYPES, MANUFACTURER, VALVE_TYPES
from .coordinator import NetatmoEnergyCoordinator

MODEL_RELAY = "Thermostat relay"
MODEL_VALVE = "Smart radiator valve"
MODEL_ROOM = "Room"


def relay_of(home: Home) -> Module | None:
    """Return the home's relay module, if any."""
    return next(
        (m for m in home.modules.values() if m.device_type in GATEWAY_TYPES), None
    )


def valves_in(room: Room) -> list[Module]:
    """Return the radiator valves in a room."""
    return [m for m in room.modules.values() if m.device_type in VALVE_TYPES]


def room_of(home: Home, module: Module) -> Room | None:
    """Return the room a module is assigned to in the Netatmo app."""
    return next(
        (room for room in home.rooms.values() if module.entity_id in room.modules),
        None,
    )


def room_identifier(room: Room) -> str:
    """Return the device identifier used for a room."""
    return f"{room.home.entity_id}-{room.entity_id}"


def weather_identifier(entry: ConfigEntry) -> str:
    """Return the device identifier of the public weather service device."""
    return f"{entry.entry_id}-public_weather"


def weather_device_info(entry: ConfigEntry) -> DeviceInfo:
    """Device info for public weather (a service, not hardware)."""
    return DeviceInfo(
        identifiers={(DOMAIN, weather_identifier(entry))},
        entry_type=DeviceEntryType.SERVICE,
        manufacturer=MANUFACTURER,
        model="Public weather stations",
        translation_key="public_weather",
    )


def _firmware(module: Module) -> str | None:
    firmware = getattr(module, "firmware_revision", None)
    return None if firmware is None else str(firmware)


def relay_device_info(relay: Module, home: Home) -> DeviceInfo:
    """Device info for the relay (hub)."""
    info = DeviceInfo(
        identifiers={(DOMAIN, relay.entity_id)},
        manufacturer=MANUFACTURER,
        model=MODEL_RELAY,
        model_id=str(relay.device_type),
        name=relay.name,
        serial_number=relay.entity_id,
    )
    if (firmware := _firmware(relay)) is not None:
        info["sw_version"] = firmware
    if (room := room_of(home, relay)) is not None:
        # Only used when the device is first created; never overrides the user.
        info["suggested_area"] = room.name
    return info


def room_device_info(room: Room, via_device_id: str | None = None) -> DeviceInfo:
    """Device info for a room with valve(s).

    No ``suggested_area``: the room device is named after the room, and HA
    2026.10+ prefixes new entity ids with the area, which would give
    ``climate.stuen_stuen``. The user assigns areas.
    With exactly one valve the room device carries the valve's id and
    firmware, so it reads as the physical valve in the device page.
    ``via_device_id`` is the relay's device registry id (HA 2026.10+).
    """
    info = DeviceInfo(
        identifiers={(DOMAIN, room_identifier(room))},
        manufacturer=MANUFACTURER,
        name=room.name,
    )
    valves = valves_in(room)
    if len(valves) == 1:
        valve = valves[0]
        info["model"] = MODEL_VALVE
        info["model_id"] = str(valve.device_type)
        info["serial_number"] = valve.entity_id
        if (firmware := _firmware(valve)) is not None:
            info["sw_version"] = firmware
    else:
        info["model"] = f"{MODEL_ROOM} ({len(valves)} valves)"
    if via_device_id is not None:
        info["via_device_id"] = via_device_id
    return info


class NetatmoEnergyRoomEntity(CoordinatorEntity[NetatmoEnergyCoordinator]):
    """Base for entities that belong to a room device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: NetatmoEnergyCoordinator, room: Room) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self.room = room
        self._attr_device_info = room_device_info(
            room, coordinator.relay_device_ids.get(room.home.entity_id)
        )

    @property
    def available(self) -> bool:
        """Unavailable when Netatmo says the room cannot be reached."""
        return super().available and self.room.reachable is not False

    @property
    def coordinator_available(self) -> bool:
        """Return True when the last Netatmo poll succeeded."""
        return self.coordinator.last_update_success


class NetatmoEnergyValveEntity(NetatmoEnergyRoomEntity):
    """Base for entities describing one valve, shown on its room device.

    With several valves in a room the entity name gets the valve's name, so
    "Battery" becomes "Battery Stue Varme".
    """

    def __init__(
        self,
        coordinator: NetatmoEnergyCoordinator,
        room: Room,
        valve: Module,
        several_valves: bool,
        description: EntityDescription,
    ) -> None:
        """Initialize the entity."""
        super().__init__(coordinator, room)
        self.valve = valve
        self.entity_description = description
        self._attr_unique_id = f"{valve.entity_id}-{description.key}"
        if several_valves:
            self._attr_translation_key = (
                f"{description.translation_key or description.key}_multi"
            )
            self._attr_translation_placeholders = {"valve": valve.name}

    @property
    def available(self) -> bool:
        """Unavailable while Netatmo cannot reach the valve."""
        return self.coordinator_available and self.valve.reachable is not False


class NetatmoEnergyRelayEntity(CoordinatorEntity[NetatmoEnergyCoordinator]):
    """Base for entities on the relay device."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: NetatmoEnergyCoordinator, home: Home, relay: Module
    ) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self.home = home
        self.relay = relay
        self._attr_device_info = relay_device_info(relay, home)

    @property
    def coordinator_available(self) -> bool:
        """Return True when the last Netatmo poll succeeded."""
        return self.coordinator.last_update_success

    @property
    def available(self) -> bool:
        """Unavailable while Netatmo reports the relay unreachable."""
        return self.coordinator_available and self.relay.reachable is not False
