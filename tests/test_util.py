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
