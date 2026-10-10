"""Tests for pure OBI utility helpers."""

import pytest
from conftest import import_submodule

obi_util = import_submodule("util")


def test_pkce() -> None:
    verifier = obi_util.generate_code_verifier()
    assert 43 <= len(verifier) <= 128
    challenge = obi_util.compute_code_challenge(verifier)
    assert "=" not in challenge


def test_generate_code_verifier_rejects_out_of_range_length() -> None:
    with pytest.raises(ValueError):
        obi_util.generate_code_verifier(length=42)
    with pytest.raises(ValueError):
        obi_util.generate_code_verifier(length=129)


def test_compute_code_challenge_rejects_out_of_range_verifier() -> None:
    with pytest.raises(ValueError):
        obi_util.compute_code_challenge("short")


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


def test_latest_record_orders_by_time_not_string() -> None:
    records = [
        {"time": "2024-08-19T02:00:00+02:00", "value": 2},  # 00:00 UTC
        {"time": "2024-08-19T01:00:00Z", "value": 3},
    ]
    assert obi_util.latest_record(records)["value"] == 3


def test_average_power_from_two_newest_records() -> None:
    records = [
        {"time": "2024-08-19 00:00:00.000000000", "value": 1000},
        {"time": "2024-08-19 00:10:00.000000000", "value": 1100},
        {"time": "2024-08-19 00:05:00.000000000", "value": 1050},
    ]
    # 50 Wh over five minutes = 600 W.
    assert obi_util.average_power_w(records) == 600.0


def test_average_power_is_unknown_without_a_usable_interval() -> None:
    assert obi_util.average_power_w([]) is None
    single = [{"time": "2024-08-19T00:00:00Z", "value": 1}]
    assert obi_util.average_power_w(single) is None
    same_time = [
        {"time": "2024-08-19T00:00:00Z", "value": 1},
        {"time": "2024-08-19T00:00:00Z", "value": 2},
    ]
    assert obi_util.average_power_w(same_time) is None
    counter_reset = [
        {"time": "2024-08-19T00:00:00Z", "value": 500},
        {"time": "2024-08-19T00:05:00Z", "value": 10},
    ]
    assert obi_util.average_power_w(counter_reset) is None


def _utc(text: str):
    from datetime import datetime

    return datetime.fromisoformat(text)


def test_hourly_energy_buckets_cumulative_counter() -> None:
    records = [
        {"time": "2024-08-19 00:00:00.000000000", "value": 1000},
        {"time": "2024-08-19 00:30:00.000000000", "value": 1100},
        {"time": "2024-08-19 01:10:00.000000000", "value": 1400},
        {"time": "2024-08-19 01:50:00.000000000", "value": 1500},
        {"time": "2024-08-19 02:05:00.000000000", "value": 1600},  # open hour
    ]
    hours = obi_util.hourly_energy(
        records,
        after=None,
        until=_utc("2024-08-19T02:00:00+00:00"),
        last_value_wh=None,
        last_sum_wh=0.0,
    )
    assert [(h.start.hour, h.state_wh, h.sum_wh) for h in hours] == [
        (0, 1100, 100),
        (1, 1500, 500),
    ]
    assert hours[-1].last_time == _utc("2024-08-19T01:50:00+00:00")


def test_hourly_energy_continues_from_previous_import() -> None:
    records = [
        {"time": "2024-08-19T00:30:00Z", "value": 1100},  # already imported
        {"time": "2024-08-19T01:15:00Z", "value": 1300},
    ]
    hours = obi_util.hourly_energy(
        records,
        after=_utc("2024-08-19T01:00:00+00:00"),
        until=_utc("2024-08-19T02:00:00+00:00"),
        last_value_wh=1100,
        last_sum_wh=100,
    )
    assert [(h.state_wh, h.sum_wh) for h in hours] == [(1300, 300)]


def test_hourly_energy_counts_value_after_counter_reset() -> None:
    records = [
        {"time": "2024-08-19T00:10:00Z", "value": 5000},
        {"time": "2024-08-19T00:20:00Z", "value": 5200},
        {"time": "2024-08-19T00:30:00Z", "value": 40},
    ]
    hours = obi_util.hourly_energy(
        records,
        after=None,
        until=_utc("2024-08-19T01:00:00+00:00"),
        last_value_wh=None,
        last_sum_wh=0.0,
    )
    assert hours[0].sum_wh == 240


def test_history_windows_cover_range_in_whole_days() -> None:
    from datetime import timedelta

    windows = obi_util.history_windows(
        _utc("2024-08-01T05:00:00+00:00"),
        _utc("2024-08-16T00:00:00+00:00"),
        timedelta(days=7),
    )
    assert windows == [
        "2024-08-01T05:00:00Z/P7D",
        "2024-08-08T05:00:00Z/P7D",
        "2024-08-15T05:00:00Z/P7D",
    ]
