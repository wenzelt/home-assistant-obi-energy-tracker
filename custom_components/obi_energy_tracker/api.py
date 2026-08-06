"""Async client for the OBI Energy Tracker Public API."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from http import HTTPStatus
import logging
import time
from typing import Any

from aiohttp import ClientError, ClientSession

from .const import (
    API_BASE_URL,
    CLIENT_ID,
    DEFAULT_HISTORY_DURATION,
    HISTORY_ACCEPT,
    TOKEN_EXPIRY_LEEWAY_SECONDS,
    TOKEN_URL,
    USER_ACCEPT,
)
from .util import latest_record

_LOGGER = logging.getLogger(__name__)


class OBIEnergyError(Exception):
    """Base exception for the OBI Energy API."""


class OBIEnergyAuthError(OBIEnergyError):
    """Raised when OBI credentials are invalid or expired."""


class OBIEnergyConnectionError(OBIEnergyError):
    """Raised when OBI cannot be reached."""


class OBIEnergyResponseError(OBIEnergyError):
    """Raised for malformed or unsuccessful API responses."""


async def _async_json_response(
    response: Any, *, context: str
) -> dict[str, Any]:
    """Decode JSON without requiring a standards-compliant Content-Type header."""
    try:
        payload = await response.json(content_type=None)
    except (ClientError, ValueError) as err:
        raise OBIEnergyResponseError(f"{context} returned invalid JSON") from err
    if not isinstance(payload, dict):
        raise OBIEnergyResponseError(f"{context} returned an unexpected JSON value")
    return payload


class OBIEnergyApi:
    """OBI Energy Tracker API client with refresh-token handling."""

    def __init__(
        self,
        session: ClientSession,
        token: Mapping[str, Any],
        token_update_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self._session = session
        self._token = dict(token)
        self._token_update_callback = token_update_callback
        self._refresh_lock = asyncio.Lock()

    @property
    def token(self) -> dict[str, Any]:
        """Return a copy of the current token."""
        return dict(self._token)

    def _token_expiring(self) -> bool:
        expires_at = self._token.get("expires_at")
        if expires_at is None:
            return False
        try:
            return float(expires_at) <= time.time() + TOKEN_EXPIRY_LEEWAY_SECONDS
        except (TypeError, ValueError):
            return True

    async def _async_refresh_token(self, *, force: bool = False) -> None:
        async with self._refresh_lock:
            if not force and not self._token_expiring():
                return
            refresh_token = self._token.get("refresh_token")
            if not refresh_token:
                raise OBIEnergyAuthError("OBI token expired without a refresh token")

            try:
                response = await self._session.post(
                    TOKEN_URL,
                    data={
                        "grant_type": "refresh_token",
                        "client_id": CLIENT_ID,
                        "refresh_token": refresh_token,
                    },
                )
            except ClientError as err:
                raise OBIEnergyConnectionError("Unable to refresh OBI token") from err

            if response.status in (HTTPStatus.BAD_REQUEST, HTTPStatus.UNAUTHORIZED):
                raise OBIEnergyAuthError("OBI refresh token was rejected")
            if response.status >= HTTPStatus.BAD_REQUEST:
                raise OBIEnergyConnectionError(
                    f"OBI token endpoint returned HTTP {response.status}"
                )

            payload = await _async_json_response(
                response, context="OBI token refresh endpoint"
            )
            if not payload.get("access_token"):
                raise OBIEnergyResponseError("Malformed token refresh response")

            updated = {**self._token, **dict(payload)}
            if "expires_in" in updated:
                updated["expires_in"] = int(updated["expires_in"])
                updated["expires_at"] = time.time() + updated["expires_in"]
            self._token = updated
            if self._token_update_callback:
                self._token_update_callback(dict(updated))

    async def _async_request_json(
        self,
        method: str,
        path: str,
        *,
        accept: str,
        params: list[tuple[str, str]] | None = None,
    ) -> dict[str, Any]:
        for attempt in range(2):
            if self._token_expiring():
                await self._async_refresh_token()

            access_token = self._token.get("access_token")
            if not access_token:
                raise OBIEnergyAuthError("OBI access token is missing")

            try:
                response = await self._session.request(
                    method,
                    f"{API_BASE_URL}{path}",
                    params=params,
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Accept": accept,
                    },
                )
            except ClientError as err:
                raise OBIEnergyConnectionError("Unable to contact OBI Energy API") from err

            if response.status == HTTPStatus.UNAUTHORIZED and attempt == 0:
                response.release()
                await self._async_refresh_token(force=True)
                continue
            if response.status in (HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN):
                response.release()
                raise OBIEnergyAuthError(
                    f"OBI Energy API rejected authentication ({response.status})"
                )
            if response.status >= HTTPStatus.BAD_REQUEST:
                response.release()
                raise OBIEnergyResponseError(
                    f"OBI Energy API returned HTTP {response.status} for {path}"
                )

            return await _async_json_response(
                response, context=f"OBI Energy API {path}"
            )

        raise OBIEnergyAuthError("OBI authentication failed after token refresh")

    async def async_get_user(self) -> dict[str, Any]:
        """Return the authenticated user and paired bridge/sensors."""
        return await self._async_request_json(
            "GET", "/users/me", accept=USER_ACCEPT
        )

    async def async_get_latest_measure(
        self,
        bridge_id: str,
        measure: str,
        *,
        duration: str = DEFAULT_HISTORY_DURATION,
    ) -> dict[str, dict[str, Any]]:
        """Return the latest record per device for one measure."""
        payload = await self._async_request_json(
            "GET",
            f"/historical-data/{bridge_id}/measures",
            accept=HISTORY_ACCEPT,
            params=[("duration", duration), ("measures", measure)],
        )
        devices = payload.get("devices")
        if not isinstance(devices, dict):
            raise OBIEnergyResponseError("Historical response did not contain devices")

        result: dict[str, dict[str, Any]] = {}
        for device_id, records in devices.items():
            if not isinstance(device_id, str) or not isinstance(records, list):
                continue
            record = latest_record(records)
            if record is not None:
                result[device_id] = record
        return result

    async def async_get_all_latest_measures(
        self, bridge_id: str
    ) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
        """Fetch import and export counters concurrently."""
        return await asyncio.gather(
            self.async_get_latest_measure(bridge_id, "energy"),
            self.async_get_latest_measure(bridge_id, "negative_energy"),
        )
