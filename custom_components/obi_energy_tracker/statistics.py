"""Import OBI meter history into Home Assistant long-term statistics.

OBI keeps the meter history in the cloud, but Home Assistant only records
statistics from the moment an entity exists. This module backfills hourly
external statistics (``obi_energy_tracker:<sensor>_grid_consumption`` and
``..._grid_export``) from OBI's history endpoint, then keeps them current.
They can be selected in the Energy dashboard like any energy sensor.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import logging
import re
from typing import Any

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_last_statistics,
)
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .api import OBIEnergyError
from .const import DOMAIN, HISTORY_IMPORT_MAX_AGE, HISTORY_IMPORT_WINDOW
from .coordinator import OBIEnergyCoordinator
from .util import history_windows, hourly_energy, parse_obi_timestamp

_LOGGER = logging.getLogger(__name__)

# OBI measure -> (statistic suffix, name suffix)
MEASURES = {
    "energy": ("grid_consumption", "grid consumption"),
    "negative_energy": ("grid_export", "grid export"),
}


def statistic_id(sensor_id: str, suffix: str) -> str:
    """Build a valid external statistic id for one OBI sensor."""
    slug = re.sub(r"[^a-z0-9]+", "_", sensor_id.lower()).strip("_") or "sensor"
    return f"{DOMAIN}:{slug}_{suffix}"


@dataclass
class _Progress:
    """Where one statistic continues from."""

    metadata: StatisticMetaData
    after: datetime
    last_value_wh: float | None
    last_sum_wh: float
    imported_hours: int = 0


def _history_start(sensor: dict[str, Any], now: datetime) -> datetime:
    """First moment to backfill: when the sensor's data became visible."""
    earliest = now - HISTORY_IMPORT_MAX_AGE
    for key in ("dataVisibleSince", "claimedAt"):
        parsed = parse_obi_timestamp(sensor.get(key))
        if parsed is not None:
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            return max(parsed.astimezone(UTC), earliest)
    return earliest


class OBIHistoryImporter:
    """Backfill and extend hourly energy statistics from OBI's history."""

    def __init__(self, hass: HomeAssistant, coordinator: OBIEnergyCoordinator) -> None:
        self.hass = hass
        self.coordinator = coordinator
        self._lock = asyncio.Lock()
        # Hours already fetched in this run of Home Assistant. Without it, a
        # measure with no data (no grid export) would refetch a year every hour.
        self._checked_until: dict[str, datetime] = {}

    async def async_import(self) -> None:
        """Import all completed hours that are not in the recorder yet."""
        if self._lock.locked():
            return
        async with self._lock:
            try:
                for measure in MEASURES:
                    await self._async_import_measure(measure)
            except OBIEnergyError as err:
                _LOGGER.warning(
                    "Importing OBI energy history failed, retrying in an hour: %s",
                    err,
                )

    async def _async_last_statistic(self, stat_id: str) -> dict[str, Any] | None:
        result = await get_instance(self.hass).async_add_executor_job(
            get_last_statistics, self.hass, 1, stat_id, False, {"state", "sum"}
        )
        rows = result.get(stat_id)
        return dict(rows[0]) if rows else None

    async def _async_import_measure(self, measure: str) -> None:
        data = self.coordinator.data
        if data is None or not data.bridge.get("id"):
            return
        bridge_id = str(data.bridge["id"])
        suffix, label = MEASURES[measure]
        now = dt_util.utcnow()
        until = now.replace(minute=0, second=0, microsecond=0)

        progress: dict[str, _Progress] = {}
        for sensor_id, reading in data.readings.items():
            stat_id = statistic_id(sensor_id, suffix)
            sensor = reading.sensor
            name = sensor.get("displayName") or sensor.get("label") or "OBI"
            metadata = StatisticMetaData(
                has_sum=True,
                mean_type=StatisticMeanType.NONE,
                name=f"{name} {label}",
                source=DOMAIN,
                statistic_id=stat_id,
                unit_class="energy",
                unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
            )
            last = await self._async_last_statistic(stat_id)
            if last is None:
                start = _history_start(sensor, now)
                progress[sensor_id] = _Progress(
                    metadata,
                    after=start.replace(minute=0, second=0, microsecond=0),
                    last_value_wh=None,
                    last_sum_wh=0.0,
                )
            else:
                last_start = datetime.fromtimestamp(last["start"], UTC)
                state = last.get("state")
                progress[sensor_id] = _Progress(
                    metadata,
                    after=last_start + timedelta(hours=1),
                    last_value_wh=state * 1000 if state is not None else None,
                    last_sum_wh=(last.get("sum") or 0.0) * 1000,
                )

        for item in progress.values():
            checked = self._checked_until.get(item.metadata["statistic_id"])
            if checked is not None and checked > item.after:
                item.after = checked

        pending = [item for item in progress.values() if item.after < until]
        if not pending:
            return

        fetch_start = min(item.after for item in pending)
        for window in history_windows(fetch_start, until, HISTORY_IMPORT_WINDOW):
            records = await self.coordinator.api.async_get_measure_records(
                bridge_id, measure, duration=window
            )
            for sensor_id, item in progress.items():
                hours = hourly_energy(
                    records.get(sensor_id, []),
                    after=item.after,
                    until=until,
                    last_value_wh=item.last_value_wh,
                    last_sum_wh=item.last_sum_wh,
                )
                if not hours:
                    continue
                async_add_external_statistics(
                    self.hass,
                    item.metadata,
                    [
                        StatisticData(
                            start=hour.start,
                            state=hour.state_wh / 1000,
                            sum=hour.sum_wh / 1000,
                        )
                        for hour in hours
                    ],
                )
                newest = hours[-1]
                item.after = newest.last_time + timedelta(microseconds=1)
                item.last_value_wh = newest.state_wh
                item.last_sum_wh = newest.sum_wh
                item.imported_hours += len(hours)

        for item in progress.values():
            self._checked_until[item.metadata["statistic_id"]] = until
            if item.imported_hours:
                _LOGGER.info(
                    "Imported %s hours of OBI history into %s",
                    item.imported_hours,
                    item.metadata["statistic_id"],
                )
