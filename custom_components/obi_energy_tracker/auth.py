"""Passwordless OBI Keycloak authentication flow."""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from http import HTTPStatus
from typing import Any
from urllib.parse import urljoin

from aiohttp import ClientError, ClientSession
from yarl import URL

from .const import (
    AUTHORIZE_URL,
    CLIENT_ID,
    OAUTH_SCOPE,
    REDIRECT_URI,
    TOKEN_URL,
)
from .util import compute_code_challenge, generate_code_verifier, login_form

_AUTH_ORIGIN = URL(AUTHORIZE_URL).origin()
_LOGGER = logging.getLogger(__name__)


class OBIAuthError(Exception):
    """Base exception for OBI authentication failures."""


class OBIConnectionError(OBIAuthError):
    """Raised when the OBI authentication service cannot be reached."""


class OBIInvalidOTP(OBIAuthError):
    """Raised when the one-time password is rejected."""


async def _async_json_response(response: Any, *, context: str) -> Mapping[str, Any]:
    """Decode JSON without requiring a standards-compliant Content-Type header."""
    try:
        payload = await response.json(content_type=None)
    except (ClientError, ValueError) as err:
        raise OBIAuthError(f"{context} returned invalid JSON") from err
    if not isinstance(payload, Mapping):
        raise OBIAuthError(f"{context} returned an unexpected JSON value")
    return payload


def _validated_auth_url(base_url: str, location: str) -> str:
    """Resolve an OBI authentication URL and reject unexpected origins."""
    try:
        target = URL(urljoin(base_url, location))
        valid = target.origin() == _AUTH_ORIGIN and target.user is None
    except ValueError as err:
        raise OBIAuthError("OBI authentication returned an invalid URL") from err
    if not valid:
        raise OBIAuthError("OBI authentication redirected to an unexpected origin")
    return str(target)


def _check_status(response: Any, stage: str) -> None:
    """Report transient failures separately, without logging response bodies/URLs."""
    status = response.status
    if status >= HTTPStatus.BAD_REQUEST:
        response.release()
        message = f"{stage} returned HTTP {status}"
        if (
            status in (HTTPStatus.REQUEST_TIMEOUT, HTTPStatus.TOO_MANY_REQUESTS)
            or status >= 500
        ):
            raise OBIConnectionError(message)
        raise OBIAuthError(message)


class OBIPasswordlessAuth:
    """Perform OBI's email + OTP Authorization Code/PKCE flow."""

    def __init__(self, session: ClientSession) -> None:
        self._session = session
        self._cookies: dict[str, str] = {}
        self._verifier: str | None = None
        self._otp_action: str | None = None
        self._otp_hidden: dict[str, str] = {}

    def _collect_cookies(self, responses: list[Any]) -> None:
        for response in responses:
            for name, morsel in response.cookies.items():
                self._cookies[name] = morsel.value

    async def async_start(self, email: str) -> None:
        """Start the login flow and request an OTP for the email address."""
        self._cookies.clear()
        self._otp_action = None
        self._otp_hidden = {}
        self._verifier = generate_code_verifier()
        params = {
            "client_id": CLIENT_ID,
            "response_type": "code",
            "scope": OAUTH_SCOPE,
            "redirect_uri": REDIRECT_URI,
            "code_challenge": compute_code_challenge(self._verifier),
            "code_challenge_method": "S256",
        }

        try:
            response = await self._session.get(
                AUTHORIZE_URL,
                params=params,
                cookies=self._cookies,
                allow_redirects=True,
            )
            self._collect_cookies([*response.history, response])
            _check_status(response, "Authorization page")
            html = await response.text()
            username_form = login_form(html, "username")
            if username_form is None:
                raise OBIAuthError("OBI login page did not contain an email form")

            username_url = _validated_auth_url(str(response.url), username_form.action)
            response = await self._session.post(
                username_url,
                data={**username_form.hidden, "username": email},
                cookies=self._cookies,
                allow_redirects=True,
            )
            self._collect_cookies([*response.history, response])
            _check_status(response, "Email submission")
            html = await response.text()
            otp_form = login_form(html, "code")
            if otp_form is None:
                raise OBIAuthError("OBI login page did not contain an OTP form")
            self._otp_action = _validated_auth_url(str(response.url), otp_form.action)
            self._otp_hidden = otp_form.hidden
        except (ClientError, TimeoutError) as err:
            error = OBIConnectionError("Unable to contact OBI authentication service")
            _LOGGER.warning("OBI login start failed: %s", error)
            raise error from err
        except OBIAuthError as err:
            # These messages are controlled locally. Never log the HTML, email,
            # request URL, cookies or exception traceback (URLs contain secrets).
            _LOGGER.warning("OBI login start failed: %s", err)
            raise

    async def async_finish(self, otp: str) -> dict[str, Any]:
        """Submit the OTP and exchange the authorization code for tokens."""
        if not self._otp_action or not self._verifier:
            raise OBIAuthError("Authentication flow was not started")

        try:
            # Home Assistant's shared session blocks any response containing an
            # absolute redirect to localhost, even with allow_redirects=False.
            # OBI intentionally returns that OAuth callback. Request-scoped empty
            # middleware lets us inspect the Location header without following it.
            response = await self._session.post(
                self._otp_action,
                data={**self._otp_hidden, "code": otp},
                cookies=self._cookies,
                allow_redirects=False,
                middlewares=(),
            )
            self._collect_cookies([response])
            _check_status(response, "OTP submission")

            location = response.headers.get("Location")
            if location:
                response.release()
            if not location:
                # Keycloak usually returns the OTP page again after an invalid code.
                if response.status < HTTPStatus.BAD_REQUEST:
                    html = await response.text()
                    form = login_form(html, "code")
                    if form is None:
                        raise OBIAuthError("OBI login page did not contain an OTP form")
                    self._otp_action = _validated_auth_url(
                        str(response.url), form.action
                    )
                    self._otp_hidden = form.hidden
                raise OBIInvalidOTP("The one-time code was rejected or expired")

            current_url = str(response.url)
            redirects = 0
            while URL(location).with_query(None).with_fragment(None) != URL(
                REDIRECT_URI
            ):
                redirects += 1
                if redirects > 10:
                    raise OBIAuthError("Too many redirects during OBI login")

                next_url = _validated_auth_url(current_url, location)
                response = await self._session.get(
                    next_url,
                    cookies=self._cookies,
                    allow_redirects=False,
                    middlewares=(),
                )
                self._collect_cookies([response])
                _check_status(response, "Login redirect")
                current_url = str(response.url)
                location = response.headers.get("Location", "")
                response.release()
                if not location:
                    raise OBIAuthError("OBI login did not redirect to the callback URL")

            code = URL(location).query.get("code")
            if not code:
                raise OBIAuthError("OBI callback did not include an authorization code")

            response = await self._session.post(
                TOKEN_URL,
                data={
                    "grant_type": "authorization_code",
                    "client_id": CLIENT_ID,
                    "redirect_uri": REDIRECT_URI,
                    "code_verifier": self._verifier,
                    "code": code,
                },
            )
            _check_status(response, "Token exchange")
            token = await _async_json_response(response, context="OBI token endpoint")
        except (ClientError, TimeoutError) as err:
            raise OBIConnectionError("Unable to complete OBI authentication") from err

        if not token.get("access_token"):
            raise OBIAuthError("OBI token response did not include an access token")

        result = dict(token)
        if "expires_in" in result:
            result["expires_in"] = int(result["expires_in"])
            result["expires_at"] = time.time() + result["expires_in"]

        # Log only non-secret token metadata so long-running auth can be diagnosed.
        _LOGGER.info(
            "OBI token obtained: scope=%s, access_expires_in=%s, refresh_expires_in=%s",
            result.get("scope"),
            result.get("expires_in"),
            result.get("refresh_expires_in"),
        )
        return result
