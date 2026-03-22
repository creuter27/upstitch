"""
Validate that BOM (physical) SKUs of listing products are consistent with their
listing SKU.  For each listing-format row that has BOM_SKUs, each BOM entry is
compared against the listing's expected attributes (manufacturer, category,
variant, size, color).  Mismatches are reported interactively; the user may
approve or skip each proposed fix.  Approved fixes are written back to the
BOM_SKUs column in the sheet (as a staging record — Billbee is not updated).

Usage:
  python execution/validate_bom_skus.py --sheet-url URL
  python execution/validate_bom_skus.py --sheet-url URL --listing-id 1215591577
  python execution/validate_bom_skus.py --sheet-url URL --dry-run

  --listing-id   Only validate rows whose listing ID starts with this prefix.
  --dry-run      Show mismatches but do not prompt for fixes.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from google_sheets_client import open_sheet, read_tab, write_tab
from execution.mappings_loader import Mappings
from execution.sku_parser import parse_sku

TAB_NAME    = "ProductList"
COL_BOM     = "BOM_SKUs"


# ──────────────────────────────────────────────────────────────────────────────
# Low-level helpers
# ──────────────────────────────────────────────────────────────────────────────

def _listing_raw_slots(sku: str) -> dict:
    """Return the raw dash-separated segments of a listing SKU."""
    parts = sku.split("-")
    # listing: listingID - mfr - cat - variant - size - color
    return {
        "listing_id":   parts[0] if len(parts) > 0 else "",
        "variant_raw":  parts[3] if len(parts) > 3 else "",
        "size_raw":     parts[4] if len(parts) > 4 else "",
        "color_raw":    parts[5] if len(parts) > 5 else "",
    }


def _trace_physical_field_indices(bom_sku: str, primary_cat: str, mappings: Mappings) -> dict[str, int | None]:
    """
    Run the same type-based assignment used by parse_sku for physical SKUs and
    record which part index was assigned to each field.
    Returns {'size': idx|None, 'variant': idx|None, 'color': idx|None}.
    """
    parts = bom_sku.split("-")
    indices = {"size": None, "variant": None, "color": None}
    size_done = variant_done = color_done = False

    for i in range(2, len(parts)):
        token = parts[i]
        if not token:
            continue
        res_size    = mappings.canonical_size(token, primary_cat) if primary_cat else None
        res_variant = mappings.canonical_variant(token)
        res_color   = mappings.canonical_color(token)

        if res_size and not size_done:
            indices["size"] = i;    size_done = True
        elif res_variant and not variant_done:
            indices["variant"] = i; variant_done = True
        elif res_color and not color_done:
            indices["color"] = i;   color_done = True
        else:
            if not size_done:    indices["size"] = i;    size_done = True
            elif not variant_done: indices["variant"] = i; variant_done = True
            elif not color_done: indices["color"] = i;   color_done = True

    return indices


def _build_corrected_bom_sku(
    bom_sku: str,
    corrections: dict[str, str],
    primary_cat: str,
    mappings: Mappings,
) -> str:
    """Replace specific field tokens in a BOM SKU string and return the result."""
    parts     = bom_sku.split("-")
    new_parts = list(parts)
    indices   = _trace_physical_field_indices(bom_sku, primary_cat, mappings)

    for field, token in corrections.items():
        idx = indices.get(field)
        if idx is not None and idx < len(new_parts):
            new_parts[idx] = token
        # If the field slot doesn't exist yet, append it
        # (only sensible for simple cases; skip for now)

    return "-".join(new_parts)


def _per_bom_size_token(
    listing_size_raw: str,
    bom_index: int,
    listing_cats: list[str],
    bom_primary_cat: str,
    mappings: Mappings,
) -> str:
    """
    Given the listing's raw size slot token, return the expected raw size token
    for the BOM item at bom_index.

    Simple (single-char or full-word token that resolves directly):
        "s"    → "s"   (same for all BOM items)
        "big"  → "big"

    Compound (multiple single-char tokens concatenated, one per BOM item):
        "ss" → bom[0]="s", bom[1]="s"
        "bs" → bom[0]="b", bom[1]="s"

    Returns the raw token to expect in the BOM SKU, or "" if undetermined.
    """
    if not listing_size_raw:
        return ""

    # Try resolving the whole token against each relevant category
    for cat in [bom_primary_cat] + listing_cats:
        if cat and mappings.canonical_size(listing_size_raw, cat):
            return listing_size_raw   # simple single token

    # Try compound: each character is a size token for one BOM item
    chars = list(listing_size_raw)
    all_valid = all(
        any(mappings.canonical_size(ch, cat) for cat in listing_cats + [bom_primary_cat] if cat)
        for ch in chars
    )
    if all_valid and bom_index < len(chars):
        return chars[bom_index]

    return listing_size_raw   # unknown compound — return whole token as fallback


# ──────────────────────────────────────────────────────────────────────────────
# Core validation
# ──────────────────────────────────────────────────────────────────────────────

def validate_bom_consistency(
    rows: list[dict],
    mappings: Mappings,
    listing_id_prefix: str | None,
) -> list[dict]:
    """
    Scan all listing rows and return a list of mismatch records.

    Each record:
        row_idx         int   — index into rows
        listing_sku     str
        bom_idx         int   — index of the BOM SKU in the pipe-list
        bom_sku         str   — original BOM SKU
        bom_skus_all    list  — all BOM SKUs for this row (for reconstruction)
        issues          list[str]
        corrected_bom_sku str — proposed corrected BOM SKU
    """
    mismatches: list[dict] = []

    for row_idx, row in enumerate(rows):
        sku       = str(row.get("SKU") or "").strip()
        bom_cell  = str(row.get(COL_BOM) or "").strip()

        if not bom_cell:
            continue

        p_listing = parse_sku(sku, mappings)
        if p_listing["sku_format"] != "listing":
            continue

        listing_id = p_listing["listing_id"] or ""
        if listing_id_prefix and not listing_id.startswith(listing_id_prefix):
            continue

        raw_slots    = _listing_raw_slots(sku)
        listing_cats = [c.strip() for c in (p_listing["category"] or "").split(",") if c.strip()]
        bom_skus     = [s.strip() for s in bom_cell.split("|") if s.strip()]

        for bom_idx, bom_sku in enumerate(bom_skus):
            p_bom = parse_sku(bom_sku, mappings)
            if p_bom["sku_format"] == "unknown":
                continue

            bom_primary_cat = (p_bom["category"] or "").split(",")[0].strip()

            issues:      list[str] = []
            corrections: dict[str, str] = {}

            # ── Manufacturer ──────────────────────────────────────────────
            if p_listing["manufacturer"] and p_bom["manufacturer"]:
                if p_listing["manufacturer"] != p_bom["manufacturer"]:
                    issues.append(
                        f"manufacturer: listing='{p_listing['manufacturer']}'"
                        f" bom='{p_bom['manufacturer']}'"
                    )
                    # raw token for manufacturer is parts[1] of bom_sku
                    bom_parts = bom_sku.split("-")
                    corrections["manufacturer_part1"] = p_listing["manufacturer"]

            # ── Category ─────────────────────────────────────────────────
            if listing_cats and p_bom["category"]:
                bom_cat = p_bom["category"]
                if bom_cat not in listing_cats:
                    issues.append(
                        f"category: listing='{p_listing['category']}'"
                        f" bom='{bom_cat}'"
                    )
                    # No automatic fix for category (would need a SKU rebuild)

            # ── Variant ───────────────────────────────────────────────────
            if p_listing["variant"] and p_bom["variant"]:
                if p_listing["variant"] != p_bom["variant"]:
                    issues.append(
                        f"variant: listing='{p_listing['variant']}'"
                        f" bom='{p_bom['variant']}'"
                    )
                    corrections["variant"] = raw_slots["variant_raw"]

            # ── Size ──────────────────────────────────────────────────────
            expected_size_token = _per_bom_size_token(
                raw_slots["size_raw"], bom_idx, listing_cats, bom_primary_cat, mappings
            )
            if expected_size_token:
                expected_size_can = (
                    mappings.canonical_size(expected_size_token, bom_primary_cat)
                    or expected_size_token
                )
                actual_size_can = p_bom["size"]
                if actual_size_can and expected_size_can != actual_size_can:
                    issues.append(
                        f"size: listing expects '{expected_size_can}'"
                        f" (raw token '{expected_size_token}')"
                        f", bom has '{actual_size_can}'"
                    )
                    corrections["size"] = expected_size_token

            # ── Color ─────────────────────────────────────────────────────
            if p_listing["color"] and p_bom["color"]:
                if p_listing["color"] != p_bom["color"]:
                    issues.append(
                        f"color: listing='{p_listing['color']}'"
                        f" bom='{p_bom['color']}'"
                    )
                    corrections["color"] = raw_slots["color_raw"]

            if issues:
                if corrections:
                    corrected = _build_corrected_bom_sku(
                        bom_sku, corrections, bom_primary_cat, mappings
                    )
                else:
                    corrected = bom_sku  # no auto-fix available

                mismatches.append({
                    "row_idx":          row_idx,
                    "listing_sku":      sku,
                    "bom_idx":          bom_idx,
                    "bom_sku":          bom_sku,
                    "bom_skus_all":     bom_skus,
                    "issues":           issues,
                    "corrected_bom_sku": corrected,
                    "has_fix":          corrected != bom_sku,
                })

    return mismatches


# ──────────────────────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Validate BOM SKU consistency against listing SKUs."
    )
    parser.add_argument("--sheet-url",   required=True)
    parser.add_argument("--listing-id",  default=None,
                        help="Filter to listing IDs starting with this prefix.")
    parser.add_argument("--dry-run",     action="store_true",
                        help="Report only — do not prompt for fixes.")
    parser.add_argument("--yes-all",     action="store_true",
                        help="Automatically approve all fixable mismatches without prompting.")
    args = parser.parse_args()

    mappings = Mappings()

    print("Opening sheet ...")
    spreadsheet = open_sheet(args.sheet_url)
    rows = read_tab(spreadsheet, TAB_NAME)
    print(f"      {len(rows)} rows loaded.\n")

    filter_label = f" (listing-id prefix '{args.listing_id}')" if args.listing_id else ""
    print(f"Validating BOM SKU consistency{filter_label} ...")
    mismatches = validate_bom_consistency(rows, mappings, args.listing_id)

    if not mismatches:
        print("No inconsistencies found.")
        return

    print(f"{len(mismatches)} mismatch(es) found.\n")

    if args.yes_all:
        approved: dict[int, dict[int, str]] = {}
        for m in mismatches:
            if m["has_fix"]:
                approved.setdefault(m["row_idx"], {})[m["bom_idx"]] = m["corrected_bom_sku"]
                print(f"  Auto-approve: {m['bom_sku']}  →  {m['corrected_bom_sku']}")
        n_fixes = sum(len(v) for v in approved.values())
        print(f"\nApplying {n_fixes} fix(es) ...")
        rows_copy = [dict(r) for r in rows]
        for row_idx, bom_fixes in approved.items():
            row = rows_copy[row_idx]
            bom_skus = [s.strip() for s in str(row.get(COL_BOM) or "").split("|") if s.strip()]
            for bom_idx, corrected in bom_fixes.items():
                if bom_idx < len(bom_skus):
                    bom_skus[bom_idx] = corrected
            row[COL_BOM] = " | ".join(bom_skus)
        write_tab(spreadsheet, TAB_NAME, rows_copy)
        print(f"[done] Sheet URL: {spreadsheet.url}")
        return

    if args.dry_run:
        for m in mismatches:
            bom_count = len(m["bom_skus_all"])
            print(f"Listing: {m['listing_sku']}")
            print(f"  BOM [{m['bom_idx']+1}/{bom_count}]: {m['bom_sku']}")
            for issue in m["issues"]:
                print(f"    ✗ {issue}")
            if m["has_fix"]:
                print(f"  → Proposed: {m['corrected_bom_sku']}")
            print()
        return

    # ── Interactive mode ──────────────────────────────────────────────────────
    # {row_idx: {bom_idx: corrected_sku}}
    approved: dict[int, dict[int, str]] = {}

    for i, m in enumerate(mismatches, 1):
        bom_count = len(m["bom_skus_all"])
        print(f"[{i}/{len(mismatches)}] Listing: {m['listing_sku']}")
        print(f"         BOM [{m['bom_idx']+1}/{bom_count}]: {m['bom_sku']}")
        for issue in m["issues"]:
            print(f"         Issue: {issue}")
        if m["has_fix"]:
            print(f"         Fix:   {m['bom_sku']}  →  {m['corrected_bom_sku']}")
        else:
            print("         (no automatic fix available — manual correction needed)")
        print()

        if not m["has_fix"]:
            input("         [press Enter to continue] ")
            print()
            continue

        while True:
            raw = input("         Apply fix? [y]es / [n]o / [q]uit: ").strip().lower()
            if raw in ("y", "yes", "n", "no", "q", "quit", ""):
                break

        print()

        if raw in ("q", "quit"):
            print("Aborted.")
            break
        if raw in ("y", "yes"):
            approved.setdefault(m["row_idx"], {})[m["bom_idx"]] = m["corrected_bom_sku"]

    if not approved:
        print("No fixes to apply.")
        return

    n_fixes = sum(len(v) for v in approved.values())
    print(f"Applying {n_fixes} fix(es) ...")

    rows_copy = [dict(r) for r in rows]
    for row_idx, bom_fixes in approved.items():
        row = rows_copy[row_idx]
        bom_skus = [s.strip() for s in str(row.get(COL_BOM) or "").split("|") if s.strip()]
        for bom_idx, corrected in bom_fixes.items():
            if bom_idx < len(bom_skus):
                bom_skus[bom_idx] = corrected
        row[COL_BOM] = " | ".join(bom_skus)

    write_tab(spreadsheet, TAB_NAME, rows_copy)
    print(f"[done] Sheet URL: {spreadsheet.url}")


if __name__ == "__main__":
    main()
