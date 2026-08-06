"""Tests for pure OBI utility helpers."""

from pathlib import Path
import importlib.util

MODULE_PATH = (
    Path(__file__).parents[1]
    / "custom_components"
    / "obi_energy_tracker"
    / "util.py"
)
spec = importlib.util.spec_from_file_location("obi_util", MODULE_PATH)
assert spec and spec.loader
obi_util = importlib.util.module_from_spec(spec)
spec.loader.exec_module(obi_util)


def test_pkce() -> None:
    verifier = obi_util.generate_code_verifier()
    assert 43 <= len(verifier) <= 128
    challenge = obi_util.compute_code_challenge(verifier)
    assert "=" not in challenge


def test_form_action() -> None:
    html = '<html><form action="https://example.test/x?a=1&amp;b=2"></form></html>'
    assert obi_util.first_form_action(html) == "https://example.test/x?a=1&b=2"


def test_latest_record_and_conversion() -> None:
    records = [
        {"time": "2026-01-01 00:00:00.000000000", "value": 1000},
        {"time": "2026-01-01 00:05:00.000000000", "value": 1500},
    ]
    assert obi_util.latest_record(records)["value"] == 1500
    assert obi_util.wh_to_kwh(1500) == 1.5


def test_latest_record_ignores_malformed_entries() -> None:
    records = [
        {"time": "2026-01-01 00:00:00.000000000", "value": 1000},
        {"time": "2026-01-01 00:05:00.000000000", "value": "not-a-number"},
        {"value": 2000},
        "not-a-dict",
        {"time": "2026-01-01 00:10:00.000000000", "value": 3000},
    ]
    assert obi_util.latest_record(records)["value"] == 3000


def test_latest_record_returns_none_for_no_valid_entries() -> None:
    assert obi_util.latest_record([]) is None
    assert obi_util.latest_record([{"value": "bad"}]) is None


def test_wh_to_kwh_handles_zero_and_none() -> None:
    assert obi_util.wh_to_kwh(0) == 0.0
    assert obi_util.wh_to_kwh(None) is None


def test_parse_obi_timestamp_truncates_nanosecond_fraction() -> None:
    # TimestreamRecordDTO.time example format: space-separated, 9-digit
    # (nanosecond) fraction, no timezone.
    result = obi_util.parse_obi_timestamp("2024-08-19 00:00:00.000000000")
    assert result is not None
    assert result.year == 2024
    assert result.month == 8
    assert result.day == 19
    assert result.microsecond == 0


def test_parse_obi_timestamp_truncates_nanoseconds_with_nonzero_microseconds() -> None:
    result = obi_util.parse_obi_timestamp("2024-08-19 00:00:00.123456789")
    assert result is not None
    assert result.microsecond == 123456


def test_parse_obi_timestamp_handles_z_suffix() -> None:
    # BridgeV2DTO/SensorV2DTO claimedAt example format: ISO 8601 with a Z suffix.
    result = obi_util.parse_obi_timestamp("2024-12-11T14:40:19.442Z")
    assert result is not None
    assert result.utcoffset().total_seconds() == 0


def test_parse_obi_timestamp_handles_z_suffix_with_nanosecond_fraction() -> None:
    result = obi_util.parse_obi_timestamp("2024-08-19 00:00:00.000000000Z")
    assert result is not None
    assert result.utcoffset().total_seconds() == 0


def test_parse_obi_timestamp_handles_explicit_offset() -> None:
    result = obi_util.parse_obi_timestamp("2024-08-19 00:00:00.000000000+02:00")
    assert result is not None
    assert result.utcoffset().total_seconds() == 7200


def test_parse_obi_timestamp_handles_no_fractional_seconds() -> None:
    result = obi_util.parse_obi_timestamp("2024-08-19 00:00:00")
    assert result is not None
    assert result.microsecond == 0


def test_parse_obi_timestamp_returns_none_for_empty_or_missing_value() -> None:
    assert obi_util.parse_obi_timestamp(None) is None
    assert obi_util.parse_obi_timestamp("") is None


def test_parse_obi_timestamp_returns_none_for_garbage() -> None:
    assert obi_util.parse_obi_timestamp("not-a-timestamp") is None
    assert obi_util.parse_obi_timestamp("2024-13-99 99:99:99.000000000") is None
