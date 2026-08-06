"""Pure utility helpers for OBI Energy Tracker."""

from __future__ import annotations

import base64
from collections.abc import Iterable
from datetime import datetime
import hashlib
from html import unescape
from html.parser import HTMLParser
import secrets
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
