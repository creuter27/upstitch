"""
Rule-based address validation for German/European delivery addresses.

Pure functions — no I/O, no side effects.
Each check returns an Issue namedtuple describing the problem and a hint for the fixer.

Usage:
    from execution.check_address import check
    issues = check(shipping_address_dict)
    for issue in issues:
        print(issue.code, issue.description, issue.hint)
"""

import re
from dataclasses import dataclass


# ─── Street suffixes common in German/Austrian/Swiss addresses ────────────────
# Used to detect when a company field actually contains a street name
_STREET_SUFFIXES = re.compile(
    r"\b(str\.|straße|strasse|weg|gasse|platz|allee|ring|damm|chaussee|"
    r"promenade|pfad|steig|stieg|zeile|graben|ufer|gässchen|gäßchen|"
    r"avenue|rue|laan|straat|loon|dreef|dijk)\b",
    re.IGNORECASE,
)

# Detects and parses a house number (optionally followed by floor/addition text) embedded
# in a street string.  Uses \b after the number so "26 Eg" doesn't get confused with "26E".
# Group 1 = street name, Group 2 = house number, Group 3 = trailing text (floor/addition).
# Examples:
#   "Hauptstraße 26"       → ("Hauptstraße", "26", "")
#   "Musterweg 26a"        → ("Musterweg",   "26a", "")
#   "Musterweg 26 Eg"      → ("Musterweg",   "26",  "Eg")
#   "Berliner Str. 14b EG" → ("Berliner Str.", "14b", "EG")
_STREET_HOUSENUMBER_RE = re.compile(
    r"^(.*?)\s+(\d+[-–]?\d*[a-zA-Z]?)\b\s*(.*?)\s*$"
)

# Matches when the string IS JUST a house number (for addition field detection)
_PURE_HOUSE_NUMBER = re.compile(r"^\s*\d+\s*[-–]?\s*\d*\s*[a-zA-Z]?\s*$")

# Matches a house number at the START of a street string, e.g. "26 Auenweg"
# Group 1 = house number, Group 2 = street name
_HOUSENUMBER_AT_START_RE = re.compile(r"^(\d+[-–]?\d*[a-zA-Z]?)\s+(.+)$")

# Matches when the ENTIRE street field is just a house number with no street name,
# e.g. Street="36" or Street="14a" — the number was entered in the wrong field.
_STREET_IS_PURE_NUMBER_RE = re.compile(r"^\d+[-–]?\d*[a-zA-Z]?\s*$")

# Detects a country-code prefix on a ZIP, e.g. "D-86899" or "DE86899"
# Group 1 = prefix letters, Group 2 = the remaining digits/dash part
_ZIP_COUNTRY_PREFIX_RE = re.compile(r'^([A-Za-z]{1,3})-?(\d[\d\s-]*)$')

# German/Austrian/Swiss ZIP code formats
_ZIP_PATTERNS = {
    "DE": re.compile(r"^\d{5}$"),
    "AT": re.compile(r"^\d{4}$"),
    "CH": re.compile(r"^\d{4}$"),
    "NL": re.compile(r"^\d{4}\s?[A-Z]{2}$", re.IGNORECASE),
    "BE": re.compile(r"^\d{4}$"),
    "FR": re.compile(r"^\d{5}$"),
    "IT": re.compile(r"^\d{5}$"),
    "ES": re.compile(r"^\d{5}$"),
    "PL": re.compile(r"^\d{2}-\d{3}$"),
    "CZ": re.compile(r"^\d{3}\s?\d{2}$"),
    "LU": re.compile(r"^\d{4}$"),
}


@dataclass
class Issue:
    code: str           # machine-readable identifier
    description: str    # human-readable explanation
    hint: str           # what to look at / how to fix


def check(addr: dict) -> list[Issue]:
    """
    Run all address checks and return a list of Issue objects.
    addr keys (all optional, Billbee ShippingAddress fields):
        FirstName, LastName, Company, Street, HouseNumber,
        AddressAddition, Zip, City, State, CountryISO2
    """
    issues: list[Issue] = []

    street = (addr.get("Street") or "").strip()
    house_number = (addr.get("HouseNumber") or "").strip()
    addition = (addr.get("AddressAddition") or "").strip()
    company = (addr.get("Company") or "").strip()
    zip_code = (addr.get("Zip") or "").strip()
    city = (addr.get("City") or "").strip()
    country = (addr.get("CountryISO2") or "DE").strip().upper()

    # 1. House number (and optional floor text) embedded in street name.
    # Also fires when an existing house_number starts with "/" (Austrian staircase code like "/4"),
    # because in that case the real house number is still embedded in the street field.
    if street and (not house_number or house_number.startswith("/")):
        m = _STREET_HOUSENUMBER_RE.match(street)
        if m and m.group(1).strip():
            clean_street = m.group(1).strip()
            hn = m.group(2).strip()
            floor = m.group(3).strip()
            floor_hint = f" Trailing text '{floor}' → AddressAddition." if floor else ""
            issues.append(Issue(
                code="HOUSE_NUMBER_IN_STREET",
                description=(
                    f"Street '{street}' contains an embedded house number. "
                    f"Suggested split: Street='{clean_street}', HouseNumber='{hn}'."
                    + (f" AddressAddition='{floor}'." if floor else "")
                ),
                hint=(
                    f"Set Street='{clean_street}', HouseNumber='{hn}'"
                    + (f", AddressAddition='{floor}'" if floor else "")
                    + f".{floor_hint}"
                ),
            ))

    # 1c. Street field is ONLY a house number with no street name, e.g. Street="36"
    if street and not house_number and _STREET_IS_PURE_NUMBER_RE.match(street):
        issues.append(Issue(
            code="STREET_IS_HOUSE_NUMBER",
            description=(
                f"Street '{street}' is only a number — it looks like a house number "
                f"entered in the wrong field with no street name provided."
            ),
            hint=(
                f"Move '{street}' from Street to HouseNumber. "
                f"The street name may be in the Company or AddressAddition field, "
                f"or needs to be entered manually."
            ),
        ))

    # 1b. House number at the START of the street field, e.g. "26 Auenweg"
    if street and not house_number:
        m_start = _HOUSENUMBER_AT_START_RE.match(street)
        # Only fire if rule 1 (number at end) did NOT already fire
        m_end = _STREET_HOUSENUMBER_RE.match(street)
        end_fired = bool(m_end and m_end.group(1).strip())
        if m_start and not end_fired:
            hn = m_start.group(1).strip()
            clean_street = m_start.group(2).strip()
            issues.append(Issue(
                code="HOUSE_NUMBER_AT_START_OF_STREET",
                description=(
                    f"Street '{street}' starts with a house number. "
                    f"Suggested split: Street='{clean_street}', HouseNumber='{hn}'."
                ),
                hint=f"Set Street='{clean_street}', HouseNumber='{hn}'.",
            ))

    # 2. House number in AddressAddition field
    if addition and not house_number:
        if _PURE_HOUSE_NUMBER.match(addition):
            issues.append(Issue(
                code="HOUSE_NUMBER_IN_ADDITION",
                description=f"AddressAddition '{addition}' looks like a house number.",
                hint="Move the value from AddressAddition to HouseNumber and clear AddressAddition (or keep it if it also contains real addition info).",
            ))

    # 3. Street name entered in Company field
    if company and not street:
        if _STREET_SUFFIXES.search(company):
            issues.append(Issue(
                code="STREET_IN_COMPANY",
                description=f"Company field '{company}' looks like a street address (contains a street suffix).",
                hint="Move the street name from Company to Street (and HouseNumber if embedded). Clear Company if it was just the street.",
            ))

    # 4. Street name entered in Company field even when street exists.
    #    Fires when:
    #      (a) Company contains a recognised street suffix (catches "Gartenweg 3"), OR
    #      (b) Company parses as StreetName+HouseNumber — catches German compound names
    #          like "Untersbergstraße 6" where the suffix is embedded in the word and
    #          \b boundaries inside _STREET_SUFFIXES don't match.
    if company and street:
        _is_business = any(
            w in company.lower() for w in ["gmbh", "ag", "kg", "e.v.", "ug", "ltd", "inc", "bv", "nv", "sarl"]
        )
        if not _is_business:
            _company_looks_like_street = bool(_STREET_SUFFIXES.search(company))
            if not _company_looks_like_street:
                _m_co = _STREET_HOUSENUMBER_RE.match(company)
                _company_looks_like_street = bool(_m_co and _m_co.group(1).strip())
            if _company_looks_like_street:
                issues.append(Issue(
                    code="STREET_IN_COMPANY_WITH_STREET_FILLED",
                    description=f"Company field '{company}' looks like a street address, but Street is also filled with '{street}'.",
                    hint="Check if Company should really be a company name or if this is a street that got entered twice / in the wrong field.",
                ))

    # 5. Missing ZIP code / country prefix / invalid format
    if not zip_code:
        issues.append(Issue(
            code="MISSING_ZIP",
            description="ZIP code (Zip field) is empty.",
            hint="Add the correct ZIP/postal code.",
        ))
    else:
        # Check for country-code prefix first (e.g. D-86899, CH-8001, D86899)
        m = _ZIP_COUNTRY_PREFIX_RE.match(zip_code)
        stripped = m.group(2).strip() if m else None
        if stripped and country in _ZIP_PATTERNS and _ZIP_PATTERNS[country].match(stripped):
            issues.append(Issue(
                code="ZIP_HAS_COUNTRY_PREFIX",
                description=f"ZIP '{zip_code}' has a country-code prefix. Stripped: '{stripped}'.",
                hint=f"Remove the prefix — set Zip='{stripped}'.",
            ))
        elif country in _ZIP_PATTERNS and not _ZIP_PATTERNS[country].match(zip_code):
            issues.append(Issue(
                code="INVALID_ZIP_FORMAT",
                description=f"ZIP code '{zip_code}' does not match expected format for {country}.",
                hint=f"Correct the ZIP code to match the format for country {country}.",
            ))

    # 6. Missing city
    if not city:
        issues.append(Issue(
            code="MISSING_CITY",
            description="City field is empty.",
            hint="Add the city name.",
        ))

    # 7. Missing street entirely (and no company that looks like a street)
    if not street and not company:
        issues.append(Issue(
            code="MISSING_STREET",
            description="Street field is empty and Company field is also empty.",
            hint="Add the street name and house number.",
        ))

    # 8. Street filled but house number completely missing (not caught by rules 1/1b/1c)
    if street and not house_number:
        m = _STREET_HOUSENUMBER_RE.match(street)
        m_start = _HOUSENUMBER_AT_START_RE.match(street)
        is_pure_number = bool(_STREET_IS_PURE_NUMBER_RE.match(street))
        if not (m and m.group(1).strip()) and not m_start and not is_pure_number:
            # None of the earlier rules fired — no number detectable in street at all
            issues.append(Issue(
                code="MISSING_HOUSE_NUMBER",
                description=f"Street '{street}' is filled but HouseNumber is empty and no number found in street.",
                hint="Add the house number to the HouseNumber field.",
            ))

    # 9. HouseNumber equals ZIP code — the real house number is missing; it may
    #    be embedded in the Street field along with the street name.
    if house_number and zip_code and house_number == zip_code:
        issues.append(Issue(
            code="HOUSE_NUMBER_EQUALS_ZIP",
            description=(
                f"HouseNumber '{house_number}' equals the ZIP code '{zip_code}' — "
                f"this is almost certainly a data-entry error. The real house number "
                f"may be embedded in the Street field ('{street}')."
            ),
            hint=(
                f"Parse the real house number from Street ('{street}') and "
                f"set HouseNumber to that value."
            ),
        ))

    # 10. Street is a single letter AND Company contains a full street address.
    #     Pattern: customer entered street initial in Street (e.g. "A"), real
    #     street+number is in Company (e.g. "Geerenstrasse 24").
    if street and len(street) == 1 and street.isalpha() and company:
        m_co = _STREET_HOUSENUMBER_RE.match(company)
        if m_co and m_co.group(1).strip():
            issues.append(Issue(
                code="STREET_IS_SINGLE_LETTER_COMPANY_HAS_ADDRESS",
                description=(
                    f"Street field contains only a single letter '{street}', "
                    f"while Company '{company}' looks like a full street address. "
                    f"Suggested: Street='{m_co.group(1).strip()}', "
                    f"HouseNumber='{m_co.group(2).strip()}', "
                    f"AddressAddition from original Street+HouseNumber, Company=''."
                ),
                hint=(
                    f"Parse Company as the real street address. "
                    f"Move original Street ('{street}') and HouseNumber to AddressAddition."
                ),
            ))

    return issues


def strip_zip_prefix(zip_code: str) -> str | None:
    """
    Strip a country-code prefix from a ZIP code (e.g. 'D-86899' → '86899').
    Returns the stripped value, or None if no prefix pattern is detected.
    """
    m = _ZIP_COUNTRY_PREFIX_RE.match(zip_code)
    return m.group(2).strip() if m else None


def parse_housenumber_at_start(street: str) -> tuple[str, str] | None:
    """
    Try to split a street string where the house number is at the start,
    e.g. "26 Auenweg" → ("26", "Auenweg").

    Returns (house_number, street_name) or None if not matched.
    """
    m = _HOUSENUMBER_AT_START_RE.match(street.strip())
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return None


def parse_street_housenumber_floor(street: str) -> tuple[str, str, str] | None:
    """
    Try to split a street string into (street_name, house_number, floor_addition).

    Returns None if no house number pattern detected.

    Examples:
        "Hauptstraße 26"       → ("Hauptstraße", "26", "")
        "Musterweg 26 Eg"      → ("Musterweg",   "26", "Eg")
        "Berliner Str. 14b EG" → ("Berliner Str.", "14b", "EG")
    """
    m = _STREET_HOUSENUMBER_RE.match(street.strip())
    if m and m.group(1).strip():
        return m.group(1).strip(), m.group(2).strip(), m.group(3).strip()
    return None
