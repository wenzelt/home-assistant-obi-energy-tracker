"""Tests for OBIEnergyApi against the published OBI Energy Tracker OpenAPI contract.

Endpoints, Accept media types, and response schemas mirror
``openapi-public.json`` (``UserV2DTO``, ``BridgeV2DTO``, ``SensorV2DTO``,
``MultiDeviceTimestreamRecordsDTO``, ``TimestreamRecordDTO``).
"""

from __future__ import annotations

import time

from aiohttp import ClientConnectionError
import pytest


from conftest import FakeResponse, FakeSession, import_submodule

api_module = import_submodule("api")
const = import_submodule("const")

OBIEnergyApi = api_module.OBIEnergyApi
OBIEnergyAuthError = api_module.OBIEnergyAuthError
OBIEnergyConnectionError = api_module.OBIEnergyConnectionError
OBIEnergyResponseError = api_module.OBIEnergyResponseError

USER_URL = f"{const.API_BASE_URL}/users/me"
BRIDGE_ID = "550e8400-e29b-41d4-a716-446655440000"
MEASURES_URL = f"{const.API_BASE_URL}/historical-data/{BRIDGE_ID}/measures"


def fresh_token(**overrides) -> dict:
    token = {
        "access_token": "access-1",
        "refresh_token": "refresh-1",
        "expires_in": 3600,
        "expires_at": time.time() + 3600,
    }
    token.update(overrides)
    return token


def user_payload(**overrides) -> dict:
    # Mirrors the OpenAPI UserV2DTO required fields plus a paired BridgeV2DTO.
    payload = {
        "ecomId": "30902006126975260",
        "email": "max.mustermann@example.com",
        "givenName": "Max Mustermann",
        "tenant": "de",
        "accountType": "HEYOBI_CUSTOMER",
        "hasSeenAdvertisingConsentBanner": True,
        "bridge": {
            "id": BRIDGE_ID,
            "label": "123abc",
            "description": "TODO",
            "claimedAt": "2024-12-11T14:40:19.442Z",
            "hardwareVersion": "1.0.0",
            "firmwareVersion": "1.0.0",
            "otaProgress": 0,
            "otaStatus": "NOT_UPDATING",
            "displayName": "123abc",
            "btChallengeId": "48B25F",
            "sensors": [
                {
                    "id": "sensor-1",
                    "bridgeId": BRIDGE_ID,
                    "hardwareVersion": "1.0.0",
                    "firmwareVersion": "1.0.0",
                    "otaStatus": "NOT_UPDATING",
                    "isOnline": True,
                    "batteryLevel": 94,
                    "claimedAt": "2024-12-11T14:40:19.442Z",
                    "dataVisibleSince": "2024-12-11T14:40:19.442Z",
                    "displayName": "Wallbox1",
                }
            ],
        },
    }
    payload.update(overrides)
    return payload


def measures_payload(device: str = "sensor-1") -> dict:
    # Mirrors MultiDeviceTimestreamRecordsDTO / TimestreamRecordDTO.
    return {
        "devices": {
            device: [
                {"time": "2024-08-19 00:00:00.000000000", "value": 500},
                {"time": "2024-08-19 00:05:00.000000000", "value": 750},
            ]
        }
    }


@pytest.fixture
def session() -> FakeSession:
    return FakeSession()


async def test_async_get_user_uses_versioned_accept_header(session) -> None:
    """GET /users/me must send the vendor v2 Accept header and bearer token."""
    session.queue_response(
        "GET", USER_URL, FakeResponse(json_payload=user_payload())
    )
    api = OBIEnergyApi(session, fresh_token())

    user = await api.async_get_user()

    call = session.calls[0]
    assert call.kwargs["headers"]["Accept"] == const.USER_ACCEPT
    assert call.kwargs["headers"]["Authorization"] == "Bearer access-1"
    assert user["ecomId"] == "30902006126975260"
    assert user["bridge"]["sensors"][0]["batteryLevel"] == 94


async def test_async_get_latest_measure_returns_newest_record_per_device(
    session,
) -> None:
    """The client must reduce each device's record list to its newest entry."""
    session.queue_response(
        "GET", MEASURES_URL, FakeResponse(json_payload=measures_payload())
    )
    api = OBIEnergyApi(session, fresh_token())

    result = await api.async_get_latest_measure(BRIDGE_ID, "energy")

    call = session.calls[0]
    assert call.kwargs["headers"]["Accept"] == const.HISTORY_ACCEPT
    assert ("measures", "energy") in call.kwargs["params"]
    assert result["sensor-1"]["value"] == 750
    assert result["sensor-1"]["time"] == "2024-08-19 00:05:00.000000000"


async def test_async_get_latest_measure_rejects_missing_devices_key(session) -> None:
    """A response without a top-level devices object violates the contract."""
    session.queue_response("GET", MEASURES_URL, FakeResponse(json_payload={"nope": True}))
    api = OBIEnergyApi(session, fresh_token())

    with pytest.raises(OBIEnergyResponseError):
        await api.async_get_latest_measure(BRIDGE_ID, "energy")


async def test_async_get_all_latest_measures_requests_both_directions(
    session,
) -> None:
    """import (energy) and export (negative_energy) must be fetched separately."""
    session.queue_response(
        "GET", MEASURES_URL, FakeResponse(json_payload=measures_payload())
    )
    session.queue_response(
        "GET", MEASURES_URL, FakeResponse(json_payload=measures_payload())
    )
    api = OBIEnergyApi(session, fresh_token())

    imported, exported = await api.async_get_all_latest_measures(BRIDGE_ID)

    requested_measures = {call.kwargs["params"][-1] for call in session.calls}
    assert requested_measures == {
        ("measures", "energy"),
        ("measures", "negative_energy"),
    }
    assert imported["sensor-1"]["value"] == 750
    assert exported["sensor-1"]["value"] == 750


async def test_expired_token_is_refreshed_before_request(session) -> None:
    """A token past its expiry leeway must be refreshed before the API call."""
    token = fresh_token(expires_at=time.time() - 5)
    session.queue_response(
        "POST",
        const.TOKEN_URL,
        FakeResponse(
            json_payload={
                "access_token": "access-2",
                "refresh_token": "refresh-2",
                "expires_in": 3600,
            }
        ),
    )
    session.queue_response("GET", USER_URL, FakeResponse(json_payload=user_payload()))

    updates: list[dict] = []
    api = OBIEnergyApi(session, token, token_update_callback=updates.append)
    await api.async_get_user()

    assert api.token["access_token"] == "access-2"
    assert updates and updates[0]["access_token"] == "access-2"


async def test_missing_refresh_token_raises_auth_error(session) -> None:
    """An expired token with no refresh token cannot be silently recovered."""
    token = fresh_token(expires_at=time.time() - 5, refresh_token=None)
    api = OBIEnergyApi(session, token)

    with pytest.raises(OBIEnergyAuthError):
        await api.async_get_user()


async def test_refresh_rejected_by_server_raises_auth_error(session) -> None:
    """A 400/401 refresh response means the refresh token itself is invalid."""
    token = fresh_token(expires_at=time.time() - 5)
    session.queue_response("POST", const.TOKEN_URL, FakeResponse(status=400))
    api = OBIEnergyApi(session, token)

    with pytest.raises(OBIEnergyAuthError):
        await api.async_get_user()


async def test_refresh_server_error_raises_connection_error(session) -> None:
    """A 5xx refresh response is a transient failure, not a rejected credential."""
    token = fresh_token(expires_at=time.time() - 5)
    session.queue_response("POST", const.TOKEN_URL, FakeResponse(status=503))
    api = OBIEnergyApi(session, token)

    with pytest.raises(OBIEnergyConnectionError):
        await api.async_get_user()


async def test_401_mid_flight_forces_one_retry_then_succeeds(session) -> None:
    """A live 401 (not just an expiry timer) must trigger a forced refresh+retry."""
    session.queue_response("GET", USER_URL, FakeResponse(status=401))
    session.queue_response(
        "POST",
        const.TOKEN_URL,
        FakeResponse(
            json_payload={
                "access_token": "access-3",
                "refresh_token": "refresh-3",
                "expires_in": 3600,
            }
        ),
    )
    session.queue_response("GET", USER_URL, FakeResponse(json_payload=user_payload()))
    api = OBIEnergyApi(session, fresh_token())

    user = await api.async_get_user()

    assert user["ecomId"] == "30902006126975260"
    assert api.token["access_token"] == "access-3"


async def test_401_after_retry_raises_auth_error(session) -> None:
    """If the retried request is still rejected, surface an auth error, not a loop."""
    session.queue_response("GET", USER_URL, FakeResponse(status=401))
    session.queue_response(
        "POST",
        const.TOKEN_URL,
        FakeResponse(
            json_payload={
                "access_token": "access-4",
                "refresh_token": "refresh-4",
                "expires_in": 3600,
            }
        ),
    )
    session.queue_response("GET", USER_URL, FakeResponse(status=401))
    api = OBIEnergyApi(session, fresh_token())

    with pytest.raises(OBIEnergyAuthError):
        await api.async_get_user()


async def test_forbidden_response_raises_auth_error_without_retry(session) -> None:
    """403 is a real authorization failure and must not trigger a refresh loop."""
    session.queue_response("GET", USER_URL, FakeResponse(status=403))
    api = OBIEnergyApi(session, fresh_token())

    with pytest.raises(OBIEnergyAuthError):
        await api.async_get_user()
    assert len(session.calls) == 1


async def test_server_error_raises_response_error(session) -> None:
    """A 5xx from the OBI Energy API is a response error, not silently swallowed."""
    session.queue_response("GET", USER_URL, FakeResponse(status=500))
    api = OBIEnergyApi(session, fresh_token())

    with pytest.raises(OBIEnergyResponseError):
        await api.async_get_user()


async def test_non_dict_json_payload_raises_response_error(session) -> None:
    """The API contract guarantees a JSON object; a bare array is a contract break."""
    session.queue_response("GET", USER_URL, FakeResponse(json_payload=[1, 2, 3]))
    api = OBIEnergyApi(session, fresh_token())

    with pytest.raises(OBIEnergyResponseError):
        await api.async_get_user()


async def test_network_failure_raises_connection_error(session) -> None:
    """A dropped connection must surface as OBIEnergyConnectionError, not raw ClientError."""
    session.queue_error("GET", USER_URL, ClientConnectionError())
    api = OBIEnergyApi(session, fresh_token())

    with pytest.raises(OBIEnergyConnectionError):
        await api.async_get_user()


async def test_refresh_network_failure_raises_connection_error(session) -> None:
    """A dropped connection during token refresh must not raise a raw ClientError."""
    token = fresh_token(expires_at=time.time() - 5)
    session.queue_error("POST", const.TOKEN_URL, ClientConnectionError())
    api = OBIEnergyApi(session, token)

    with pytest.raises(OBIEnergyConnectionError):
        await api.async_get_user()


async def test_refresh_response_without_access_token_raises_response_error(
    session,
) -> None:
    """A 2xx refresh response missing access_token is a malformed contract."""
    token = fresh_token(expires_at=time.time() - 5)
    session.queue_response(
        "POST", const.TOKEN_URL, FakeResponse(json_payload={"token_type": "Bearer"})
    )
    api = OBIEnergyApi(session, token)

    with pytest.raises(OBIEnergyResponseError):
        await api.async_get_user()


async def test_refresh_response_invalid_json_raises_response_error(session) -> None:
    """A non-JSON refresh response body must not propagate a raw parse error."""
    token = fresh_token(expires_at=time.time() - 5)
    session.queue_response("POST", const.TOKEN_URL, FakeResponse(status=200))
    api = OBIEnergyApi(session, token)

    with pytest.raises(OBIEnergyResponseError):
        await api.async_get_user()


async def test_missing_access_token_without_expiry_raises_auth_error(session) -> None:
    """A token dict with no access_token and no expiry info still can't authenticate."""
    token = {"refresh_token": "refresh-1"}
    api = OBIEnergyApi(session, token)

    with pytest.raises(OBIEnergyAuthError):
        await api.async_get_user()
    assert not session.calls


async def test_non_numeric_expires_at_is_treated_as_expiring(session) -> None:
    """A corrupted expires_at value must not be trusted; force a refresh instead."""
    token = fresh_token(expires_at="not-a-number")
    session.queue_response(
        "POST",
        const.TOKEN_URL,
        FakeResponse(
            json_payload={
                "access_token": "access-5",
                "refresh_token": "refresh-5",
                "expires_in": 3600,
            }
        ),
    )
    session.queue_response("GET", USER_URL, FakeResponse(json_payload=user_payload()))
    api = OBIEnergyApi(session, token)

    await api.async_get_user()

    assert api.token["access_token"] == "access-5"


async def test_missing_expires_at_is_not_treated_as_expiring(session) -> None:
    """A token with no expiry information at all should not force a refresh."""
    token = fresh_token()
    del token["expires_at"]
    session.queue_response("GET", USER_URL, FakeResponse(json_payload=user_payload()))
    api = OBIEnergyApi(session, token)

    await api.async_get_user()

    assert len(session.calls) == 1
    assert session.calls[0].method == "GET"


async def test_malformed_device_entries_are_skipped(session) -> None:
    """Non-string device ids and non-list record lists must be ignored, not crash."""
    session.queue_response(
        "GET",
        MEASURES_URL,
        FakeResponse(
            json_payload={
                "devices": {
                    "sensor-1": [{"time": "2024-08-19 00:00:00.000000000", "value": 1}],
                    "sensor-2": "not-a-list",
                }
            }
        ),
    )
    api = OBIEnergyApi(session, fresh_token())

    result = await api.async_get_latest_measure(BRIDGE_ID, "energy")

    assert set(result) == {"sensor-1"}


async def test_vendor_content_type_body_without_json_content_type_is_parsed(
    session,
) -> None:
    """OBI's vendor Accept type is not application/json; body must still parse.

    ``_async_json_response`` decodes with ``content_type=None`` specifically so a
    non-standard ``Content-Type`` header (OBI's vendor media type) does not make
    aiohttp reject a JSON body that vendor APIs commonly return.
    """
    session.queue_response(
        "GET",
        USER_URL,
        FakeResponse(json_payload=user_payload(ecomId="1")),
    )
    api = OBIEnergyApi(session, fresh_token())

    user = await api.async_get_user()

    assert user["ecomId"] == "1"
