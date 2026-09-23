"""Pure utility helpers for OBI Energy Tracker."""

from __future__ import annotations

import base64
import hashlib
import secrets
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from html import unescape
from html.parser import HTMLParser
from typing import Any


class _FirstFormParser(HTMLParser):
    """Extract the action of the first HTML form."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.action: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.action is not None or tag.lower() != "form":
            return
        attributes = dict(attrs)
        action = attributes.get("action")
        if action:
            self.action = unescape(action)


def first_form_action(html: str) -> str | None:
    """Return the first form action from an HTML document."""
    parser = _FirstFormParser()
    parser.feed(html)
    return parser.action


@dataclass
class LoginForm:
    """A login form containing the expected field and its hidden values."""

    action: str
    hidden: dict[str, str] = field(default_factory=dict)


class _LoginFormParser(HTMLParser):
    def __init__(self, required_field: str) -> None:
        super().__init__(convert_charrefs=True)
        self.required_field = required_field
        self.result: LoginForm | None = None
        self.current: LoginForm | None = None
        self.has_field = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "form":
            self.current = LoginForm(attributes.get("action") or "")
            self.has_field = False
        elif tag == "input" and self.current is not None:
            name = attributes.get("name")
            if "disabled" in attributes:
                return
            input_type = (attributes.get("type") or "text").lower()
            if name == self.required_field and input_type != "hidden":
                self.has_field = True
            if name and input_type == "hidden":
                self.current.hidden[name] = attributes.get("value") or ""

    def handle_endtag(self, tag: str) -> None:
        if tag == "form":
            if self.result is None and self.current is not None and self.has_field:
                self.result = self.current
            self.current = None


def login_form(html: str, required_field: str) -> LoginForm | None:
    """Select a form by its input, not its position in the page."""
    parser = _LoginFormParser(required_field)
    parser.feed(html)
    parser.close()
    return parser.result


def generate_code_verifier(length: int = 96) -> str:
    """Generate an RFC 7636 code verifier."""
    if not 43 <= length <= 128:
        raise ValueError("PKCE verifier length must be between 43 and 128")
    return secrets.token_urlsafe(96)[:length]


def compute_code_challenge(verifier: str) -> str:
    """Compute an RFC 7636 S256 code challenge."""
    if not 43 <= len(verifier) <= 128:
        raise ValueError("PKCE verifier length must be between 43 and 128")
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def latest_record(records: Iterable[dict[str, Any]]) -> dict[str, Any] | None:
    """Return the newest valid record from an API record sequence."""
    valid = [
        item
        for item in records
        if isinstance(item, dict)
        and isinstance(item.get("time"), str)
        and isinstance(item.get("value"), (int, float))
    ]
    if not valid:
        return None
    return max(valid, key=lambda item: item["time"])


def wh_to_kwh(value: int | float | None) -> float | None:
    """Convert a Wh counter value to kWh."""
    if value is None:
        return None
    return round(float(value) / 1000.0, 6)


def parse_obi_timestamp(value: str | None) -> datetime | None:
    """Parse an OBI timestamp, tolerating nanosecond fractions and absent zones."""
    if not value:
        return None
    normalized = value.replace(" ", "T", 1)
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        # Python supports microseconds. Truncate longer fractional seconds.
        if "." not in normalized:
            return None
        prefix, suffix = normalized.split(".", 1)
        zone_index = min(
            (idx for idx in (suffix.find("+"), suffix.find("-")) if idx >= 0),
            default=-1,
        )
        fraction = suffix if zone_index < 0 else suffix[:zone_index]
        zone = "" if zone_index < 0 else suffix[zone_index:]
        try:
            return datetime.fromisoformat(f"{prefix}.{fraction[:6]}{zone}")
        except ValueError:
            return None
