"""Backfill OBI history into Home Assistant long-term statistics."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

from freezegun.api import FrozenDateTimeFactory
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.obi_energy_tracker.const import (
    CONF_ACCOUNT_ID,
    CONF_TOKEN,
    DOMAIN,
)
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import (
    list_statistic_ids,
    statistics_during_period,
)
from homeassistant.core import HomeAssistant

API = "custom_components.obi_energy_tracker.api.OBIEnergyApi"
SENSOR_ID = "550e8400-e29b-41d4-a716-446655440000"
CONSUMPTION = f"{DOMAIN}:550e8400_e29b_41d4_a716_446655440000_grid_consumption"
EXPORT = f"{DOMAIN}:550e8400_e29b_41d4_a716_446655440000_grid_export"

IMPORT_RECORDS = [
    {"time": "2026-10-10 09:00:00.000000000", "value": 1000},
    {"time": "2026-10-10 09:30:00.000000000", "value": 1100},
    {"time": "2026-10-10 10:15:00.000000000", "value": 1400},
    {"time": "2026-10-10 11:45:00.000000000", "value": 1500},
    {"time": "2026-10-10 12:10:00.000000000", "value": 1600},
]


def _user() -> dict[str, Any]:
    return {
        "ecomId": "account-1",
        "bridge": {
            "id": "bridge-1",
            "sensors": [
                {
                    "id": SENSOR_ID,
                    "displayName": "Meter",
                    "isOnline": True,
                    "dataVisibleSince": "2026-10-10T09:00:00.000Z",
                }
            ],
        },
    }


async def _hourly(hass: HomeAssistant, statistic_id: str) -> list[tuple]:
    rows = await get_instance(hass).async_add_executor_job(
        statistics_during_period,
        hass,
        datetime(2026, 10, 1, tzinfo=UTC),
        None,
        {statistic_id},
        "hour",
        None,
        {"state", "sum"},
    )
    return [
        (
            datetime.fromtimestamp(row["start"], UTC).hour,
            round(row["state"], 3),
            round(row["sum"], 3),
        )
        for row in rows.get(statistic_id, [])
    ]


async def test_history_is_backfilled_and_extended(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to("2026-10-10T12:30:00+00:00")
    records = {"energy": list(IMPORT_RECORDS), "negative_energy": []}
    requested: list[str] = []

    async def _get_records(bridge_id, measure, *, duration):
        requested.append(f"{measure} {duration}")
        return {SENSOR_ID: records[measure]}

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="account-1",
        data={
            CONF_ACCOUNT_ID: "account-1",
            CONF_TOKEN: {"access_token": "a", "refresh_token": "r"},
        },
    )
    entry.add_to_hass(hass)
    with (
        patch(f"{API}.async_get_user", new=AsyncMock(return_value=_user())),
        patch(
            f"{API}.async_get_all_measure_records",
            new=AsyncMock(return_value=({SENSOR_ID: IMPORT_RECORDS}, {})),
        ),
        patch(f"{API}.async_get_measure_records", side_effect=_get_records),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done(wait_background_tasks=True)
        await async_wait_recording_done(hass)

        # Fetched from when the sensor's data became visible; the open hour
        # (12:00) is left for the next run.
        assert requested == [
            "energy 2026-10-10T09:00:00Z/P7D",
            "negative_energy 2026-10-10T09:00:00Z/P7D",
        ]
        assert await _hourly(hass, CONSUMPTION) == [
            (9, 1.1, 0.1),
            (10, 1.4, 0.4),
            (11, 1.5, 0.5),
        ]
        assert await _hourly(hass, EXPORT) == []

        ids = await get_instance(hass).async_add_executor_job(
            list_statistic_ids, hass, {CONSUMPTION}
        )
        assert ids[0]["source"] == DOMAIN
        assert ids[0]["statistics_unit_of_measurement"] == "kWh"
        assert ids[0]["name"] == "Meter grid consumption"

        # An hour later only the new hour is fetched and appended.
        records["energy"].append(
            {"time": "2026-10-10 12:40:00.000000000", "value": 1700}
        )
        requested.clear()
        freezer.tick(timedelta(hours=1))
        async_fire_time_changed(hass)
        await hass.async_block_till_done(wait_background_tasks=True)
        await async_wait_recording_done(hass)

        # Export has no data, but is not refetched from the start either.
        assert requested == [
            "energy 2026-10-10T12:00:00Z/P7D",
            "negative_energy 2026-10-10T12:00:00Z/P7D",
        ]
        assert (await _hourly(hass, CONSUMPTION))[-1] == (12, 1.7, 0.7)
