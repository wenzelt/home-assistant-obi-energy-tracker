"""Sensor platform for OBI Energy Tracker."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfEnergy, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import OBIEnergyConfigEntry, OBIEnergyCoordinator
from .entity import OBIEnergyEntity, async_setup_sensor_entities

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class OBIEnergySensorDescription(SensorEntityDescription):
    """Describe an OBI energy sensor."""

    value_key: str
    raw_key: str | None = None
    time_key: str | None = None


ENERGY_DESCRIPTIONS = (
    OBIEnergySensorDescription(
        key="energy_import",
        translation_key="energy_import",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=3,
        value_key="import_kwh",
        raw_key="import_wh",
        time_key="import_time",
    ),
    OBIEnergySensorDescription(
        key="energy_export",
        translation_key="energy_export",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=3,
        value_key="export_kwh",
        raw_key="export_wh",
        time_key="export_time",
    ),
)

# Average power over the interval between the two newest counter readings.
# OBI publishes cumulative counters only, so this is derived, not measured.
POWER_DESCRIPTIONS = (
    OBIEnergySensorDescription(
        key="power_import",
        translation_key="power_import",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        suggested_display_precision=0,
        value_key="import_power_w",
        time_key="import_time",
    ),
    OBIEnergySensorDescription(
        key="power_export",
        translation_key="power_export",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        suggested_display_precision=0,
        value_key="export_power_w",
        time_key="export_time",
    ),
)

BATTERY_DESCRIPTION = OBIEnergySensorDescription(
    key="battery",
    translation_key="battery",
    device_class=SensorDeviceClass.BATTERY,
    native_unit_of_measurement=PERCENTAGE,
    entity_category=EntityCategory.DIAGNOSTIC,
    value_key="batteryLevel",
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: OBIEnergyConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up OBI Energy Tracker sensors."""

    def _entities(
        coordinator: OBIEnergyCoordinator, sensor_id: str
    ) -> list[SensorEntity]:
        entities: list[SensorEntity] = [
            OBIMeterSensor(coordinator, sensor_id, description)
            for description in (*ENERGY_DESCRIPTIONS, *POWER_DESCRIPTIONS)
        ]
        entities.append(OBIBatterySensor(coordinator, sensor_id, BATTERY_DESCRIPTION))
        return entities

    async_setup_sensor_entities(entry, async_add_entities, _entities)


class OBIMeterSensor(OBIEnergyEntity, SensorEntity):
    """Import/export energy counter or derived power sensor."""

    entity_description: OBIEnergySensorDescription

    def __init__(
        self,
        coordinator: OBIEnergyCoordinator,
        sensor_id: str,
        description: OBIEnergySensorDescription,
    ) -> None:
        super().__init__(coordinator, sensor_id)
        self.entity_description = description
        self._attr_unique_id = f"{sensor_id}_{description.key}"

    @property
    def native_value(self) -> float | None:
        reading = self.coordinator.data.readings[self._sensor_id]
        return getattr(reading, self.entity_description.value_key)

    @property
    def available(self) -> bool:
        return super().available and self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        reading = self.coordinator.data.readings[self._sensor_id]
        attributes: dict[str, Any] = {}
        if self.entity_description.raw_key:
            attributes["raw_value_wh"] = getattr(
                reading, self.entity_description.raw_key
            )
        if self.entity_description.time_key:
            timestamp = getattr(reading, self.entity_description.time_key)
            attributes["last_measurement"] = (
                timestamp.isoformat() if timestamp is not None else None
            )
        return attributes


class OBIBatterySensor(OBIEnergyEntity, SensorEntity):
    """OBI sensor battery level."""

    entity_description: OBIEnergySensorDescription

    def __init__(
        self,
        coordinator: OBIEnergyCoordinator,
        sensor_id: str,
        description: OBIEnergySensorDescription,
    ) -> None:
        super().__init__(coordinator, sensor_id)
        self.entity_description = description
        self._attr_unique_id = f"{sensor_id}_{description.key}"

    @property
    def native_value(self) -> int | float | None:
        sensor = self.coordinator.data.readings[self._sensor_id].sensor
        value = sensor.get(self.entity_description.value_key)
        return value if isinstance(value, (int, float)) else None
