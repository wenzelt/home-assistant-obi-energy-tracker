"""DataUpdateCoordinator for OBI Energy Tracker."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    OBIEnergyApi,
    OBIEnergyAuthError,
    OBIEnergyConnectionError,
    OBIEnergyError,
)
from .const import DEFAULT_UPDATE_INTERVAL, DOMAIN
from .util import parse_obi_timestamp, wh_to_kwh

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class OBIReading:
    """Latest values for one paired OBI sensor."""

    sensor: dict[str, Any]
    import_wh: float | None
    export_wh: float | None
    import_kwh: float | None
    export_kwh: float | None
    import_time: Any
    export_time: Any


@dataclass(slots=True)
class OBIData:
    """Coordinator data model."""

    user: dict[str, Any]
    bridge: dict[str, Any]
    readings: dict[str, OBIReading]


class OBIEnergyCoordinator(DataUpdateCoordinator[OBIData]):
    """Coordinate OBI cloud polling."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        api: OBIEnergyApi,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            config_entry=entry,
            update_interval=DEFAULT_UPDATE_INTERVAL,
        )
        self.api = api

    async def _async_update_data(self) -> OBIData:
        try:
            user = await self.api.async_get_user()
            bridge = user.get("bridge")
            if not isinstance(bridge, dict) or not bridge.get("id"):
                raise UpdateFailed("No OBI Energy Tracker bridge is paired to this account")

            imported, exported = await self.api.async_get_all_latest_measures(
                str(bridge["id"])
            )
        except OBIEnergyAuthError as err:
            raise ConfigEntryAuthFailed("OBI authentication expired") from err
        except OBIEnergyConnectionError as err:
            raise UpdateFailed("Unable to reach OBI Energy Tracker cloud") from err
        except OBIEnergyError as err:
            raise UpdateFailed(str(err)) from err

        sensors = bridge.get("sensors")
        if not isinstance(sensors, list):
            sensors = []

        readings: dict[str, OBIReading] = {}
        for sensor in sensors:
            if not isinstance(sensor, dict) or not sensor.get("id"):
                continue
            sensor_id = str(sensor["id"])
            import_record = imported.get(sensor_id)
            export_record = exported.get(sensor_id)
            import_wh = (
                float(import_record["value"])
                if import_record and isinstance(import_record.get("value"), (int, float))
                else None
            )
            export_wh = (
                float(export_record["value"])
                if export_record and isinstance(export_record.get("value"), (int, float))
                else None
            )
            readings[sensor_id] = OBIReading(
                sensor=sensor,
                import_wh=import_wh,
                export_wh=export_wh,
                import_kwh=wh_to_kwh(import_wh),
                export_kwh=wh_to_kwh(export_wh),
                import_time=parse_obi_timestamp(
                    import_record.get("time") if import_record else None
                ),
                export_time=parse_obi_timestamp(
                    export_record.get("time") if export_record else None
                ),
            )

        return OBIData(user=user, bridge=bridge, readings=readings)
