"""Passwordless OBI Keycloak authentication flow."""

from __future__ import annotations

from collections.abc import Mapping
from http import HTTPStatus
import time
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
from .util import compute_code_challenge, first_form_action, generate_code_verifier


class OBIAuthError(Exception):
    """Base exception for OBI authentication failures."""


class OBIConnectionError(OBIAuthError):
    """Raised when the OBI authentication service cannot be reached."""


class OBIInvalidOTP(OBIAuthError):
    """Raised when the one-time password is rejected."""


class OBIPasswordlessAuth:
    """Perform OBI's email + OTP Authorization Code/PKCE flow."""

    def __init__(self, session: ClientSession) -> None:
        self._session = session
        self._cookies: dict[str, str] = {}
        self._verifier: str | None = None
        self._otp_action: str | None = None

    def _collect_cookies(self, responses: list[Any]) -> None:
        for response in responses:
            for name, morsel in response.cookies.items():
                self._cookies[name] = morsel.value

    async def async_start(self, email: str) -> None:
        """Start the login flow and request an OTP for the email address."""
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
            if response.status >= HTTPStatus.BAD_REQUEST:
                raise OBIAuthError(f"Authorization page returned HTTP {response.status}")
            html = await response.text()
            username_action = first_form_action(html)
            if not username_action:
                raise OBIAuthError("OBI login page did not contain an email form")

            response = await self._session.post(
                urljoin(str(response.url), username_action),
                data={"username": email},
                cookies=self._cookies,
                allow_redirects=True,
            )
            self._collect_cookies([*response.history, response])
            if response.status >= HTTPStatus.BAD_REQUEST:
                raise OBIAuthError(f"Email submission returned HTTP {response.status}")
            html = await response.text()
            otp_action = first_form_action(html)
            if not otp_action:
                raise OBIAuthError("OBI login page did not contain an OTP form")
            self._otp_action = urljoin(str(response.url), otp_action)
        except ClientError as err:
            raise OBIConnectionError("Unable to contact OBI authentication service") from err

    async def async_finish(self, otp: str) -> dict[str, Any]:
        """Submit the OTP and exchange the authorization code for tokens."""
        if not self._otp_action or not self._verifier:
            raise OBIAuthError("Authentication flow was not started")

        try:
            response = await self._session.post(
                self._otp_action,
                data={"code": otp},
                cookies=self._cookies,
                allow_redirects=False,
            )
            self._collect_cookies([response])

            location = response.headers.get("Location")
            if location:
                response.release()
            if not location:
                # Keycloak usually returns the OTP page again after an invalid code.
                if response.status < HTTPStatus.BAD_REQUEST:
                    html = await response.text()
                    action = first_form_action(html)
                    if action:
                        self._otp_action = urljoin(str(response.url), action)
                raise OBIInvalidOTP("The one-time code was rejected or expired")

            current_url = str(response.url)
            redirects = 0
            while not location.startswith(REDIRECT_URI):
                redirects += 1
                if redirects > 10:
                    raise OBIAuthError("Too many redirects during OBI login")
                next_url = urljoin(current_url, location)
                response = await self._session.get(
                    next_url,
                    cookies=self._cookies,
                    allow_redirects=False,
                )
                self._collect_cookies([response])
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
            if response.status >= HTTPStatus.BAD_REQUEST:
                raise OBIAuthError(f"Token exchange returned HTTP {response.status}")
            token = await response.json()
        except ClientError as err:
            raise OBIConnectionError("Unable to complete OBI authentication") from err

        if not isinstance(token, Mapping) or not token.get("access_token"):
            raise OBIAuthError("OBI token response did not include an access token")

        result = dict(token)
        if "expires_in" in result:
            result["expires_in"] = int(result["expires_in"])
            result["expires_at"] = time.time() + result["expires_in"]
        return result
