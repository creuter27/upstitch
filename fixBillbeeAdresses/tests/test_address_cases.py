"""
Deterministic address validation tests.

Each case in tests/address_cases.yaml is run against:
  1. check_address.check()          — issue detection
  2. _deterministic_suggestion()    — rule-based fix

Geocode-dependent behaviour (OpenCage suggestions, sub-locality detection,
company-as-street verification) is NOT tested here — those require live API
calls and are marked separately as integration tests.

Run:
    .venv/bin/python -m pytest tests/ -v
"""

import sys
from pathlib import Path

import pytest
import yaml

# Make sure execution/ modules are importable
sys.path.insert(0, str(Path(__file__).parent.parent))

from execution.check_address import check as check_address
# Import the private helper via module import
import main as _main


# ---------------------------------------------------------------------------
# Load test cases
# ---------------------------------------------------------------------------

_CASES_FILE = Path(__file__).parent / "address_cases.yaml"


def _load_cases():
    with open(_CASES_FILE, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data["cases"]


def _case_id(case):
    return case.get("description", "?")


ALL_CASES = _load_cases()


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _addr(case_addr: dict) -> dict:
    """Fill in optional fields so check_address doesn't choke on missing keys."""
    defaults = {
        "FirstName": "Test",
        "LastName": "User",
        "Company": "",
        "Street": "",
        "HouseNumber": "",
        "AddressAddition": "",
        "Zip": "12345",
        "City": "Berlin",
        "State": "",
        "CountryISO2": "DE",
    }
    return {**defaults, **case_addr}


# ---------------------------------------------------------------------------
# Test 1: issue detection
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("case", ALL_CASES, ids=[_case_id(c) for c in ALL_CASES])
def test_check_address_issues(case):
    """check_address.check() must return at least the expected issue codes."""
    addr = _addr(case["addr"])
    issues = check_address(addr)
    found_codes = {i.code for i in issues}
    expected = set(case.get("expected_issues") or [])

    missing = expected - found_codes
    assert not missing, (
        f"Expected issue(s) {missing} not found.\n"
        f"  addr:   {case['addr']}\n"
        f"  found:  {found_codes}"
    )


# ---------------------------------------------------------------------------
# Test 2: deterministic fix
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("case", ALL_CASES, ids=[_case_id(c) for c in ALL_CASES])
def test_deterministic_suggestion(case):
    """_deterministic_suggestion() must produce at least the expected fix fields."""
    expected_fix = case.get("expected_fix") or {}
    if not expected_fix:
        pytest.skip("No deterministic fix expected for this case")

    addr = _addr(case["addr"])
    issues = check_address(addr)
    actual_fix = _main._deterministic_suggestion(addr, issues)

    for field, expected_val in expected_fix.items():
        assert field in actual_fix, (
            f"Field '{field}' missing from fix.\n"
            f"  addr:   {case['addr']}\n"
            f"  issues: {[i.code for i in issues]}\n"
            f"  fix:    {actual_fix}"
        )
        assert actual_fix[field] == expected_val, (
            f"Field '{field}': expected {expected_val!r}, got {actual_fix[field]!r}.\n"
            f"  addr:   {case['addr']}\n"
            f"  fix:    {actual_fix}"
        )


# ---------------------------------------------------------------------------
# Test 3: street normalisation helper
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("original,suggested,expected", [
    ("Hallgarter Str.", "Hallgarter Straße", True),
    ("Hallgarter Str.", "Hallgarter Strasse", True),
    ("Riswicker Straße", "Riswicker Straße", True),
    ("Riswickerstraße", "Riswicker Straße", True),   # word split
    ("Hauptstr.", "Hauptstraße", True),
    ("Hauptstraße", "Nebenstraße", False),            # genuinely different
    ("", "Hauptstraße", False),
    ("Hauptstraße", "", False),
])
def test_is_street_normalization(original, suggested, expected):
    result = _main._is_street_normalization(original, suggested)
    assert result == expected, (
        f"_is_street_normalization({original!r}, {suggested!r}) "
        f"returned {result}, expected {expected}"
    )
