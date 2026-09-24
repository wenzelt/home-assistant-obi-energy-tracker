"""Base entities for OBI Energy Tracker."""

from __future__ import annotations

from collections.abc import Callable, Iterable

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import OBIEnergyConfigEntry, OBIEnergyCoordinator


@callback
def async_setup_sensor_entities(
    entry: OBIEnergyConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
    entity_factory: Callable[[OBIEnergyCoordinator, str], Iterable[Entity]],
) -> None:
    """Add entities for every OBI sensor, including ones paired after setup."""
    coordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def _async_add_new() -> None:
        new_ids = [
            sensor_id
            for sensor_id in coordinator.data.readings
            if sensor_id not in known
        ]
        if not new_ids:
            return
        known.update(new_ids)
        async_add_entities(
            entity
            for sensor_id in new_ids
            for entity in entity_factory(coordinator, sensor_id)
        )

    _async_add_new()
    entry.async_on_unload(coordinator.async_add_listener(_async_add_new))


class OBIEnergyEntity(CoordinatorEntity[OBIEnergyCoordinator]):
    """Base entity tied to one OBI meter sensor."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: OBIEnergyCoordinator, sensor_id: str) -> None:
        super().__init__(coordinator)
        self._sensor_id = sensor_id
        sensor = coordinator.data.readings[sensor_id].sensor
        display_name = sensor.get("displayName") or sensor.get("label")
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, sensor_id)},
            manufacturer="OBI",
            name=display_name or "OBI Energy Tracker",
            model=sensor.get("model") or sensor.get("type") or "Energy Tracker Sensor",
            sw_version=sensor.get("firmwareVersion"),
            hw_version=sensor.get("hardwareVersion"),
        )

    @property
    def available(self) -> bool:
        return super().available and self._sensor_id in self.coordinator.data.readings
