"""Run the config entry, coordinator and platforms in Home Assistant."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import CONF_EMAIL, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.obi_energy_tracker.api import (
    OBIEnergyAuthError,
    OBIEnergyConnectionError,
)
from custom_components.obi_energy_tracker.const import (
    CONF_ACCOUNT_ID,
    CONF_TOKEN,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
)
from custom_components.obi_energy_tracker.diagnostics import (
    async_get_config_entry_diagnostics,
)

API = "custom_components.obi_energy_tracker.api.OBIEnergyApi"


def _sensor(sensor_id: str, name: str) -> dict[str, Any]:
    return {
        "id": sensor_id,
        "displayName": name,
        "model": "ET-1",
        "batteryLevel": 87,
        "isOnline": True,
    }


def _user(*sensors: dict[str, Any]) -> dict[str, Any]:
    return {
        "ecomId": "account-1",
        "givenName": "Test",
        "email": "user@example.com",
        "bridge": {"id": "bridge-1", "sensors": list(sensors)},
    }


def _records(*pairs: tuple[str, float]) -> list[dict[str, Any]]:
    return [{"time": time, "value": value} for time, value in pairs]


IMPORTS = {
    "s1": _records(
        ("2026-09-24T10:00:00Z", 1_000_000), ("2026-09-24T10:05:00Z", 1_000_100)
    )
}
EXPORTS = {
    "s1": _records(("2026-09-24T10:00:00Z", 5000), ("2026-09-24T10:05:00Z", 5000))
}


@pytest.fixture
def entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="account-1",
        data={
            CONF_EMAIL: "user@example.com",
            CONF_ACCOUNT_ID: "account-1",
            CONF_TOKEN: {"access_token": "a", "refresh_token": "r"},
        },
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
def mock_api():
    with (
        patch(f"{API}.async_get_user", new_callable=AsyncMock) as get_user,
        patch(
            f"{API}.async_get_all_measure_records", new_callable=AsyncMock
        ) as get_records,
        patch(f"{API}.async_get_measure_records", new=AsyncMock(return_value={})),
    ):
        get_user.return_value = _user(_sensor("s1", "Meter"))
        get_records.return_value = (IMPORTS, EXPORTS)
        yield get_user, get_records


def _entity_id(hass: HomeAssistant, unique_id: str) -> str:
    domain = "binary_sensor" if unique_id.endswith("_online") else "sensor"
    entity_id = er.async_get(hass).async_get_entity_id(domain, DOMAIN, unique_id)
    assert entity_id is not None, unique_id
    return entity_id


async def test_setup_creates_entities(hass, entry, mock_api) -> None:
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert entry.state is ConfigEntryState.LOADED

    consumption = hass.states.get(_entity_id(hass, "s1_energy_import"))
    assert float(consumption.state) == 1000.1
    assert consumption.attributes["unit_of_measurement"] == "kWh"
    assert consumption.attributes["state_class"] == "total_increasing"

    # 100 Wh over five minutes = 1200 W.
    power = hass.states.get(_entity_id(hass, "s1_power_import"))
    assert float(power.state) == 1200.0
    assert power.attributes["unit_of_measurement"] == "W"
    assert float(hass.states.get(_entity_id(hass, "s1_power_export")).state) == 0.0

    assert float(hass.states.get(_entity_id(hass, "s1_battery")).state) == 87
    assert hass.states.get(_entity_id(hass, "s1_online")).state == "on"

    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED


async def test_newly_paired_sensor_is_added_without_reload(
    hass, entry, mock_api
) -> None:
    get_user, get_records = mock_api
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    get_user.return_value = _user(_sensor("s1", "Meter"), _sensor("s2", "Garage"))
    get_records.return_value = (
        {**IMPORTS, "s2": _records(("2026-09-24T10:05:00Z", 2000))},
        EXPORTS,
    )
    async_fire_time_changed(hass, dt_util.utcnow() + DEFAULT_UPDATE_INTERVAL)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert float(hass.states.get(_entity_id(hass, "s2_energy_import")).state) == 2.0
    # One record is not enough to derive power, and export has no data.
    assert (
        hass.states.get(_entity_id(hass, "s2_power_import")).state == STATE_UNAVAILABLE
    )
    assert hass.states.get(_entity_id(hass, "s2_online")).state == "on"
    registry = er.async_get(hass)
    assert (
        len(er.async_entries_for_config_entry(registry, entry.entry_id)) == 12
    )


async def test_connection_error_retries_setup(hass, entry, mock_api) -> None:
    mock_api[0].side_effect = OBIEnergyConnectionError("down")
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_auth_error_starts_reauth(hass, entry, mock_api) -> None:
    mock_api[0].side_effect = OBIEnergyAuthError("expired")
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [flow["context"]["source"] for flow in flows] == [SOURCE_REAUTH]


async def test_diagnostics_redact_tokens(hass, entry, mock_api) -> None:
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    assert diagnostics["entry"][CONF_TOKEN] == "**REDACTED**"
    assert diagnostics["entry"][CONF_EMAIL] == "**REDACTED**"
    assert diagnostics["sensors"]["s1"]["import_power_w"] == 1200.0
