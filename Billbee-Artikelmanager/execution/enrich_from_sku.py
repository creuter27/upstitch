"""
Enrich the 'upload' tab of an existing Google Sheet by parsing each product's
SKU and filling the attribute columns with canonical values.

Columns filled (only when the parsed value is not None):
  Manufacturer, Produktkategorie, Produktgröße, Produktvariante, Produktfarbe

Usage:
  python execution/enrich_from_sku.py --sheet-url URL [--manufacturer MFR]

  --sheet-url     URL of the Google Sheet (required)
  --manufacturer  Override: set Manufacturer to this value for ALL rows regardless
                  of what the SKU parser finds. Useful when the whole sheet is
                  pre-filtered for one manufacturer and many SKUs are non-standard.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from google_sheets_client import open_sheet, read_tab, write_tab
from execution.mappings_loader import Mappings
from execution.sku_parser import parse_sku, parse_sku_from_bom, derive_listing_bom_items

# Column names as they appear in the sheet (match Billbee XLSX column names)
COL_MANUFACTURER  = "Manufacturer"


def _mfr_display_name(query: str | None, mappings: Mappings) -> str | None:
    """
    Return the first token for a manufacturer code, preserving the case from
    the mapping file (e.g. "TRX" → "Trixie Baby").

    Lookup order:
      1. Direct canonical key match (e.g. "TRX")
      2. Token-based reverse lookup (e.g. "trixie" → "TRX")
      3. Fallback: return query unchanged
    """
    if not query:
        return query
    entry = mappings.manufacturers.get(query)
    if entry is None:
        canonical = mappings.canonical_manufacturer(query)
        entry = mappings.manufacturers.get(canonical) if canonical else None
    if entry and isinstance(entry, dict):
        tokens = entry.get("tokens", [])
        if tokens:
            return tokens[0]
    return query
COL_KATEGORIE     = "Custom Field Produktkategorie"
COL_GROESSE       = "Custom Field Produktgröße"
COL_VARIANTE      = "Custom Field Produktvariante"
COL_FARBE         = "Custom Field Produktfarbe"
_DEFAULT_TAB = "ProductList"


def _apply_parsed(row: dict, parsed: dict, mfr_override: str | None, mappings: Mappings) -> bool:
    """
    Write parsed attribute values into the row dict.

    All five attribute columns are always reset so that stale values from a
    previous run don't survive when a column no longer has a parsed value.
    Returns True if at least one column received a meaningful value.
    """
    any_filled = False

    if mfr_override:
        row[COL_MANUFACTURER] = _mfr_display_name(mfr_override, mappings)
        any_filled = True
    elif parsed["manufacturer"]:
        row[COL_MANUFACTURER] = _mfr_display_name(parsed["manufacturer"], mappings)
        any_filled = True

    # Always write all derived columns (empty string when not parsed)
    row[COL_KATEGORIE] = parsed["category"] or ""
    row[COL_GROESSE]   = parsed["size"]     or ""
    row[COL_VARIANTE]  = parsed["variant"]  or ""
    row[COL_FARBE]     = parsed["color"]    or ""

    if any(parsed[f] is not None for f in ("category", "size", "variant", "color")):
        any_filled = True

    return any_filled


def enrich_rows(
    rows: list[dict],
    mappings: Mappings,
    mfr_override: str | None,
) -> tuple[list[dict], int, int, int]:
    """
    Parse each row's SKU and fill attribute columns.

    When the primary SKU has an unknown format, fall back to parsing the
    BOM_SKUs cell (pipe-separated physical SKUs of the linked BOM items).

    Returns (enriched_rows, n_enriched, n_bom_fallback, n_skipped).
    """
    enriched = 0
    bom_fallback = 0
    skipped = 0
    result = []

    for row in rows:
        row = dict(row)  # copy
        sku = str(row.get("SKU") or "")
        parsed = parse_sku(sku, mappings)

        if parsed["sku_format"] != "unknown":
            # For listing SKUs, derive per-item category and size from the
            # compound SKU structure (e.g. "bs" → "big, 350" for BPBT listing).
            if parsed["sku_format"] == "listing":
                bom_items = derive_listing_bom_items(sku, mappings)
                if bom_items:
                    seen_cats: list[str] = []
                    for item in bom_items:
                        if item["category"] and item["category"] not in seen_cats:
                            seen_cats.append(item["category"])
                    if seen_cats:
                        parsed["category"] = ", ".join(seen_cats)
                    sizes = [item["size"] for item in bom_items if item["size"]]
                    parsed["size"] = ", ".join(sizes) if sizes else None

            any_filled = _apply_parsed(row, parsed, mfr_override, mappings)
            if any_filled:
                enriched += 1
            else:
                skipped += 1
        else:
            # Primary SKU not recognised — try BOM SKUs as fallback
            bom_cell = str(row.get("BOM_SKUs") or "")
            bom_parsed = parse_sku_from_bom(bom_cell, mappings)
            has_bom_data = any(
                bom_parsed[f] is not None
                for f in ("manufacturer", "category", "size", "variant", "color")
            )
            if has_bom_data:
                any_filled = _apply_parsed(row, bom_parsed, mfr_override, mappings)
                if any_filled:
                    bom_fallback += 1
                else:
                    skipped += 1
            else:
                # No useful info from BOM either — still apply mfr override if set
                if mfr_override:
                    row[COL_MANUFACTURER] = _mfr_display_name(mfr_override, mappings)
                    skipped += 1  # counted as skipped (no SKU-derived data)
                else:
                    skipped += 1

        result.append(row)

    return result, enriched, bom_fallback, skipped


def main():
    parser = argparse.ArgumentParser(description="Enrich a Google Sheet tab from SKU parsing.")
    parser.add_argument("--sheet-url", required=True, help="URL of the Google Sheet to enrich.")
    parser.add_argument("--manufacturer", help="Override: force this manufacturer code on ALL rows.")
    parser.add_argument("--tab", default=_DEFAULT_TAB,
                        help=f"Tab to read and write (default: '{_DEFAULT_TAB}').")
    args = parser.parse_args()

    mappings = Mappings()

    print(f"[1/4] Opening sheet ...")
    spreadsheet = open_sheet(args.sheet_url)
    print(f"      {spreadsheet.title}")

    print(f"[2/4] Reading '{args.tab}' tab ...")
    rows = read_tab(spreadsheet, args.tab)
    print(f"      {len(rows)} rows loaded.")

    if not rows:
        print("[warn] Tab is empty. Nothing to do.")
        sys.exit(0)

    if args.manufacturer:
        print(f"[3/4] Enriching rows (manufacturer override: '{args.manufacturer}') ...")
    else:
        print(f"[3/4] Enriching rows from SKU ...")

    enriched_rows, n_enriched, n_bom, n_skipped = enrich_rows(rows, mappings, args.manufacturer)

    print(f"      {n_enriched} rows enriched from SKU, {n_bom} from BOM fallback, {n_skipped} unchanged.")

    print(f"[4/4] Writing enriched data back to '{args.tab}' tab ...")
    write_tab(spreadsheet, args.tab, enriched_rows)

    print(f"\n[done] Sheet URL: {spreadsheet.url}")


if __name__ == "__main__":
    main()
