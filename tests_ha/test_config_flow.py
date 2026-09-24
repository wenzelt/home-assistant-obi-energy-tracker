"""Run the passwordless login and reauth flows in Home Assistant."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_EMAIL
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.obi_energy_tracker.auth import OBIInvalidOTP
from custom_components.obi_energy_tracker.const import (
    CONF_ACCOUNT_ID,
    CONF_TOKEN,
    DOMAIN,
)

FLOW = "custom_components.obi_energy_tracker.config_flow"
TOKEN = {"access_token": "a", "refresh_token": "r"}
USER = {"ecomId": "account-1", "givenName": "Test"}


def _patch_login(user=USER):
    auth = patch(f"{FLOW}.OBIPasswordlessAuth", autospec=True)
    get_user = patch(
        f"{FLOW}.OBIEnergyApi.async_get_user", new=AsyncMock(return_value=user)
    )
    setup = patch(
        "custom_components.obi_energy_tracker.async_setup_entry", return_value=True
    )
    return auth, get_user, setup


async def test_user_flow_creates_entry(hass: HomeAssistant) -> None:
    auth, get_user, setup = _patch_login()
    with auth as auth_cls, get_user, setup:
        auth_cls.return_value.async_finish.side_effect = [OBIInvalidOTP, TOKEN]
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        assert result["step_id"] == "user"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_EMAIL: " user@example.com "}
        )
        assert result["step_id"] == "otp"
        auth_cls.return_value.async_start.assert_awaited_once_with("user@example.com")

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"otp": "WRONG1"}
        )
        assert result["errors"] == {"base": "invalid_otp"}

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"otp": "ABC123"}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Test"
    assert result["result"].unique_id == "account-1"
    assert result["data"] == {
        CONF_EMAIL: "user@example.com",
        CONF_ACCOUNT_ID: "account-1",
        CONF_TOKEN: TOKEN,
    }


async def test_reauth_updates_token(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="account-1",
        data={
            CONF_EMAIL: "user@example.com",
            CONF_ACCOUNT_ID: "account-1",
            CONF_TOKEN: {"access_token": "old"},
        },
    )
    entry.add_to_hass(hass)
    auth, get_user, setup = _patch_login()
    with auth as auth_cls, get_user, setup:
        auth_cls.return_value.async_finish.return_value = TOKEN
        result = await entry.start_reauth_flow(hass)
        assert result["step_id"] == "reauth_confirm"
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
        assert result["step_id"] == "otp"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"otp": "ABC123"}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_TOKEN] == TOKEN


async def test_reauth_with_other_account_is_rejected(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="account-1",
        data={CONF_EMAIL: "user@example.com", CONF_TOKEN: {"access_token": "old"}},
    )
    entry.add_to_hass(hass)
    auth, get_user, setup = _patch_login(user={"ecomId": "someone-else"})
    with auth as auth_cls, get_user, setup:
        auth_cls.return_value.async_finish.return_value = TOKEN
        result = await entry.start_reauth_flow(hass)
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"otp": "ABC123"}
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_account"
    assert entry.data[CONF_TOKEN] == {"access_token": "old"}
