"""Base entities for OBI Energy Tracker."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import OBIEnergyCoordinator


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
