"""
Package type (Verpackungstyp) management.

  - Reads available package types from data/package_types.yaml (always fresh, no cache)
  - Maintains data/package_type_mapping.json: combo_key → {name, id, set_at}
    combo_key format: "Manufacturer|Category|Size|Qty + ..." (sorted, qty summed per group)

Usage:
    from execution.package_type_store import (
        fetch_package_types, combo_key, get_for_combo, set_for_combo
    )
    pkg_types = fetch_package_types(client)
    key = combo_key(resolved_items)
    known = get_for_combo(resolved_items)   # {name, id} or None
    set_for_combo(resolved_items, name="Luftpolsterumschlag M", pkg_id=7)
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import yaml

MAPPING_FILE = Path(__file__).parent.parent / "data" / "package_type_mapping.json"
FALLBACK_YAML = Path(__file__).parent.parent / "data" / "package_types.yaml"


# ---------------------------------------------------------------------------
# Combo key
# ---------------------------------------------------------------------------

def combo_key(items: list[dict]) -> str:
    """
    Build a stable, sorted string key from the item list.

    Items are first grouped by (manufacturer, category, size) and their
    quantities summed. Format: "Manufacturer|Category|Size|Qty + ..."
    Groups where all of manufacturer/category/size are empty are excluded.
    """
    totals: dict[tuple, float] = {}
    for i in items:
        mfr = i.get("manufacturer") or ""
        cat = i.get("category") or ""
        sz = i.get("size") or ""
        if not (mfr or cat or sz):
            continue
        k = (mfr, cat, sz)
        totals[k] = totals.get(k, 0) + (i.get("quantity") or 0)

    parts = []
    for (mfr, cat, sz), qty in totals.items():
        qty_str = str(int(qty)) if qty == int(qty) else str(qty)
        parts.append(f"{mfr}|{cat}|{sz}|{qty_str}")
    return " + ".join(sorted(parts))


# ---------------------------------------------------------------------------
# Mapping file (combo → package type)
# ---------------------------------------------------------------------------

def load_mapping() -> dict:
    if MAPPING_FILE.exists():
        with open(MAPPING_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_mapping(mapping: dict) -> None:
    MAPPING_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(MAPPING_FILE, "w", encoding="utf-8") as f:
        json.dump(mapping, f, ensure_ascii=False, indent=2)


def get_for_combo(items: list[dict]) -> dict | None:
    """
    Look up the saved package type for this item combination.
    Returns {name, id} or None if not yet known.
    """
    key = combo_key(items)
    if not key:
        return None
    mapping = load_mapping()
    entry = mapping.get(key)
    if entry:
        return {"name": entry.get("name", ""), "id": entry.get("id")}
    return None


def set_for_combo(items: list[dict], name: str, pkg_id=None) -> None:
    """Save the chosen package type for this item combination."""
    key = combo_key(items)
    if not key:
        return
    mapping = load_mapping()
    mapping[key] = {
        "name": name,
        "id": pkg_id,
        "set_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    save_mapping(mapping)


# ---------------------------------------------------------------------------
# Package type discovery (Billbee API + fallback YAML)
# ---------------------------------------------------------------------------

def _load_fallback_yaml() -> list[dict]:
    """
    Read package types from data/package_types.yaml.
    If the file doesn't exist, create a template and return empty list.
    """
    if not FALLBACK_YAML.exists():
        FALLBACK_YAML.parent.mkdir(parents=True, exist_ok=True)
        template = (
            "# Verpackungstypen — local fallback list\n"
            "# Billbee's API does not expose Verpackungstypen, so add yours here.\n"
            "# Format:\n"
            "#   - name: Brief\n"
            "#     id: null  # set to Billbee's internal ID if known\n"
            "package_types:\n"
            "  - name: Brief\n"
            "  - name: Luftpolsterumschlag S\n"
            "  - name: Luftpolsterumschlag M\n"
            "  - name: Luftpolsterumschlag L\n"
            "  - name: Karton XS\n"
            "  - name: Karton S\n"
            "  - name: Karton M\n"
        )
        FALLBACK_YAML.write_text(template, encoding="utf-8")
        print(f"[package_types] Created template at {FALLBACK_YAML} — please review/update it.")

    with open(FALLBACK_YAML, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return [
        {"name": entry.get("name", ""), "id": entry.get("id")}
        for entry in (data or {}).get("package_types", [])
        if entry.get("name")
    ]


def fetch_package_types(client) -> list[dict]:
    """
    Return available package types as [{id, name}].
    Always reads from data/package_types.yaml (no caching).
    """
    return _load_fallback_yaml()
