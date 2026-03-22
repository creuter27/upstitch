"""
Loads mappings/products.yaml and provides canonical-lookup helpers.

Usage:
    from execution.mappings_loader import Mappings
    m = Mappings()
    m.canonical_manufacturer("trixie baby")       # → "TRX"
    m.canonical_category("tow")                   # → "handtuch"
    m.canonical_variant("papagei")                # → "parrot"
    m.canonical_size("s", category="rucksack")    # → "small"
    m.canonical_color("rot")                      # → "red"

All lookups are case-insensitive. Returns None when no match is found.
"""

from pathlib import Path
from typing import Optional

import yaml

MAPPINGS_FILE = Path(__file__).parent.parent / "mappings" / "products.yaml"


class Mappings:
    def __init__(self, path: Path = MAPPINGS_FILE):
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)

        self._manufacturers: dict = data.get("manufacturers", {})
        self._categories: dict = data.get("categories", {})
        self._variants: dict = data.get("variants", {})
        self._sizes: dict = data.get("sizes", {})
        self._colors: dict = data.get("colors", {})

        # Pre-build reverse lookup tables: lowercase token → canonical key
        self._mfr_by_token = self._build_token_map(self._manufacturers)
        self._cat_by_token = self._build_token_map(self._categories)
        self._var_by_token = self._build_token_map(self._variants)
        self._col_by_token = self._build_token_map(self._colors)

        # Sizes are per-category; build one map per category
        self._size_by_cat_token: dict[str, dict[str, str]] = {}
        for cat, sizes in self._sizes.items():
            self._size_by_cat_token[cat.lower()] = self._build_token_map(sizes)

    @staticmethod
    def _build_token_map(mapping: dict) -> dict[str, str]:
        """Return {lowercase_token: canonical_key} from a mappings dict.

        The canonical key itself is always included as a self-referencing token
        (case-insensitive), so lookup works even when a SKU segment already
        contains the canonical name (e.g. 'dino' resolves to 'dino').
        """
        result = {}
        for canonical, attrs in mapping.items():
            # Canonical key maps to itself
            result[str(canonical).lower()] = str(canonical)
            if isinstance(attrs, dict):
                for token in attrs.get("tokens", []):
                    result[str(token).lower()] = str(canonical)
        return result

    # ------------------------------------------------------------------
    # Lookup helpers
    # ------------------------------------------------------------------

    def canonical_manufacturer(self, token: str) -> Optional[str]:
        return self._mfr_by_token.get(token.lower())

    def canonical_category(self, token: str) -> Optional[str]:
        return self._cat_by_token.get(token.lower())

    def canonical_variant(self, token: str) -> Optional[str]:
        return self._var_by_token.get(token.lower())

    def canonical_size(self, token: str, category: str) -> Optional[str]:
        cat_map = self._size_by_cat_token.get(category.lower(), {})
        return cat_map.get(token.lower())

    def canonical_color(self, token: str) -> Optional[str]:
        return self._col_by_token.get(token.lower())

    # ------------------------------------------------------------------
    # Raw access (for iteration, reporting, etc.)
    # ------------------------------------------------------------------

    @property
    def manufacturers(self) -> dict:
        return self._manufacturers

    @property
    def categories(self) -> dict:
        return self._categories

    @property
    def variants(self) -> dict:
        return self._variants

    @property
    def sizes(self) -> dict:
        return self._sizes

    @property
    def colors(self) -> dict:
        return self._colors
