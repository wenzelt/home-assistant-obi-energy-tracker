"""Diagnostics support for OBI Energy Tracker."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.redact import async_redact_data

from .const import CONF_TOKEN, DOMAIN
from .coordinator import OBIEnergyCoordinator

TO_REDACT = {
    "email",
    "ecomId",
    "givenName",
    CONF_TOKEN,
    "access_token",
    "refresh_token",
    "id_token",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return redacted diagnostics for a config entry."""
    coordinator: OBIEnergyCoordinator = hass.data[DOMAIN][entry.entry_id]
    data = coordinator.data
    return async_redact_data(
        {
            "entry": dict(entry.data),
            "bridge": data.bridge,
            "sensor_count": len(data.readings),
            "sensors": {
                sensor_id: {
                    "sensor": reading.sensor,
                    "import_wh": reading.import_wh,
                    "export_wh": reading.export_wh,
                    "import_time": str(reading.import_time),
                    "export_time": str(reading.export_time),
                }
                for sensor_id, reading in data.readings.items()
            },
        },
        TO_REDACT,
    )
