"""OBI Energy Tracker integration."""

from __future__ import annotations

from datetime import datetime

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval

from .api import OBIEnergyApi
from .const import CONF_TOKEN, HISTORY_IMPORT_INTERVAL, PLATFORMS
from .coordinator import OBIEnergyConfigEntry, OBIEnergyCoordinator
from .statistics import OBIHistoryImporter


async def async_setup_entry(hass: HomeAssistant, entry: OBIEnergyConfigEntry) -> bool:
    """Set up OBI Energy Tracker from a config entry."""

    def _save_token(token: dict) -> None:
        hass.config_entries.async_update_entry(
            entry,
            data={**entry.data, CONF_TOKEN: token},
        )

    api = OBIEnergyApi(
        async_get_clientsession(hass),
        entry.data[CONF_TOKEN],
        token_update_callback=_save_token,
    )
    coordinator = OBIEnergyCoordinator(hass, entry, api)
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    importer = OBIHistoryImporter(hass, coordinator)

    @callback
    def _async_schedule_import(_now: datetime | None = None) -> None:
        entry.async_create_background_task(
            hass, importer.async_import(), "obi_energy_tracker history import"
        )

    _async_schedule_import()
    entry.async_on_unload(
        async_track_time_interval(
            hass, _async_schedule_import, HISTORY_IMPORT_INTERVAL
        )
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: OBIEnergyConfigEntry) -> bool:
    """Unload an OBI Energy Tracker config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
