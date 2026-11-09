"""Connectivity binary sensors for Netatmo Energy valves and the relay."""
# VERSION = "0.3.0"

from __future__ import annotations

from typing import Any, override

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.components.binary_sensor.const import BinarySensorDeviceClass
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from pyatmo.home import Home
from pyatmo.modules.module import Module

from .coordinator import NetatmoEnergyConfigEntry, NetatmoEnergyCoordinator
from .entity import (
    NetatmoEnergyRelayEntity,
    NetatmoEnergyValveEntity,
    relay_of,
    valves_in,
)

PARALLEL_UPDATES = 0

CONNECTIVITY = BinarySensorEntityDescription(
    key="connectivity",
    device_class=BinarySensorDeviceClass.CONNECTIVITY,
    entity_category=EntityCategory.DIAGNOSTIC,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NetatmoEnergyConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create a connectivity sensor per valve and one for the relay."""
    coordinator = entry.runtime_data
    entities: list[BinarySensorEntity] = []
    for home in coordinator.homes:
        for room in home.rooms.values():
            valves = valves_in(room)
            entities.extend(
                NetatmoValveConnectivity(
                    coordinator, room, valve, len(valves) > 1, CONNECTIVITY
                )
                for valve in valves
            )
        if (relay := relay_of(home)) is not None:
            entities.append(NetatmoRelayConnectivity(coordinator, home, relay))
            entities.append(NetatmoWebhookActive(coordinator, home, relay))
    async_add_entities(entities)


class NetatmoValveConnectivity(NetatmoEnergyValveEntity, BinarySensorEntity):
    """Whether Netatmo can reach the valve."""

    @property
    @override
    def available(self) -> bool:
        # Must stay available to be able to show "disconnected".
        return self.coordinator_available

    @property
    @override
    def is_on(self) -> bool | None:
        return self.valve.reachable


class NetatmoRelayConnectivity(NetatmoEnergyRelayEntity, BinarySensorEntity):
    """Whether Netatmo can reach the relay."""

    entity_description = CONNECTIVITY

    def __init__(
        self, coordinator: NetatmoEnergyCoordinator, home: Home, relay: Module
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, home, relay)
        self._attr_unique_id = f"{relay.entity_id}-connectivity"

    @property
    @override
    def available(self) -> bool:
        return self.coordinator_available

    @property
    @override
    def is_on(self) -> bool:
        # /homestatus often omits "reachable" for the relay itself; a
        # successful poll with a non-False value means it is online.
        return self.relay.reachable is not False


class NetatmoWebhookActive(NetatmoEnergyRelayEntity, BinarySensorEntity):
    """Whether the Netatmo webhook is registered and not removed by Netatmo."""

    _attr_translation_key = "webhook"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self, coordinator: NetatmoEnergyCoordinator, home: Home, relay: Module
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, home, relay)
        self._attr_unique_id = f"{relay.entity_id}-webhook"

    @property
    @override
    def available(self) -> bool:
        return True

    @property
    @override
    def is_on(self) -> bool:
        return self.coordinator.webhook.active

    @property
    @override
    def extra_state_attributes(self) -> dict[str, Any]:
        state = self.coordinator.webhook
        return {
            "events_received": state.events_received,
            "last_event": state.last_event_at.isoformat()
            if state.last_event_at
            else None,
            "rejected_signatures": state.rejected_signatures,
            "last_error": state.last_error,
        }
