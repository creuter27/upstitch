"""
Geocode a delivery address using the OpenCage Geocoding API.

When the full query yields low confidence (< 6), retries with progressively
stripped queries to work around misspelled cities or abbreviated street names:
  1. Full query  (street + house + ZIP + city + country)
  2. Without city  (lets the ZIP code carry the lookup)
  3. Without street  (ZIP + city + country only)
The highest-confidence result is returned.

Free tier: 2,500 requests/day — sufficient for typical order volumes.
Sign up at: https://opencagedata.com/

Usage:
    from execution.geocode_address import geocode
    result = geocode(shipping_address_dict)
    # result = {
    #   "confidence": 9,         # 0-10 (10 = highest confidence)
    #   "formatted": "Hauptstraße 42, 10115 Berlin, Germany",
    #   "components": {...},     # parsed address components from OpenCage
    # }
    # Returns None if the API call fails or no result found.
"""

import os
import time

import requests

OPENCAGE_API_URL = "https://api.opencagedata.com/geocode/v1/json"

# Retry with fallback queries when confidence is below this threshold
_LOW_CONFIDENCE = 6


def _addr_to_string(addr: dict) -> str:
    """Format address dict into a single geocoding query string."""
    parts = []
    street = (addr.get("Street") or "").strip()
    house = (addr.get("HouseNumber") or "").strip()
    addition = (addr.get("AddressAddition") or "").strip()
    zip_code = (addr.get("Zip") or "").strip()
    city = (addr.get("City") or "").strip()
    country = (addr.get("CountryISO2") or "").strip()

    if street and house:
        parts.append(f"{street} {house}")
    elif street:
        parts.append(street)
    if addition:
        parts.append(addition)
    if zip_code:
        parts.append(zip_code)
    if city:
        parts.append(city)
    if country:
        parts.append(country)

    return ", ".join(parts)


def _api_call(query: str, key: str) -> dict | None:
    """Make a single OpenCage API call. Returns a result dict or None."""
    if not query.strip():
        return None
    try:
        resp = requests.get(
            OPENCAGE_API_URL,
            params={
                "q": query,
                "key": key,
                "limit": 1,
                "no_annotations": 1,
                "language": "native",  # use local language of the location, not German
            },
            timeout=10,
        )
        if resp.status_code == 401:
            os.environ["OPENCAGE_API_KEY"] = ""
            print("[geocode] 401 Unauthorized — OpenCage API key is invalid. "
                  "Geocoding disabled for this session. Add a valid key to .env.")
            return None
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"[geocode] API call failed: {e}")
        return None

    # Rate limit: OpenCage free tier = 1 req/sec recommended
    time.sleep(1.1)

    results = data.get("results", [])
    if not results:
        return None

    top = results[0]
    return {
        "confidence": top.get("confidence", 0),
        "formatted": top.get("formatted", ""),
        "components": top.get("components", {}),
    }


def geocode(addr: dict, api_key: str | None = None) -> dict | None:
    """
    Geocode an address dict using OpenCage.

    When the full query yields low confidence (< 6), retries with:
      - Street + ZIP + country  (city dropped — misspelled city is a common failure)
      - ZIP + country only      (street dropped — abbreviated street is another failure)
    Returns the highest-confidence result found.

    Parameters
    ----------
    addr : dict
        Billbee ShippingAddress dict.
    api_key : str, optional
        OpenCage API key. Falls back to OPENCAGE_API_KEY env var.

    Returns
    -------
    dict with keys: confidence, formatted, components
    Returns None if geocoding fails or yields no result.
    """
    key = api_key or os.environ.get("OPENCAGE_API_KEY", "")
    if not key or key.startswith("your-"):
        print("[geocode] OPENCAGE_API_KEY not configured — skipping geocoding")
        return None

    # 1. Full query
    full_query = _addr_to_string(addr)
    result = _api_call(full_query, key)
    if result and result.get("confidence", 0) >= _LOW_CONFIDENCE:
        return result

    best = result  # may be None or low-confidence

    # 2. Retry without city (misspelled city is the most common failure)
    city = (addr.get("City") or "").strip()
    if city:
        no_city_query = _addr_to_string({**addr, "City": ""})
        if no_city_query != full_query:
            r = _api_call(no_city_query, key)
            if r and r.get("confidence", 0) > (best or {}).get("confidence", 0):
                best = r
            if best and best.get("confidence", 0) >= _LOW_CONFIDENCE:
                return best

    # 3. Retry without street (abbreviated/wrong street name)
    street = (addr.get("Street") or "").strip()
    if street:
        no_street_query = _addr_to_string({**addr, "Street": "", "HouseNumber": "", "AddressAddition": ""})
        if no_street_query not in (full_query, _addr_to_string({**addr, "City": ""})):
            r = _api_call(no_street_query, key)
            if r and r.get("confidence", 0) > (best or {}).get("confidence", 0):
                best = r

    return best
