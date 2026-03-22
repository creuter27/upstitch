"""
Loader for mappings/product_specs.yaml.

Provides per-manufacturer × category × size product attribute lookup.
Used by assign_taric.py (material, countryOfOrigin) and the upload pipeline
(weight, dimensions, price, cost).

Lookup priority:  manufacturer → category → size  (falls back to "default").
All keys are matched case-insensitively.

Manufacturer resolution
-----------------------
The YAML keys use canonical manufacturer codes (e.g. "TRX", "FRE").
When a Mappings instance is supplied, the raw manufacturer string from the
sheet (e.g. "Trixie Baby", "Fresk") is first resolved to its canonical code
via Mappings.canonical_manufacturer() before the YAML lookup.  If no match
is found, the raw string is used as-is (case-insensitive).

Usage:
    from execution.specs_loader import SpecsLoader
    from execution.mappings_loader import Mappings
    specs = SpecsLoader(mappings=Mappings())
    spec = specs.lookup("Trixie Baby", "rucksack", "small")
    # → {"material": "textile", "countryOfOrigin": "CN", ...}
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from execution.mappings_loader import Mappings

SPECS_FILE = Path(__file__).parent.parent / "mappings" / "product_specs.yaml"


class SpecsLoader:
    def __init__(self, path: Path = SPECS_FILE, mappings: "Mappings | None" = None):
        with open(path, encoding="utf-8") as f:
            data: dict = yaml.safe_load(f) or {}

        self._mappings = mappings

        # Normalise all keys to lowercase for case-insensitive lookup.
        # Structure: mfr_lower → cat_lower → size_lower → {field: value}
        self._data: dict[str, dict[str, dict[str, dict]]] = {}
        for mfr, cats in data.items():
            mfr_lower = str(mfr).lower()
            self._data[mfr_lower] = {}
            if not isinstance(cats, dict):
                continue
            for cat, sizes in cats.items():
                cat_lower = str(cat).lower()
                self._data[mfr_lower][cat_lower] = {}
                if not isinstance(sizes, dict):
                    continue
                for sz, attrs in sizes.items():
                    sz_lower = str(sz).lower()
                    self._data[mfr_lower][cat_lower][sz_lower] = attrs or {}

    def _resolve_mfr(self, manufacturer: str) -> str:
        """
        Return the lowercase YAML key for a manufacturer string.

        Resolution order:
        1. If Mappings is available: try canonical_manufacturer() lookup.
           The canonical code (e.g. "TRX") is the key used in product_specs.yaml.
        2. Fall back to the raw string lowercased.
        """
        raw = str(manufacturer).strip()
        if self._mappings:
            canonical = self._mappings.canonical_manufacturer(raw)
            if canonical:
                return canonical.lower()
        return raw.lower()

    def lookup(self, manufacturer: str, category: str, size: str = "") -> dict:
        """
        Return the merged spec for (manufacturer, category, size).

        Merge order: default values ← size-specific overrides (None values in
        the size block are ignored so they don't clobber the default).

        Returns an empty dict when the manufacturer/category is not configured.
        """
        mfr_key = self._resolve_mfr(manufacturer)
        cat_key = str(category).lower().strip()
        sz_key  = str(size).lower().strip() if size else "default"

        sizes = self._data.get(mfr_key, {}).get(cat_key, {})
        if not sizes:
            return {}

        default  = dict(sizes.get("default", {}))
        specific = sizes.get(sz_key, {}) if sz_key != "default" else {}

        # Merge: size-specific values win, but None values are skipped so they
        # don't mask a valid default.
        merged = dict(default)
        for k, v in specific.items():
            if v is not None:
                merged[k] = v

        return merged
