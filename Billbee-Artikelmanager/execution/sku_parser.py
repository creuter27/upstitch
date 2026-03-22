"""
SKU parsing: detect format and extract canonical attribute values.

Physical SKU format:  manufacturer - category - size - variant - color
Listing SKU format:   listingID - manufacturer - category - variant - size - color

Detection:
  - physical: parts[0] resolves to a known manufacturer
  - listing:  parts[0] is numeric OR parts[0] is not a manufacturer but parts[1] is
  - unknown:  everything else

Compound categories (listing only) are decomposed by greedy longest-token-first
prefix scan, e.g. "BPBTN" → ["rucksack", "flasche"].
"""

from typing import Optional
from execution.mappings_loader import Mappings


def _is_manufacturer(token: str, mappings: Mappings) -> bool:
    t = token.upper()
    return t in mappings.manufacturers or mappings.canonical_manufacturer(token) is not None


def _resolve_manufacturer(token: str, mappings: Mappings) -> Optional[str]:
    upper = token.upper()
    if upper in mappings.manufacturers:
        return upper
    return mappings.canonical_manufacturer(token)


def _build_category_prefix_table(mappings: Mappings) -> list[tuple[str, str]]:
    """
    Return a list of (token_lowercase, canonical) sorted by token length descending.
    Used for greedy longest-match prefix scanning of compound category strings.

    Canonical keys are included as self-referencing tokens so that full names
    (e.g. 'rucksack') are matched directly alongside abbreviations ('bp').
    """
    seen: set[str] = set()
    entries = []
    for canonical, attrs in mappings.categories.items():
        # Include canonical key itself
        key = canonical.lower()
        if key not in seen:
            entries.append((key, canonical))
            seen.add(key)
        for token in attrs.get("tokens", []):
            t = token.lower()
            if t not in seen:
                entries.append((t, canonical))
                seen.add(t)
    # Longest tokens first so greedy scan picks the most specific match
    entries.sort(key=lambda x: -len(x[0]))
    return entries


def parse_compound_category(token: str, mappings: Mappings) -> list[str]:
    """
    Decompose a (possibly compound) category token into a list of canonical names.

    Examples:
      "BPBTN" → ["rucksack", "flasche"]
      "BTN"   → ["flasche"]
      "BP"    → ["rucksack"]
      "Backp" → ["rucksack"]
      "xyz"   → []

    Strategy: greedy left-to-right scan, try longest matching token at each position,
    skip unrecognised suffix characters (e.g. "n" in "BTN").
    """
    if not token:
        return []

    prefix_table = _build_category_prefix_table(mappings)
    s = token.lower()
    result = []
    pos = 0

    while pos < len(s):
        matched = False
        for tok, canonical in prefix_table:
            if s.startswith(tok, pos):
                if not result or result[-1] != canonical:   # avoid duplicates
                    result.append(canonical)
                pos += len(tok)
                matched = True
                break
        if not matched:
            pos += 1   # skip unrecognised character (e.g. "n" suffix)

    return result


def _or_none(value: str) -> Optional[str]:
    """Return value if non-empty, else None."""
    return value if value else None


def parse_sku(sku: str, mappings: Mappings) -> dict:
    """
    Parse a Billbee SKU and return a dict of canonical attribute values.

    Return keys:
      sku_format   'physical' | 'listing' | 'unknown'
      listing_id   str | None
      manufacturer canonical code string (e.g. 'TRX') | None
      category     comma-joined canonical names (e.g. 'rucksack, flasche') | None
      size         canonical size string (e.g. 'big', '350') | None
      variant      canonical variant name (e.g. 'bear') | None
      color        canonical color name (e.g. 'red') | None

    Unresolvable tokens are kept as their raw (stripped) values.
    Empty segments from double-dashes are stored as None.
    """
    result = {
        "sku_format": "unknown",
        "listing_id": None,
        "manufacturer": None,
        "category": None,
        "size": None,
        "variant": None,
        "color": None,
    }

    if not sku or not sku.strip():
        return result

    # Split preserving empty strings (double-dash → empty segment)
    parts = sku.split("-")

    if len(parts) < 2:
        # Pure numeric or single-segment → unrecognised
        return result

    # ------------------------------------------------------------------
    # Detect format
    # ------------------------------------------------------------------
    p0_is_mfr = _is_manufacturer(parts[0], mappings)
    p1_is_mfr = len(parts) > 1 and _is_manufacturer(parts[1], mappings)

    if p0_is_mfr:
        result["sku_format"] = "physical"
        mfr_idx = 0
    elif p1_is_mfr:
        result["sku_format"] = "listing"
        result["listing_id"] = parts[0]
        mfr_idx = 1
    else:
        return result  # unknown — leave as-is

    # ------------------------------------------------------------------
    # Extract raw segments by position
    # ------------------------------------------------------------------
    mfr_token   = parts[mfr_idx]                                    # always present
    cat_token   = parts[mfr_idx + 1] if len(parts) > mfr_idx + 1 else ""
    slot2_token = parts[mfr_idx + 2] if len(parts) > mfr_idx + 2 else ""
    slot3_token = parts[mfr_idx + 3] if len(parts) > mfr_idx + 3 else ""
    slot4_token = parts[mfr_idx + 4] if len(parts) > mfr_idx + 4 else ""

    # ------------------------------------------------------------------
    # Manufacturer
    # ------------------------------------------------------------------
    result["manufacturer"] = _resolve_manufacturer(mfr_token, mappings) or mfr_token

    # ------------------------------------------------------------------
    # Category (handles single and compound tokens)
    # ------------------------------------------------------------------
    if cat_token:
        cats = parse_compound_category(cat_token, mappings)
        if cats:
            result["category"] = ", ".join(cats)
        else:
            result["category"] = cat_token  # keep raw if unmapped

    # ------------------------------------------------------------------
    # Physical: manufacturer - category - [SIZE] - [VARIANT] - [COLOR]
    # Listing:  listingID - manufacturer - category - VARIANT - size - color
    #
    # Physical slot assignment uses type-based resolution rather than strict
    # positional mapping.  Each non-empty token in slots 2-4 is tested in order:
    #   1. Does it resolve as a size (category-aware)?  → size field
    #   2. Does it resolve as a variant?                → variant field
    #   3. Does it resolve as a color?                  → color field
    #   4. Raw / unresolvable: fill next empty field in the order size→variant→color.
    # This correctly handles SKUs where the variant token appears before the size
    # token (e.g. TRX-BPN-HUMM-s: HUMM=hummel variant, s=small size).
    # ------------------------------------------------------------------
    if result["sku_format"] == "physical":
        primary_cat = cats[0] if "cats" in dir() and cats else None

        for token in [slot2_token, slot3_token, slot4_token]:
            if not token:
                continue
            resolved_size    = mappings.canonical_size(token, primary_cat or "") if primary_cat else None
            resolved_variant = mappings.canonical_variant(token)
            resolved_color   = mappings.canonical_color(token)

            if resolved_size and result["size"] is None:
                result["size"] = resolved_size
            elif resolved_variant and result["variant"] is None:
                result["variant"] = resolved_variant
            elif resolved_color and result["color"] is None:
                result["color"] = resolved_color
            else:
                # Raw token: fill next empty field in positional order
                if result["size"] is None:
                    result["size"] = token
                elif result["variant"] is None:
                    result["variant"] = token
                elif result["color"] is None:
                    result["color"] = token

    else:  # listing
        variant_token = slot2_token
        size_token    = slot3_token
        color_token   = slot4_token

        if variant_token:
            result["variant"] = mappings.canonical_variant(variant_token) or _or_none(variant_token)

        if size_token:
            primary_cat = None
            if result["category"]:
                # Use first canonical category for size resolution
                first_cat = result["category"].split(",")[0].strip()
                primary_cat = first_cat if first_cat in mappings.sizes else None
            resolved_size = mappings.canonical_size(size_token, primary_cat or "") if primary_cat else None
            result["size"] = resolved_size or _or_none(size_token)

        if color_token:
            result["color"] = mappings.canonical_color(color_token) or _or_none(color_token)

    return result


def parse_sku_from_bom(bom_skus_cell: str, mappings: Mappings) -> dict:
    """
    Derive attribute values from the BOM_SKUs cell of a listing product.

    bom_skus_cell: pipe-separated raw BOM SKU string (e.g. "TRX-BP-big-baer | TRX-BP-small-baer").

    Strategy:
    - Parse each BOM physical SKU individually.
    - manufacturer, category, variant, color must agree across all BOM items
      (use the first successfully resolved value).
    - size: collect all distinct resolved sizes; if exactly one → use it;
      if multiple (set product) → join with ", " to signal a set
      (e.g. "big, small" for a big+small bundle).

    Returns the same dict structure as parse_sku (sku_format is always 'bom_derived').
    Returns all-None result if no BOM SKUs can be parsed.
    """
    result = {
        "sku_format": "bom_derived",
        "listing_id": None,
        "manufacturer": None,
        "category": None,
        "size": None,
        "variant": None,
        "color": None,
    }

    if not bom_skus_cell or not bom_skus_cell.strip():
        return result

    raw_skus = [s.strip() for s in bom_skus_cell.split("|") if s.strip()]
    if not raw_skus:
        return result

    sizes:      list[str] = []
    categories: list[str] = []

    for sku in raw_skus:
        parsed = parse_sku(sku, mappings)
        if parsed["sku_format"] == "unknown":
            continue

        # Category: collect all unique values (compound listings have multiple)
        if parsed["category"] is not None and parsed["category"] not in categories:
            categories.append(parsed["category"])

        # Manufacturer / variant / color: first resolved value wins
        for field in ("manufacturer", "variant", "color"):
            if result[field] is None and parsed[field] is not None:
                result[field] = parsed[field]

        # Collect all sizes for later deduplication
        if parsed["size"] is not None and parsed["size"] not in sizes:
            sizes.append(parsed["size"])

    if categories:
        result["category"] = ", ".join(categories)
    if sizes:
        result["size"] = ", ".join(sizes)

    return result


def _expand_compound_size_chars(size_raw: str, categories: list[str], mappings: Mappings) -> list[str]:
    """
    Expand a raw listing size token into a list of per-item single tokens.

    Simple (whole token resolves as a size for any relevant category):
        "b"   → ["b"]
        "big" → ["big"]
        "350" → ["350"]

    Compound (each character is a valid size token):
        "bs" → ["b", "s"]
        "ss" → ["s", "s"]

    Returns [size_raw] if the token cannot be decomposed character-by-character.
    Returns [] if size_raw is empty.
    """
    if not size_raw:
        return []

    # Try the whole token first
    for cat in categories:
        if mappings.canonical_size(size_raw, cat):
            return [size_raw]

    # Try character-by-character
    chars = list(size_raw)
    all_valid = all(
        any(mappings.canonical_size(ch, cat) for cat in categories)
        for ch in chars
    )
    if all_valid:
        return chars

    return [size_raw]  # undecomposable — treat as single token


def derive_listing_bom_items(listing_sku: str, mappings: Mappings) -> list[dict]:
    """
    From a listing-format SKU, derive the expected physical BOM items.

    Returns [] for non-listing SKUs or if no categories can be resolved.

    Each item in the returned list is a dict:
        manufacturer  str | None   canonical manufacturer code
        category      str | None   canonical single category for this BOM item
        size          str | None   canonical size (category-aware)
        variant       str | None   canonical variant
        color         str | None   canonical color

    For compound listings the logic is:
      - categories = [cat1, cat2, ...]  (from compound category token)
      - size_chars = expand(raw_size_token) → one token per item
      - n_items = max(len(categories), len(size_chars))
      - item i: category = categories[i % n_cats], size = size_chars[i % n_sizes]

    Examples:
      BPBTN + ss  → rucksack/small + flasche/350
      BPBT  + bs  → rucksack/big  + flasche/350
      BP    + bs  → rucksack/big  + rucksack/small
      BP    + b   → rucksack/big
    """
    parsed = parse_sku(listing_sku, mappings)
    if parsed["sku_format"] != "listing":
        return []

    # Raw size token is at position 4 in the listing SKU parts
    # (listingID[0] - mfr[1] - cat[2] - variant[3] - size[4] - color[5])
    parts = listing_sku.split("-")
    size_raw = parts[4] if len(parts) > 4 else ""

    categories = [c.strip() for c in (parsed["category"] or "").split(",") if c.strip()]
    if not categories:
        return []

    size_chars = _expand_compound_size_chars(size_raw, categories, mappings)

    n_cats  = len(categories)
    n_sizes = len(size_chars)
    n_items = max(n_cats, n_sizes) if (n_cats and n_sizes) else max(n_cats, 1)

    items = []
    for i in range(n_items):
        cat       = categories[i % n_cats]
        size_char = size_chars[i % n_sizes] if size_chars else ""
        size_can  = (mappings.canonical_size(size_char, cat) or size_char) if size_char else None

        items.append({
            "manufacturer": parsed["manufacturer"],
            "category":     cat,
            "size":         size_can,
            "variant":      parsed["variant"],
            "color":        parsed["color"],
        })

    return items
