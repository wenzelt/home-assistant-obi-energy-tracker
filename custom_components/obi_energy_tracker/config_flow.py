"""Config flow for OBI Energy Tracker."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import SOURCE_REAUTH
from homeassistant.const import CONF_EMAIL
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    OBIEnergyApi,
    OBIEnergyAuthError,
    OBIEnergyConnectionError,
    OBIEnergyError,
)
from .auth import OBIAuthError, OBIConnectionError, OBIInvalidOTP, OBIPasswordlessAuth
from .const import CONF_ACCOUNT_ID, CONF_TOKEN, DOMAIN

_LOGGER = logging.getLogger(__name__)
CONF_OTP = "otp"


class OBIEnergyConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle an OBI Energy Tracker config flow."""

    VERSION = 1
    MINOR_VERSION = 1

    def __init__(self) -> None:
        self._email: str | None = None
        self._auth: OBIPasswordlessAuth | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Start passwordless authentication."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self._email = str(user_input[CONF_EMAIL]).strip()
            self._auth = OBIPasswordlessAuth(async_get_clientsession(self.hass))
            try:
                await self._auth.async_start(self._email)
            except OBIConnectionError:
                errors["base"] = "cannot_connect"
            except OBIAuthError:
                errors["base"] = "auth_start_failed"
            else:
                return await self.async_step_otp()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_EMAIL): str}),
            errors=errors,
        )

    async def async_step_otp(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Accept the six-character email OTP."""
        errors: dict[str, str] = {}
        if user_input is not None and self._auth and self._email:
            try:
                token = await self._auth.async_finish(str(user_input[CONF_OTP]).strip())
                api = OBIEnergyApi(async_get_clientsession(self.hass), token)
                user = await api.async_get_user()
            except OBIInvalidOTP:
                errors["base"] = "invalid_otp"
            except OBIConnectionError as err:
                _LOGGER.warning(
                    "Connection failed while completing OBI passwordless login: %s",
                    err,
                    exc_info=True,
                )
                errors["base"] = "cannot_connect"
            except OBIEnergyConnectionError as err:
                _LOGGER.warning(
                    "OBI login completed, but the Energy API connection failed: %s",
                    err,
                    exc_info=True,
                )
                errors["base"] = "cannot_connect"
            except OBIAuthError as err:
                _LOGGER.warning("OBI authentication failed: %s", err, exc_info=True)
                errors["base"] = "invalid_auth"
            except OBIEnergyAuthError as err:
                _LOGGER.warning("OBI Energy API rejected the token: %s", err)
                errors["base"] = "invalid_auth"
            except OBIEnergyError as err:
                _LOGGER.warning("OBI Energy API setup failed: %s", err, exc_info=True)
                errors["base"] = "api_error"
            else:
                return await self._async_complete(self._email, token, user)

        return self.async_show_form(
            step_id="otp",
            data_schema=vol.Schema({vol.Required(CONF_OTP): str}),
            errors=errors,
            description_placeholders={"email": self._email or ""},
        )

    async def _async_complete(
        self, email: str, token: dict[str, Any], user: dict[str, Any]
    ) -> config_entries.ConfigFlowResult:
        account_id = str(user.get("ecomId") or email)
        await self.async_set_unique_id(account_id)
        data = {
            CONF_EMAIL: email,
            CONF_ACCOUNT_ID: account_id,
            CONF_TOKEN: token,
        }

        if self.source == SOURCE_REAUTH:
            self._abort_if_unique_id_mismatch(reason="wrong_account")
            return self.async_update_reload_and_abort(
                self._get_reauth_entry(),
                data_updates=data,
            )

        self._abort_if_unique_id_configured()
        title = str(user.get("givenName") or email)
        return self.async_create_entry(title=title, data=data)

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> config_entries.ConfigFlowResult:
        """Start reauthentication for an expired token."""
        self._email = str(entry_data.get(CONF_EMAIL, ""))
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Confirm sending a new OTP to the saved email address."""
        errors: dict[str, str] = {}
        if user_input is not None and self._email:
            self._auth = OBIPasswordlessAuth(async_get_clientsession(self.hass))
            try:
                await self._auth.async_start(self._email)
            except OBIConnectionError:
                errors["base"] = "cannot_connect"
            except OBIAuthError:
                errors["base"] = "auth_start_failed"
            else:
                return await self.async_step_otp()

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({}),
            errors=errors,
            description_placeholders={"email": self._email or ""},
        )
