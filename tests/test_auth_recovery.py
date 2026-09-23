"""Regression tests for safe and diagnosable reconnection failures."""

import pytest
from conftest import FakeResponse, FakeSession, import_submodule

auth = import_submodule("auth")
util = import_submodule("util")
const = import_submodule("const")
EMAIL_URL = "https://auth.obi.com/email"
OTP_URL = "https://auth.obi.com/otp"
EMAIL_FORM = f'<form action="{EMAIL_URL}"><input name="username"></form>'
OTP_FORM = f'<form action="{OTP_URL}"><input name="code"></form>'


def test_form_selection_and_hidden_fields():
    form = util.login_form(
        '<form action="/search"><input name="q"></form>'
        '<form action="/login?a=1&amp;b=2">'
        '<input type="hidden" name="csrf" value="a&amp;b">'
        '<input type="hidden" name="disabled" value="x" disabled>'
        '<input name="username"></form>',
        "username",
    )
    assert form.action == "/login?a=1&b=2"
    assert form.hidden == {"csrf": "a&b"}
    assert util.login_form(EMAIL_FORM, "code") is None
    assert util.login_form('<form><input name="code"></form>', "code").action == ""


@pytest.mark.parametrize("status", [408, 429, 500, 502, 503])
@pytest.mark.parametrize("stage", ["authorize", "email", "otp", "token"])
async def test_transient_http_errors_are_connection_errors(status, stage):
    session = FakeSession()
    session.queue_response(
        "GET",
        const.AUTHORIZE_URL,
        FakeResponse(
            status=status if stage == "authorize" else 200,
            text_body=EMAIL_FORM,
            url=const.AUTHORIZE_URL,
        ),
    )
    session.queue_response(
        "POST",
        EMAIL_URL,
        FakeResponse(
            status=status if stage == "email" else 200,
            text_body=OTP_FORM,
            url=EMAIL_URL,
        ),
    )
    session.queue_response(
        "POST",
        OTP_URL,
        FakeResponse(
            status=status if stage == "otp" else 302,
            headers={"Location": f"{const.REDIRECT_URI}?code=test"},
            url=OTP_URL,
        ),
    )
    session.queue_response("POST", const.TOKEN_URL, FakeResponse(status=status))
    flow = auth.OBIPasswordlessAuth(session)
    with pytest.raises(auth.OBIConnectionError, match=f"HTTP {status}"):
        await flow.async_start("user@example.test")
        await flow.async_finish("123456")


async def test_start_failure_logged_without_secrets(caplog):
    session = FakeSession()
    session.queue_response(
        "GET",
        const.AUTHORIZE_URL,
        FakeResponse(
            status=403,
            url=const.AUTHORIZE_URL + "?secret=private",
            text_body="private-cookie private-email@example.test",
        ),
    )
    with pytest.raises(auth.OBIAuthError):
        await auth.OBIPasswordlessAuth(session).async_start(
            "private-email@example.test"
        )
    assert "Authorization page returned HTTP 403" in caplog.text
    assert "private" not in caplog.text


async def test_email_form_redisplayed_is_not_an_otp_form(caplog):
    session = FakeSession()
    session.queue_response(
        "GET",
        const.AUTHORIZE_URL,
        FakeResponse(
            text_body=EMAIL_FORM,
            url=const.AUTHORIZE_URL,
        ),
    )
    session.queue_response(
        "POST",
        EMAIL_URL,
        FakeResponse(
            text_body=EMAIL_FORM,
            url=EMAIL_URL,
        ),
    )
    flow = auth.OBIPasswordlessAuth(session)
    with pytest.raises(auth.OBIAuthError, match="OTP form"):
        await flow.async_start("user@example.test")
    assert flow._otp_action is None
    assert "OTP form" in caplog.text


async def test_hidden_fields_preserved_and_user_input_wins():
    session = FakeSession()
    session.queue_response(
        "GET",
        const.AUTHORIZE_URL,
        FakeResponse(
            text_body=EMAIL_FORM.replace(
                '<input name="username">',
                '<input type="hidden" name="csrf" value="email-csrf">'
                '<input type="hidden" name="username" value="wrong">'
                '<input name="username">',
            ),
            url=const.AUTHORIZE_URL,
        ),
    )
    session.queue_response(
        "POST",
        EMAIL_URL,
        FakeResponse(
            text_body=OTP_FORM.replace(
                '<input name="code">',
                '<input type="hidden" name="csrf" value="otp-csrf"><input name="code">',
            ),
            url=EMAIL_URL,
        ),
    )
    session.queue_response(
        "POST",
        OTP_URL,
        FakeResponse(
            text_body=OTP_FORM,
            url=OTP_URL,
        ),
    )
    flow = auth.OBIPasswordlessAuth(session)
    await flow.async_start("user@example.test")
    assert session.calls[1].kwargs["data"] == {
        "csrf": "email-csrf",
        "username": "user@example.test",
    }
    with pytest.raises(auth.OBIInvalidOTP):
        await flow.async_finish("123456")
    assert session.calls[2].kwargs["data"] == {"csrf": "otp-csrf", "code": "123456"}
    assert flow._otp_hidden == {}


async def test_timeout_is_reported_as_connection_failure(caplog):
    session = FakeSession()
    session.queue_error("GET", const.AUTHORIZE_URL, TimeoutError("secret URL"))
    with pytest.raises(auth.OBIConnectionError):
        await auth.OBIPasswordlessAuth(session).async_start("user@example.test")
    assert "login start failed" in caplog.text
    assert "secret URL" not in caplog.text


async def test_restart_clears_stale_pending_otp_and_cookies():
    session = FakeSession()
    session.queue_response("GET", const.AUTHORIZE_URL, FakeResponse(status=400))
    flow = auth.OBIPasswordlessAuth(session)
    flow._otp_action = OTP_URL
    flow._cookies = {"stale": "cookie"}
    flow._otp_hidden = {"csrf": "stale"}
    with pytest.raises(auth.OBIAuthError):
        await flow.async_start("user@example.test")
    assert session.calls[0].kwargs["cookies"] == {}
    assert flow._otp_action is None
    assert flow._otp_hidden == {}


async def test_documented_scope_is_used_and_no_silent_retry():
    session = FakeSession()
    session.queue_response("GET", const.AUTHORIZE_URL, FakeResponse(status=400))
    with pytest.raises(auth.OBIAuthError):
        await auth.OBIPasswordlessAuth(session).async_start("user@example.test")
    assert len(session.calls) == 1
    assert session.calls[0].kwargs["params"]["scope"] == "openid"


async def test_unrelated_page_after_otp_is_not_reported_as_invalid_code():
    session = FakeSession()
    session.queue_response(
        "POST",
        OTP_URL,
        FakeResponse(
            url=OTP_URL,
            text_body=EMAIL_FORM,
        ),
    )
    flow = auth.OBIPasswordlessAuth(session)
    flow._otp_action = OTP_URL
    flow._verifier = "x" * 64
    with pytest.raises(auth.OBIAuthError) as error:
        await flow.async_finish("123456")
    assert not isinstance(error.value, auth.OBIInvalidOTP)


@pytest.mark.parametrize(
    "callback",
    [
        "http://localhost/callback-attacker?code=secret",
        "http://localhost/callback/extra?code=secret",
        "http://localhost:9999/callback?code=secret",
    ],
)
async def test_callback_must_match_exactly(callback):
    session = FakeSession()
    flow = auth.OBIPasswordlessAuth(session)
    flow._otp_action = OTP_URL
    flow._verifier = "x" * 64
    session.queue_response(
        "POST",
        OTP_URL,
        FakeResponse(
            status=302,
            url=OTP_URL,
            headers={"Location": callback},
        ),
    )
    with pytest.raises(auth.OBIAuthError, match="unexpected origin"):
        await flow.async_finish("123456")
    assert len(session.calls) == 1
