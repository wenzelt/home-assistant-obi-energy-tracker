"""Pure utility helpers for OBI Energy Tracker."""

from __future__ import annotations

import base64
import hashlib
import secrets
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
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


def _record_sort_key(record: dict[str, Any]) -> tuple[int, Any]:
    """Order records chronologically, falling back to the raw string.

    Offset-free timestamps are treated as UTC so they compare with aware ones.
    """
    parsed = parse_obi_timestamp(record["time"])
    if parsed is None:
        return (0, record["time"])
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return (1, parsed.timestamp())


def valid_records(records: Iterable[Any]) -> list[dict[str, Any]]:
    """Return well-formed API records sorted from oldest to newest."""
    valid = [
        item
        for item in records
        if isinstance(item, dict)
        and isinstance(item.get("time"), str)
        and isinstance(item.get("value"), (int, float))
        and not isinstance(item.get("value"), bool)
    ]
    return sorted(valid, key=_record_sort_key)


def latest_record(records: Iterable[Any]) -> dict[str, Any] | None:
    """Return the newest valid record from an API record sequence."""
    valid = valid_records(records)
    return valid[-1] if valid else None


def average_power_w(records: Iterable[Any]) -> float | None:
    """Derive average power (W) between the two newest cumulative Wh records.

    Returns ``None`` when there are fewer than two records, the timestamps
    cannot be compared, or the counter decreased (reset or replaced meter).
    """
    valid = valid_records(records)
    if len(valid) < 2:
        return None
    previous, latest = valid[-2], valid[-1]
    start = parse_obi_timestamp(previous["time"])
    end = parse_obi_timestamp(latest["time"])
    if start is None or end is None:
        return None
    if start.tzinfo is None:
        start = start.replace(tzinfo=UTC)
    if end.tzinfo is None:
        end = end.replace(tzinfo=UTC)
    seconds = (end - start).total_seconds()
    delta_wh = float(latest["value"]) - float(previous["value"])
    if seconds <= 0 or delta_wh < 0:
        return None
    return round(delta_wh * 3600.0 / seconds, 1)


def record_time_utc(record: dict[str, Any]) -> datetime | None:
    """Return a record's timestamp as an aware UTC datetime.

    Offset-free OBI timestamps are treated as UTC.
    """
    parsed = parse_obi_timestamp(record.get("time"))
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


@dataclass
class HourlyEnergy:
    """One completed hour of a cumulative Wh counter."""

    start: datetime
    state_wh: float
    sum_wh: float
    last_time: datetime


def hourly_energy(
    records: Iterable[Any],
    *,
    after: datetime | None,
    until: datetime,
    last_value_wh: float | None,
    last_sum_wh: float,
) -> list[HourlyEnergy]:
    """Fold cumulative Wh counter records into hourly statistics.

    Only records at or after ``after`` and strictly before ``until`` are used,
    so ``until`` should be the start of the current, still-open hour. ``sum``
    grows by each counter increase; a decrease is treated as a counter reset,
    where the new value is counted as consumption since the reset. Without a
    previous value, the first record only sets the baseline.
    """
    hours: dict[datetime, HourlyEnergy] = {}
    previous = last_value_wh
    total = last_sum_wh
    for record in valid_records(records):
        timestamp = record_time_utc(record)
        if timestamp is None or timestamp >= until:
            continue
        if after is not None and timestamp < after:
            continue
        value = float(record["value"])
        if previous is not None:
            total += value - previous if value >= previous else value
        previous = value
        hour = timestamp.replace(minute=0, second=0, microsecond=0)
        hours[hour] = HourlyEnergy(
            start=hour, state_wh=value, sum_wh=total, last_time=timestamp
        )
    return [hours[hour] for hour in sorted(hours)]


def history_windows(
    start: datetime, end: datetime, step: timedelta
) -> list[str]:
    """Split [start, end) into ISO 8601 ``start/duration`` query windows."""
    days = step.days
    if days < 1 or step != timedelta(days=days):
        raise ValueError("step must be a whole number of days")
    windows: list[str] = []
    current = start.astimezone(UTC)
    while current < end:
        windows.append(f"{current:%Y-%m-%dT%H:%M:%SZ}/P{days}D")
        current += step
    return windows


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
