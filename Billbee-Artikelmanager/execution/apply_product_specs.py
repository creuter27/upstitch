"""
Apply product specifications from product_specs.yaml to the Google Sheet rows.

Logic
-----
Physical rows (IsBom != TRUE):
  Look up spec by (Manufacturer, Produktkategorie, Produktgröße).
  Fill: Weight (g) net, Weight (g) gross, LengthCm, WidthCm, HeightCm,
        CostPrice gross, Price gross.
  Only non-null spec values are written; existing values are not cleared.

  Fallback when custom fields are empty:
    Parse the SKU to derive category and size, then look up the spec.
    If Manufacturer is also missing, derive it from the SKU parser too.
    This covers EAN-based or non-standard SKUs that the pipeline couldn't
    enrich automatically.

Listing rows (IsBom=TRUE):
  Aggregate from the BOM component rows (Subarticle N SKU columns) after
  physical specs have been applied:
    Weight (g) net / gross = sum  of component weights
    LengthCm               = max  of component lengths
    WidthCm                = max  of component widths
    HeightCm               = max  of component heights
  CostPrice / Price are NOT set for sets (components vary; set manually).

Tabs supported
--------------
  upload tab   — uses IsBom column + Subarticle N SKU columns (no Action/BOM_SKUs)
  pipeline tab — uses BOM_SKUs column (legacy; IsBom checked as fallback)

Usage:
  python execution/apply_product_specs.py --sheet-url URL [--dry-run]
"""

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from google_sheets_client import open_sheet, read_tab, write_tab
from execution.mappings_loader import Mappings
from execution.specs_loader import SpecsLoader
from execution.sku_parser import parse_sku

_DEFAULT_TAB = "ProductList"

COL_SKU    = "SKU"
COL_MFR    = "Manufacturer"
COL_CAT    = "Custom Field Produktkategorie"
COL_SIZE   = "Custom Field Produktgröße"
COL_BOM    = "BOM_SKUs"       # pipeline tab format (legacy)
COL_ACTION = "Action"         # pipeline tab only; absent from upload tab → always ignored

# spec YAML key → sheet column name
_SPEC_TO_COL: dict[str, str] = {
    "weight": "Weight (g) net",
    "length": "LengthCm",
    "width":  "WidthCm",
    "height": "HeightCm",
    "cost":   "CostPrice net",
    "price":  "Price gross",
}

COL_WEIGHT_GROSS = "Weight (g) gross"

_SUB_SKU_RE = re.compile(r"Subarticle (\d+) SKU")

def _norm_sku(sku: str) -> str:
    """Collapse multiple consecutive dashes to one (matches sync_pipeline_to_upload behaviour)."""
    return re.sub(r"-{2,}", "-", sku.strip())


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _is_delete(row: dict) -> bool:
    # Action column is absent in the upload tab → always returns False there.
    return str(row.get(COL_ACTION) or "").strip().lower() == "delete"


def _is_listing(row: dict) -> bool:
    # IsBom column (Billbee XLSX / upload tab)
    if str(row.get("IsBom") or "").strip().upper() == "TRUE":
        return True
    # BOM_SKUs column (pipeline/legacy tab format)
    return bool(str(row.get(COL_BOM) or "").strip())


def _bom_skus(row: dict) -> list[str]:
    """
    Return BOM component SKUs for a listing row.

    Checks Subarticle N SKU columns first (upload tab / Billbee XLSX format),
    falls back to the pipe-separated BOM_SKUs cell (pipeline tab format).
    """
    # Upload tab: Subarticle N SKU columns
    pairs: list[tuple[int, str]] = []
    for key, val in row.items():
        m = _SUB_SKU_RE.fullmatch(key)
        if m and str(val).strip():
            pairs.append((int(m.group(1)), str(val).strip()))
    if pairs:
        pairs.sort()
        return [v for _, v in pairs]

    # Pipeline tab fallback: BOM_SKUs cell
    raw = str(row.get(COL_BOM) or "").strip()
    return [s.strip() for s in raw.split("|") if s.strip()] if raw else []


def _to_float(val) -> float | None:
    """Parse a cell value to float; return None for empty / unparseable."""
    if val is None:
        return None
    s = str(val).strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


# ──────────────────────────────────────────────────────────────────────────────
# Core logic
# ──────────────────────────────────────────────────────────────────────────────

def apply_specs(
    rows: list[dict],
    specs: SpecsLoader,
    mappings: Mappings,
    dry_run: bool = False,
    component_rows: list[dict] | None = None,
) -> tuple[list[dict], dict]:
    """
    Apply product specifications to all rows in-place (or dry-run).

    Returns (rows, stats) where stats is a summary dict.
    """
    stats: dict = {
        "physical_updated":    0,
        "physical_no_spec":    0,
        "listing_updated":     0,
        "listing_partial":     0,   # some BOM SKUs not found in sheet
        "listing_no_bom":      0,   # no BOM SKU resolved at all
        # Detail lists for reporting
        "no_spec_rows":        [],  # (sku, mfr, cat, size)
        "partial_rows":        [],  # (sku, [missing_bom_skus])
        "no_data_rows":        [],  # sku
    }

    # ── Build SKU → row dict map for BOM component lookup ────────────────────
    # component_rows (e.g. from 'upload') are added first at lower priority;
    # rows from the main tab override them (fresher / just enriched in phase 1).
    # Both raw SKU and normalised (single-dash) forms are indexed.
    sku_to_row: dict[str, dict] = {}
    for r in (component_rows or []):
        if not _is_delete(r):
            sku = str(r.get(COL_SKU) or "").strip()
            if sku:
                sku_to_row[sku] = r
                sku_to_row[_norm_sku(sku)] = r

    # ── Phase 1: physical rows ────────────────────────────────────────────────
    for i, row in enumerate(rows):
        if _is_delete(row) or _is_listing(row):
            continue

        mfr  = str(row.get(COL_MFR)  or "").strip()
        cat  = str(row.get(COL_CAT)  or "").strip()
        size = str(row.get(COL_SIZE) or "").strip()
        sku  = str(row.get(COL_SKU)  or "").strip()

        # Fallback: parse category / size / manufacturer from SKU when
        # custom fields are empty (handles EAN-based rows and non-enriched rows).
        if not cat:
            parsed = parse_sku(sku, mappings)
            if parsed["sku_format"] != "unknown":
                cat  = str(parsed.get("category") or "").strip()
                size = str(parsed.get("size")     or "").strip() or size
                if not mfr and parsed.get("manufacturer"):
                    mfr = str(parsed["manufacturer"]).strip()

        if not cat:
            stats["physical_no_spec"] += 1
            stats["no_spec_rows"].append((sku, mfr, cat, size))
            continue

        # Physical rows normally have a single category; use the first if
        # somehow comma-separated (shouldn't happen but safe to handle).
        primary_cat = cat.split(",")[0].strip()

        spec = specs.lookup(mfr, primary_cat, size)
        if not spec:
            stats["physical_no_spec"] += 1
            stats["no_spec_rows"].append((sku, mfr, primary_cat, size))
            continue

        updates: dict[str, object] = {}
        for spec_key, col in _SPEC_TO_COL.items():
            val = spec.get(spec_key)
            if val is not None:
                updates[col] = val
                if spec_key == "weight":
                    updates[COL_WEIGHT_GROSS] = val
                elif spec_key == "cost":
                    updates["CostPrice gross"] = ""

        if not updates:
            stats["physical_no_spec"] += 1
            stats["no_spec_rows"].append((sku, mfr, primary_cat, size))
            continue

        rows[i] = {**row, **updates}
        # Keep sku_to_row current so phase 2 sees the updated specs.
        sku_to_row[sku] = rows[i]
        sku_to_row[_norm_sku(sku)] = rows[i]
        stats["physical_updated"] += 1

        if dry_run:
            print(f"  [physical] {sku:45s}  {updates}")

    # ── Phase 2: listing rows (sets) ──────────────────────────────────────────
    for i, row in enumerate(rows):
        if _is_delete(row) or not _is_listing(row):
            continue

        bom_skus = _bom_skus(row)

        # ── Direct spec lookup for listings with no BOM components ──
        # Handles listings where Subarticle columns were never populated.
        # For compound categories/sizes (sets), zip and sum/max per pair.
        if not bom_skus:
            mfr_l      = str(row.get(COL_MFR)  or "").strip()
            cats_str   = str(row.get(COL_CAT)  or "").strip()
            sizes_str  = str(row.get(COL_SIZE) or "").strip()

            if not cats_str:
                parsed_l = parse_sku(row_sku, mappings)
                if parsed_l["sku_format"] != "unknown":
                    cats_str  = str(parsed_l.get("category") or "")
                    sizes_str = str(parsed_l.get("size")     or "") or sizes_str
                    if not mfr_l and parsed_l.get("manufacturer"):
                        mfr_l = str(parsed_l["manufacturer"]).strip()

            if not cats_str:
                stats["listing_no_bom"] += 1
                stats["no_data_rows"].append(row_sku)
                continue

            cats  = [c.strip() for c in cats_str.split(",")  if c.strip()]
            sizes = [s.strip() for s in sizes_str.split(",") if s.strip()]
            pairs = [(cats[j], sizes[j] if j < len(sizes) else "") for j in range(len(cats))]

            d_weights: list[float] = []
            d_lengths: list[float] = []
            d_widths:  list[float] = []
            d_heights: list[float] = []
            d_costs:   list[float] = []
            for c, sz in pairs:
                sp = specs.lookup(mfr_l, c, sz)
                if sp.get("weight") is not None: d_weights.append(float(sp["weight"]))
                if sp.get("length") is not None: d_lengths.append(float(sp["length"]))
                if sp.get("width")  is not None: d_widths.append(float(sp["width"]))
                if sp.get("height") is not None: d_heights.append(float(sp["height"]))
                if sp.get("cost")   is not None: d_costs.append(float(sp["cost"]))

            d_updates: dict = {}
            if d_weights:
                total_w = round(sum(d_weights), 4)
                d_updates["Weight (g) net"] = total_w
                d_updates[COL_WEIGHT_GROSS] = total_w
            if d_lengths: d_updates["LengthCm"] = max(d_lengths)
            if d_widths:  d_updates["WidthCm"]  = max(d_widths)
            if d_heights: d_updates["HeightCm"] = max(d_heights)
            if d_costs:
                d_updates["CostPrice net"] = round(sum(d_costs), 4)
                d_updates["CostPrice gross"] = ""

            if not d_updates:
                stats["listing_no_bom"] += 1
                stats["no_data_rows"].append(row_sku)
                continue

            rows[i] = {**row, **d_updates}
            stats["listing_updated"] += 1
            if dry_run:
                cats_label = ", ".join(f"{c}/{sz}" for c, sz in pairs)
                print(f"  [listing/direct  ] {row_sku:45s}  {cats_label}  {d_updates}")
            continue

        weights, lengths, widths, heights, costs = [], [], [], [], []
        missing_skus: list[str] = []

        for bom_sku in bom_skus:
            comp_row = sku_to_row.get(bom_sku) or sku_to_row.get(_norm_sku(bom_sku))
            if comp_row is None:
                missing_skus.append(bom_sku)
                continue
            w  = _to_float(comp_row.get("Weight (g) net"))
            l  = _to_float(comp_row.get("LengthCm"))
            wd = _to_float(comp_row.get("WidthCm"))
            h  = _to_float(comp_row.get("HeightCm"))
            c  = _to_float(comp_row.get("CostPrice net"))

            if w  is not None: weights.append(w)
            if l  is not None: lengths.append(l)
            if wd is not None: widths.append(wd)
            if h  is not None: heights.append(h)
            if c  is not None: costs.append(c)

        updates = {}
        if weights:
            total_w = round(sum(weights), 4)
            updates["Weight (g) net"] = total_w
            updates[COL_WEIGHT_GROSS] = total_w
        if lengths: updates["LengthCm"] = max(lengths)
        if widths:  updates["WidthCm"]  = max(widths)
        if heights: updates["HeightCm"] = max(heights)
        if costs:
            updates["CostPrice net"] = round(sum(costs), 4)
            updates["CostPrice gross"] = ""

        row_sku = str(row.get(COL_SKU) or "")

        if not updates:
            stats["listing_no_bom"] += 1
            stats["no_data_rows"].append(row_sku)
            continue

        rows[i] = {**row, **updates}

        if missing_skus:
            stats["listing_partial"] += 1
            stats["partial_rows"].append((row_sku, missing_skus))
        else:
            stats["listing_updated"] += 1

        if dry_run:
            label = "[listing(set)]" if len(bom_skus) > 1 else "[listing]    "
            flag  = f" [missing: {', '.join(missing_skus)}]" if missing_skus else ""
            print(f"  {label} {row_sku:45s}  {updates}{flag}")

    return rows, stats


# ──────────────────────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Apply product specs (weight, dimensions, price) to the Google Sheet."
    )
    parser.add_argument("--sheet-url", required=True, help="URL of the Google Sheet.")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print planned changes without writing to the sheet.",
    )
    parser.add_argument("--tab", default=_DEFAULT_TAB,
                        help=f"Tab to read and write (default: '{_DEFAULT_TAB}').")
    parser.add_argument("--lookup-tab", default=None,
                        help="Tab to read BOM component rows from for weight/dimension "
                             "aggregation (default: same as --tab).")
    args = parser.parse_args()

    mappings = Mappings()
    specs    = SpecsLoader(mappings=mappings)

    print("Opening sheet …")
    ss   = open_sheet(args.sheet_url)
    rows = read_tab(ss, args.tab)
    print(f"  {len(rows)} rows loaded from '{args.tab}'.")

    component_rows: list[dict] | None = None
    lookup_tab = args.lookup_tab or args.tab
    if lookup_tab != args.tab:
        component_rows = read_tab(ss, lookup_tab)
        print(f"  {len(component_rows)} component rows loaded from '{lookup_tab}'.")
    print()

    rows, stats = apply_specs(rows, specs, mappings,
                              dry_run=args.dry_run, component_rows=component_rows)

    print(f"\nSummary")
    print(f"  Physical rows updated : {stats['physical_updated']}")
    print(f"  Physical rows no spec : {stats['physical_no_spec']}")
    print(f"  Listing rows updated  : {stats['listing_updated']}")
    print(f"  Listing rows partial  : {stats['listing_partial']}  (some BOM SKUs not in sheet)")
    print(f"  Listing rows no data  : {stats['listing_no_bom']}   (no BOM component weights found)")

    if stats["no_spec_rows"]:
        print(f"\nPhysical rows with no spec entry (manufacturer/category not in product_specs.yaml):")
        for sku, mfr, cat, size in stats["no_spec_rows"]:
            size_str = f"  size={size!r}" if size else ""
            print(f"    {sku:45s}  mfr={mfr!r}  cat={cat!r}{size_str}")

    if stats["partial_rows"]:
        print(f"\nListing rows with unresolved BOM SKUs:")
        for sku, missing in stats["partial_rows"]:
            print(f"    {sku:45s}  missing: {', '.join(missing)}")

    if stats["no_data_rows"]:
        print(f"\nListing rows where no BOM component had weight/dimension data:")
        for sku in stats["no_data_rows"]:
            print(f"    {sku}")

    if args.dry_run:
        print("\n[dry-run] No changes written. Re-run without --dry-run to apply.")
    else:
        total = stats["physical_updated"] + stats["listing_updated"] + stats["listing_partial"]
        print(f"\nWriting {total} updated rows …")
        write_tab(ss, args.tab, rows)
        print("[done]")


if __name__ == "__main__":
    main()
