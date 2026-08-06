"""Tests for OBIPasswordlessAuth, OBI's email + OTP Authorization Code/PKCE flow.

These exercise the exact multi-hop sequence (authorize -> email form ->
OTP form -> Keycloak redirect chain -> token exchange) that produced the
"Unable to connect to OBI" / "OBI rejected the authentication request"
errors reported against the live integration: a network failure mid-chain
surfaces as ``OBIConnectionError`` ("cannot_connect"), while a rejected or
already-consumed authorization code at the token endpoint surfaces as a
bare ``OBIAuthError`` ("invalid_auth" / "OBI rejected the authentication
request") -- distinct from ``OBIInvalidOTP`` ("invalid_otp"), which only
covers the OTP form itself re-appearing.
"""

from __future__ import annotations

from aiohttp import ClientConnectionError
import pytest

from conftest import FakeResponse, FakeSession, import_submodule

auth_module = import_submodule("auth")
const = import_submodule("const")

OBIPasswordlessAuth = auth_module.OBIPasswordlessAuth
OBIAuthError = auth_module.OBIAuthError
OBIConnectionError = auth_module.OBIConnectionError
OBIInvalidOTP = auth_module.OBIInvalidOTP

AUTHORIZE_URL = const.AUTHORIZE_URL
TOKEN_URL = const.TOKEN_URL
EMAIL_FORM_URL = "https://auth.obi.com/login-actions/authenticate"
OTP_FORM_URL = "https://auth.obi.com/login-actions/authenticate?execution=otp"
CALLBACK_URL = "http://localhost/callback"


def email_form_html(action: str = EMAIL_FORM_URL) -> str:
    return f'<html><body><form action="{action}">' '<input name="username"></form></body></html>'


def otp_form_html(action: str = OTP_FORM_URL) -> str:
    return f'<html><body><form action="{action}">' '<input name="code"></form></body></html>'


@pytest.fixture
def session() -> FakeSession:
    return FakeSession()


async def start_flow(session: FakeSession) -> OBIPasswordlessAuth:
    """Drive async_start() to a valid pending-OTP state for reuse by finish tests."""
    session.queue_response(
        "GET",
        AUTHORIZE_URL,
        FakeResponse(text_body=email_form_html(), url=AUTHORIZE_URL),
    )
    session.queue_response(
        "POST",
        EMAIL_FORM_URL,
        FakeResponse(text_body=otp_form_html(), url=EMAIL_FORM_URL),
    )
    auth = OBIPasswordlessAuth(session)
    await auth.async_start("user@example.test")
    return auth


# --- async_start ---------------------------------------------------------


async def test_async_start_extracts_email_then_otp_form_action(session) -> None:
    """A successful start must land on the OTP form's action URL."""
    auth = await start_flow(session)

    assert auth._otp_action == OTP_FORM_URL
    call = session.calls[0]
    assert call.kwargs["params"]["client_id"] == const.CLIENT_ID
    assert call.kwargs["params"]["code_challenge_method"] == "S256"


async def test_async_start_missing_email_form_raises_auth_error(session) -> None:
    """If OBI's login page has no form, the flow cannot proceed."""
    session.queue_response(
        "GET", AUTHORIZE_URL, FakeResponse(text_body="<html></html>", url=AUTHORIZE_URL)
    )
    auth = OBIPasswordlessAuth(session)

    with pytest.raises(OBIAuthError):
        await auth.async_start("user@example.test")


async def test_async_start_missing_otp_form_raises_auth_error(session) -> None:
    """If the email submission doesn't return an OTP form, surface an auth error."""
    session.queue_response(
        "GET", AUTHORIZE_URL, FakeResponse(text_body=email_form_html(), url=AUTHORIZE_URL)
    )
    session.queue_response(
        "POST", EMAIL_FORM_URL, FakeResponse(text_body="<html></html>", url=EMAIL_FORM_URL)
    )
    auth = OBIPasswordlessAuth(session)

    with pytest.raises(OBIAuthError):
        await auth.async_start("user@example.test")


async def test_async_start_http_error_status_raises_auth_error(session) -> None:
    """A non-2xx authorize page is an auth failure, not silently ignored."""
    session.queue_response("GET", AUTHORIZE_URL, FakeResponse(status=500, url=AUTHORIZE_URL))
    auth = OBIPasswordlessAuth(session)

    with pytest.raises(OBIAuthError):
        await auth.async_start("user@example.test")


async def test_async_start_network_failure_raises_connection_error(session) -> None:
    """A network drop while starting login must map to cannot_connect, not crash."""
    session.queue_error("GET", AUTHORIZE_URL, ClientConnectionError())
    auth = OBIPasswordlessAuth(session)

    with pytest.raises(OBIConnectionError):
        await auth.async_start("user@example.test")


# --- async_finish: success path ------------------------------------------


async def test_async_finish_exchanges_code_for_token(session) -> None:
    """OTP acceptance -> redirect chain -> code -> token exchange, end to end."""
    auth = await start_flow(session)
    session.queue_response(
        "POST",
        OTP_FORM_URL,
        FakeResponse(
            status=302,
            headers={"Location": f"{CALLBACK_URL}?code=authcode123&state=xyz"},
            url=OTP_FORM_URL,
        ),
    )
    session.queue_response(
        "POST",
        TOKEN_URL,
        FakeResponse(
            json_payload={
                "access_token": "tok-1",
                "refresh_token": "ref-1",
                "expires_in": "3600",
            }
        ),
    )

    token = await auth.async_finish("123456")

    assert token["access_token"] == "tok-1"
    assert token["expires_in"] == 3600
    assert token["expires_at"] > 0
    token_call = session.calls[-1]
    assert token_call.kwargs["data"]["code"] == "authcode123"
    assert token_call.kwargs["data"]["grant_type"] == "authorization_code"
    assert token_call.kwargs["data"]["code_verifier"] == auth._verifier


async def test_async_finish_follows_intermediate_redirects_before_callback(
    session,
) -> None:
    """Keycloak may hop through more than one redirect before the final callback."""
    auth = await start_flow(session)
    intermediate_url = "https://auth.obi.com/login-actions/action-token"
    session.queue_response(
        "POST",
        OTP_FORM_URL,
        FakeResponse(status=302, headers={"Location": intermediate_url}, url=OTP_FORM_URL),
    )
    session.queue_response(
        "GET",
        intermediate_url,
        FakeResponse(
            status=302,
            headers={"Location": f"{CALLBACK_URL}?code=authcode456"},
            url=intermediate_url,
        ),
    )
    session.queue_response(
        "POST",
        TOKEN_URL,
        FakeResponse(json_payload={"access_token": "tok-2", "expires_in": 3600}),
    )

    token = await auth.async_finish("123456")

    assert token["access_token"] == "tok-2"
    token_call = session.calls[-1]
    assert token_call.kwargs["data"]["code"] == "authcode456"


# --- async_finish: invalid OTP (server re-shows the OTP form) ------------


async def test_async_finish_reshown_otp_form_raises_invalid_otp(session) -> None:
    """No Location header means Keycloak rejected the code and re-rendered the form."""
    auth = await start_flow(session)
    session.queue_response(
        "POST",
        OTP_FORM_URL,
        FakeResponse(text_body=otp_form_html(action=OTP_FORM_URL), url=OTP_FORM_URL),
    )

    with pytest.raises(OBIInvalidOTP):
        await auth.async_finish("000000")


async def test_async_finish_updates_otp_action_after_rejection_for_retry(
    session,
) -> None:
    """A fresh OTP form action after rejection lets a *new* code be retried safely."""
    auth = await start_flow(session)
    new_action = "https://auth.obi.com/login-actions/authenticate?execution=otp&try=2"
    session.queue_response(
        "POST",
        OTP_FORM_URL,
        FakeResponse(text_body=otp_form_html(action=new_action), url=OTP_FORM_URL),
    )

    with pytest.raises(OBIInvalidOTP):
        await auth.async_finish("000000")

    assert auth._otp_action == new_action


# --- async_finish: the "cannot_connect" vs "invalid_auth" split ----------


async def test_async_finish_network_failure_during_token_exchange_is_connection_error(
    session,
) -> None:
    """A dropped connection at the token endpoint must map to cannot_connect.

    This is the "Unable to connect to OBI" failure mode: OTP + redirects
    succeeded (an authorization code was issued) but the final POST to the
    token endpoint failed at the transport level.
    """
    auth = await start_flow(session)
    session.queue_response(
        "POST",
        OTP_FORM_URL,
        FakeResponse(
            status=302,
            headers={"Location": f"{CALLBACK_URL}?code=authcode789"},
            url=OTP_FORM_URL,
        ),
    )
    session.queue_error("POST", TOKEN_URL, ClientConnectionError())

    with pytest.raises(OBIConnectionError):
        await auth.async_finish("123456")


async def test_async_finish_rejected_authorization_code_is_auth_error_not_invalid_otp(
    session,
) -> None:
    """A stale/already-redeemed authorization code fails at the token endpoint.

    This is the "OBI rejected the authentication request" failure mode seen
    on a retry with an already-consumed OTP: the OTP form step is already
    behind us (Location header was present), so the token-exchange 400 must
    surface as a generic ``OBIAuthError`` ("invalid_auth"), not
    ``OBIInvalidOTP`` ("invalid_otp") -- they render different user-facing
    strings and only one matches what actually happened.
    """
    auth = await start_flow(session)
    session.queue_response(
        "POST",
        OTP_FORM_URL,
        FakeResponse(
            status=302,
            headers={"Location": f"{CALLBACK_URL}?code=stale-code"},
            url=OTP_FORM_URL,
        ),
    )
    session.queue_response("POST", TOKEN_URL, FakeResponse(status=400))

    with pytest.raises(OBIAuthError) as exc_info:
        await auth.async_finish("123456")
    assert not isinstance(exc_info.value, OBIInvalidOTP)


# --- async_finish: malformed redirect / callback --------------------------


async def test_async_finish_too_many_redirects_raises_auth_error(session) -> None:
    """A redirect loop must be bounded, not hang or recurse forever."""
    auth = await start_flow(session)
    loop_url = "https://auth.obi.com/login-actions/loop"
    session.queue_response(
        "POST",
        OTP_FORM_URL,
        FakeResponse(status=302, headers={"Location": loop_url}, url=OTP_FORM_URL),
    )
    for _ in range(12):
        session.queue_response(
            "GET", loop_url, FakeResponse(status=302, headers={"Location": loop_url}, url=loop_url)
        )

    with pytest.raises(OBIAuthError):
        await auth.async_finish("123456")


async def test_async_finish_redirect_without_location_raises_auth_error(session) -> None:
    """A redirect hop with no further Location header can't reach the callback."""
    auth = await start_flow(session)
    dead_end_url = "https://auth.obi.com/login-actions/dead-end"
    session.queue_response(
        "POST",
        OTP_FORM_URL,
        FakeResponse(status=302, headers={"Location": dead_end_url}, url=OTP_FORM_URL),
    )
    session.queue_response("GET", dead_end_url, FakeResponse(status=302, headers={}, url=dead_end_url))

    with pytest.raises(OBIAuthError):
        await auth.async_finish("123456")


async def test_async_finish_callback_without_code_raises_auth_error(session) -> None:
    """A callback URL missing the ?code= parameter cannot be exchanged."""
    auth = await start_flow(session)
    session.queue_response(
        "POST",
        OTP_FORM_URL,
        FakeResponse(status=302, headers={"Location": f"{CALLBACK_URL}?state=xyz"}, url=OTP_FORM_URL),
    )

    with pytest.raises(OBIAuthError):
        await auth.async_finish("123456")


async def test_async_finish_missing_access_token_raises_auth_error(session) -> None:
    """A token response without access_token is a broken contract, not a valid login."""
    auth = await start_flow(session)
    session.queue_response(
        "POST",
        OTP_FORM_URL,
        FakeResponse(status=302, headers={"Location": f"{CALLBACK_URL}?code=abc"}, url=OTP_FORM_URL),
    )
    session.queue_response("POST", TOKEN_URL, FakeResponse(json_payload={"token_type": "Bearer"}))

    with pytest.raises(OBIAuthError):
        await auth.async_finish("123456")


async def test_async_finish_before_start_raises_auth_error(session) -> None:
    """Calling finish() without a prior successful start() must fail fast."""
    auth = OBIPasswordlessAuth(session)

    with pytest.raises(OBIAuthError):
        await auth.async_finish("123456")
