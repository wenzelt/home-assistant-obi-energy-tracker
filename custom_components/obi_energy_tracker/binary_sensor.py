"""Binary sensor platform for OBI Energy Tracker."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import OBIEnergyConfigEntry, OBIEnergyCoordinator
from .entity import OBIEnergyEntity, async_setup_sensor_entities

PARALLEL_UPDATES = 0

ONLINE_DESCRIPTION = BinarySensorEntityDescription(
    key="online",
    translation_key="online",
    device_class=BinarySensorDeviceClass.CONNECTIVITY,
    entity_category=EntityCategory.DIAGNOSTIC,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: OBIEnergyConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up OBI connectivity sensors."""
    async_setup_sensor_entities(
        entry,
        async_add_entities,
        lambda coordinator, sensor_id: [OBIOnlineBinarySensor(coordinator, sensor_id)],
    )


class OBIOnlineBinarySensor(OBIEnergyEntity, BinarySensorEntity):
    """Whether the OBI meter sensor is online."""

    entity_description = ONLINE_DESCRIPTION

    def __init__(self, coordinator: OBIEnergyCoordinator, sensor_id: str) -> None:
        super().__init__(coordinator, sensor_id)
        self._attr_unique_id = f"{sensor_id}_online"

    @property
    def is_on(self) -> bool | None:
        value = self.coordinator.data.readings[self._sensor_id].sensor.get("isOnline")
        return value if isinstance(value, bool) else None
