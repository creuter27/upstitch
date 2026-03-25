"""
Loads products.yaml from Billbee-Artikelmanager and builds token → canonical lookups.

For each field type (manufacturers, categories, variants, sizes, colors):
  - canonical_names(field)  → list of canonical keys
  - resolve(field, token)   → canonical key or None
  - all_tokens(field)       → {token: canonical} dict

Also builds a flat prompt snippet for injection into the Claude system prompt.
"""
import os
from pathlib import Path

import yaml

_REPO = Path(os.environ.get(
    "BILLBEE_ARTIKELMANAGER_PATH",
    Path(__file__).parent.parent / "Billbee-Artikelmanager",
))
_YAML = _REPO / "mappings" / "products.yaml"


def _load() -> dict:
    with open(_YAML, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _build_lookup(data: dict) -> dict[str, dict[str, str]]:
    """
    Returns {field: {token_lower: canonical}} for manufacturers, categories, variants, colors.
    Sizes are kept per-category and NOT flattened (see _size_lookup).
    """
    lookup: dict[str, dict[str, str]] = {}
    for field in ("manufacturers", "categories", "variants", "colors"):
        section = data.get(field, {})
        lookup[field] = {}
        for canonical, entry in section.items():
            lookup[field][canonical.lower()] = canonical
            tokens = entry.get("tokens") or [] if isinstance(entry, dict) else []
            for t in tokens:
                if t:
                    lookup[field][str(t).lower()] = canonical
    return lookup


def _build_size_lookup(data: dict) -> dict[str, dict[str, str]]:
    """Returns {category: {token_lower: canonical_size}}."""
    result: dict[str, dict[str, str]] = {}
    for cat, sizes in (data.get("sizes") or {}).items():
        result[cat] = {}
        for canonical, entry in sizes.items():
            result[cat][str(canonical).lower()] = str(canonical)
            tokens = entry.get("tokens") or [] if isinstance(entry, dict) else []
            for t in tokens:
                if t:
                    result[cat][str(t).lower()] = str(canonical)
    return result


_data = _load()
_lookup = _build_lookup(_data)
_size_lookup = _build_size_lookup(_data)


def resolve(field: str, value: str) -> str | None:
    """
    Resolve a token to its canonical name for the given field.
    field: "manufacturers" | "categories" | "variants" | "sizes" | "colors"
    Returns canonical name, or None if not found.
    """
    if not value:
        return None
    return _lookup.get(field, {}).get(value.lower())


def resolve_filters(filters: dict) -> dict:
    """
    Normalize a filter dict from Claude using the token lookup.
    Keys: category, size, variant, color.
    Size is resolved category-aware; unknown values are kept as-is.
    """
    result = {}
    for key, val in filters.items():
        if not val:
            result[key] = val
            continue
        if key == "size":
            # Resolve size only within the matching category to avoid cross-category confusion
            category = filters.get("category") or ""
            canonical_cat = resolve("categories", category) or category
            size_map = _size_lookup.get(canonical_cat, {})
            result[key] = size_map.get(val.lower(), val)
        elif key == "category":
            result[key] = resolve("categories", val) or val
        elif key == "variant":
            result[key] = resolve("variants", val) or val
        elif key == "color":
            result[key] = resolve("colors", val) or val
        else:
            result[key] = val
    return result


def resolve_manufacturer(value: str) -> str | None:
    return resolve("manufacturers", value)


def prompt_snippet() -> str:
    """Returns a compact token reference to inject into the Claude system prompt."""
    lines = []

    lines.append("Manufacturers (use the code):")
    for code, entry in _data.get("manufacturers", {}).items():
        tokens = entry.get("tokens", []) if isinstance(entry, dict) else []
        lines.append(f"  {code}: {', '.join(tokens)}")

    lines.append("\nCategories (canonical → tokens):")
    for name, entry in _data.get("categories", {}).items():
        tokens = entry.get("tokens", []) if isinstance(entry, dict) else []
        all_t = [name] + [str(t) for t in tokens]
        lines.append(f"  {name}: {', '.join(all_t)}")

    lines.append("\nVariants (canonical → tokens/synonyms):")
    for name, entry in _data.get("variants", {}).items():
        tokens = entry.get("tokens", []) if isinstance(entry, dict) else []
        all_t = [name] + [str(t) for t in tokens]
        lines.append(f"  {name}: {', '.join(all_t)}")

    lines.append("\nSizes (canonical → tokens):")
    for cat, sizes in (_data.get("sizes") or {}).items():
        for name, entry in sizes.items():
            tokens = entry.get("tokens", []) if isinstance(entry, dict) else []
            all_t = [str(name)] + [str(t) for t in tokens]
            lines.append(f"  [{cat}] {name}: {', '.join(all_t)}")

    lines.append("\nColors (canonical → tokens):")
    for name, entry in (_data.get("colors") or {}).items():
        tokens = entry.get("tokens", []) if isinstance(entry, dict) else []
        all_t = [name] + [str(t) for t in tokens]
        lines.append(f"  {name}: {', '.join(all_t)}")

    return "\n".join(lines)
