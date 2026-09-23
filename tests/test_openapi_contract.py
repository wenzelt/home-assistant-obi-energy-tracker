"""Executable comparisons with the supplied spec, not duplicated constants.

These are HTTP-double contract tests. They do not validate live OBI responses
or Home Assistant lifecycle behavior. OAuth is documented in prose, not paths.
"""

import json
import re
from pathlib import Path

import pytest
from conftest import FakeResponse, FakeSession, import_submodule

SPEC = json.loads(
    (Path(__file__).parents[1] / "docs" / "openapi-public.json").read_text()
)
SCHEMAS = SPEC["components"]["schemas"]
BASE = SPEC["servers"][0]["url"]
HISTORY = "/historical-data/{bridgeId}/measures"
BRIDGE_ID = SCHEMAS["BridgeV2DTO"]["properties"]["id"]["example"]
api = import_submodule("api")
auth = import_submodule("auth")
const = import_submodule("const")
util = import_submodule("util")


def resolve(schema):
    if "$ref" in schema:
        return SCHEMAS[schema["$ref"].split("/")[-1]]
    return schema


def example(schema, *, required_only=False):
    """Build payloads from the actual schema examples and required arrays."""
    schema = resolve(schema)
    if "example" in schema:
        return schema["example"]
    if schema["type"] == "object":
        keys = schema.get("required", []) if required_only else schema["properties"]
        return {
            key: example(schema["properties"][key], required_only=required_only)
            for key in keys
        }
    if schema["type"] == "array":
        return [example(schema["items"], required_only=required_only)]
    raise AssertionError(f"No example available for {schema}")


def response_content(path):
    return SPEC["paths"][path]["get"]["responses"]["200"]["content"]


def test_urls_headers_and_bearer_security_match_spec():
    assert const.API_BASE_URL == BASE
    assert const.USER_ACCEPT in response_content("/users/me")
    assert const.HISTORY_ACCEPT in response_content(HISTORY)
    scheme = SPEC["components"]["securitySchemes"]["bearerAuth"]
    assert (scheme["type"], scheme["scheme"], scheme["bearerFormat"]) == (
        "http",
        "bearer",
        "JWT",
    )
    assert SPEC["security"] == [{"bearerAuth": []}]


@pytest.mark.parametrize("required_only", [True, False])
async def test_user_schema_examples_and_optional_bridge(required_only):
    schema = next(iter(response_content("/users/me").values()))["schema"]
    payload = example(schema, required_only=required_only)
    session = FakeSession()
    session.queue_response(
        "GET", BASE + "/users/me", FakeResponse(json_payload=payload)
    )
    result = await api.OBIEnergyApi(
        session, {"access_token": "fixture"}
    ).async_get_user()
    assert result == payload
    headers = session.calls[0].kwargs["headers"]
    assert headers["Authorization"] == "Bearer fixture"
    assert headers["Accept"] in response_content("/users/me")
    if required_only:
        assert "bridge" not in result
    else:
        assert result["bridge"]["sensors"][0]["batteryLevel"] == 94


async def test_nullable_sensor_fields_survive_user_parsing():
    payload = example(SCHEMAS["UserV2DTO"])
    sensor = payload["bridge"]["sensors"][0]
    for key, schema in SCHEMAS["SensorV2DTO"]["properties"].items():
        if schema.get("nullable"):
            sensor[key] = None
    session = FakeSession()
    session.queue_response(
        "GET", BASE + "/users/me", FakeResponse(json_payload=payload)
    )
    result = await api.OBIEnergyApi(
        session, {"access_token": "fixture"}
    ).async_get_user()
    assert result["bridge"]["sensors"][0]["batteryLevel"] is None
    assert result["bridge"]["sensors"][0]["displayName"] is None


PARAMETERS = {p["name"]: p for p in SPEC["paths"][HISTORY]["get"]["parameters"]}


@pytest.mark.parametrize("measure", PARAMETERS["measures"]["schema"]["items"]["enum"])
async def test_history_requests_and_responses_match_spec(measure):
    schema = next(iter(response_content(HISTORY).values()))["schema"]
    payload = example(schema)
    session = FakeSession()
    url = BASE + HISTORY.replace("{bridgeId}", BRIDGE_ID)
    session.queue_response("GET", url, FakeResponse(json_payload=payload))
    result = await api.OBIEnergyApi(
        session, {"access_token": "fixture"}
    ).async_get_latest_measure(BRIDGE_ID, measure)
    call = session.calls[0]
    assert call.method == "GET"
    assert call.kwargs["headers"]["Accept"] in response_content(HISTORY)
    assert call.kwargs["headers"]["Authorization"] == "Bearer fixture"
    assert call.kwargs["params"] == [
        ("duration", const.DEFAULT_HISTORY_DURATION),
        ("measures", measure),
    ]
    for key, _ in call.kwargs["params"]:
        assert PARAMETERS[key]["in"] == "query"
    assert result == {
        device: records[0] for device, records in payload["devices"].items()
    }


@pytest.mark.parametrize("path", ["/users/me", HISTORY])
@pytest.mark.parametrize("status", [401, 403, 404])
async def test_documented_error_statuses_do_not_all_mean_expired_login(path, status):
    responses = SPEC["paths"][path]["get"]["responses"]
    assert str(status) in responses
    session = FakeSession()
    session.queue_response(
        "GET", BASE + path.replace("{bridgeId}", BRIDGE_ID), FakeResponse(status=status)
    )
    client = api.OBIEnergyApi(session, {"access_token": "fixture"})
    expected = api.OBIEnergyAuthError if status == 401 else api.OBIEnergyResponseError
    with pytest.raises(expected):
        if path == "/users/me":
            await client.async_get_user()
        else:
            await client.async_get_latest_measure(BRIDGE_ID, "energy")
    # No token refresh for forbidden/not-found responses. With no refresh token,
    # 401 also fails immediately; refresh+retry itself has separate unit tests.
    assert len(session.calls) == 1


async def test_authorization_flow_matches_embedded_login_example():
    description = SPEC["info"]["description"]
    oauth_base = re.search(r"^B=(\S+)$", description, re.MULTILINE)[1]
    client_id = re.search(r"^C=(\S+)$", description, re.MULTILINE)[1]
    redirect = re.search(r"^U=(\S+)$", description, re.MULTILINE)[1]
    scope = re.search(r"scope=([^&]+)&", description)[1]
    assert oauth_base + "/auth" == const.AUTHORIZE_URL
    assert oauth_base + "/token" == const.TOKEN_URL
    email_url = "https://auth.obi.com/email-form"
    otp_url = "https://auth.obi.com/otp-form"
    session = FakeSession()
    session.queue_response(
        "GET",
        const.AUTHORIZE_URL,
        FakeResponse(
            url=const.AUTHORIZE_URL,
            text_body=f'<form action="{email_url}"><input name="username"></form>',
        ),
    )
    session.queue_response(
        "POST",
        email_url,
        FakeResponse(
            url=email_url,
            text_body=f'<form action="{otp_url}"><input name="code"></form>',
        ),
    )
    session.queue_response(
        "POST",
        otp_url,
        FakeResponse(
            url=otp_url,
            status=302,
            headers={"Location": redirect + "?code=fixture-code"},
        ),
    )
    session.queue_response(
        "POST",
        const.TOKEN_URL,
        FakeResponse(
            json_payload={"access_token": "fixture", "expires_in": 300},
        ),
    )
    flow = auth.OBIPasswordlessAuth(session)
    await flow.async_start("user@example.test")
    await flow.async_finish("ABC123")
    params = session.calls[0].kwargs["params"]
    assert params["client_id"] == client_id
    assert params["redirect_uri"] == redirect
    assert params["scope"] == scope
    assert params["response_type"] == "code"
    assert params["code_challenge_method"] == "S256"
    assert params["code_challenge"] == util.compute_code_challenge(flow._verifier)
    assert session.calls[1].kwargs["data"] == {"username": "user@example.test"}
    assert session.calls[2].kwargs["data"] == {"code": "ABC123"}
    assert session.calls[3].kwargs["data"] == {
        "grant_type": "authorization_code",
        "client_id": client_id,
        "redirect_uri": redirect,
        "code_verifier": flow._verifier,
        "code": "fixture-code",
    }


@pytest.mark.parametrize(
    "schema_name,field",
    [
        ("TimestreamRecordDTO", "time"),
        ("BridgeV2DTO", "claimedAt"),
        ("SensorV2DTO", "claimedAt"),
        ("SensorV2DTO", "dataVisibleSince"),
    ],
)
def test_timestamp_examples_parse(schema_name, field):
    value = SCHEMAS[schema_name]["properties"][field]["example"]
    assert util.parse_obi_timestamp(value) is not None


def test_documented_polling_default():
    interval = SCHEMAS["SensorV2DTO"]["properties"]["uploadInterval"]
    assert const.DEFAULT_UPDATE_INTERVAL.total_seconds() == interval["example"]
    assert "300 (default)" in interval["description"]
