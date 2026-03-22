"""
Refresh all API-derived columns in the 'downloaded' tab from the Billbee API
without overwriting values that were set by our pipeline scripts.

Protected columns (never overwritten):
  Custom Field Produktkategorie, Custom Field Produktgröße,
  Custom Field Produktvariante, Custom Field Produktfarbe,
  BOM_SKUs, TARIC Code, Country of origin, Action

All other columns are updated from the Billbee API.  Rows with no Billbee Id,
or whose Id is not returned by the current API query, are left unchanged.

Usage:
  # Refresh TRX products only (fast — uses search endpoint):
  python execution/refresh_from_api.py --sheet-url URL --manufacturer TRX

  # Refresh all products (slow — downloads full catalog):
  python execution/refresh_from_api.py --sheet-url URL
"""

import argparse
import sys
from pathlib import Path

from gspread.utils import rowcol_to_a1

sys.path.insert(0, str(Path(__file__).parent.parent))

from billbee_client import BillbeeClient
from google_sheets_client import open_sheet
from execution.download_to_sheet import (
    flatten_product,
    build_mfr_terms,
    build_cat_terms,
    path_a_download,
    path_b_download,
)
from execution.mappings_loader import Mappings

# Columns whose values are set by our pipeline scripts, not just downloaded from
# the API.  These are NEVER overwritten — re-run the relevant pipeline script to
# update them.
NEVER_OVERWRITE: set[str] = {
    "Custom Field Produktkategorie",
    "Custom Field Produktgröße",
    "Custom Field Produktvariante",
    "Custom Field Produktfarbe",
    "BOM_SKUs",
    "TARIC Code",
    "Country of origin",
    "Action",
}


def _int_id(val) -> int | None:
    try:
        return int(float(str(val))) if str(val).strip() else None
    except (ValueError, TypeError):
        return None


def _to_str(val) -> str:
    """Stringify a value from flatten_product for storage in Sheets."""
    if isinstance(val, bool):
        return "TRUE" if val else "FALSE"
    if val is None:
        return ""
    return str(val)


def main():
    parser = argparse.ArgumentParser(
        description="Refresh API-derived columns without overwriting pipeline edits.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="\n".join([
            "Protected columns (never overwritten):",
            *[f"  {c}" for c in sorted(NEVER_OVERWRITE)],
        ]),
    )
    parser.add_argument("--sheet-url", required=True,
                        help="URL of the Google Sheet with the 'downloaded' tab.")
    parser.add_argument("--manufacturer",
                        help="Only refresh this manufacturer's rows (e.g. TRX). "
                             "Omit to refresh everything (slower).")
    parser.add_argument("--category",
                        help="Only refresh rows matching this category token.")
    args = parser.parse_args()

    client = BillbeeClient()
    mappings = Mappings()

    # ── Step 1: custom field definitions ─────────────────────────────────────
    print("[1/5] Fetching custom field definitions ...")
    field_defs = client.get_custom_field_definitions()
    print(f"      {len(field_defs)} custom field(s): {list(field_defs.values())}")

    # ── Step 2: read existing sheet ───────────────────────────────────────────
    print("[2/5] Reading existing sheet ...")
    ss = open_sheet(args.sheet_url)
    ws = ss.worksheet("downloaded")
    all_values = ws.get_all_values()
    if not all_values:
        print("[error] 'downloaded' tab is empty.")
        sys.exit(1)
    headers = all_values[0]
    data_rows = all_values[1:]
    print(f"      {len(headers)} columns, {len(data_rows)} data rows.")

    try:
        id_col_idx = headers.index("Id")
    except ValueError:
        print("[error] 'Id' column not found in sheet.")
        sys.exit(1)

    header_to_idx: dict[str, int] = {h: i for i, h in enumerate(headers)}
    n_cols = len(headers)

    # Map Billbee Id (int) → data_rows index (0-based)
    id_to_row_idx: dict[int, int] = {}
    for i, row in enumerate(data_rows):
        if len(row) > id_col_idx:
            bid = _int_id(row[id_col_idx])
            if bid is not None:
                id_to_row_idx[bid] = i
    print(f"      {len(id_to_row_idx)} rows with a Billbee Id.")

    # ── Step 3: download from API ─────────────────────────────────────────────
    print("[3/5] Downloading products from Billbee API ...")
    mfr_terms = build_mfr_terms(args.manufacturer, mappings) if args.manufacturer else None
    cat_terms  = build_cat_terms(args.category, mappings) if args.category else None

    if mfr_terms or cat_terms:
        search_query = " ".join(filter(None, [args.manufacturer, args.category]))
        products = path_a_download(client, field_defs, mfr_terms, cat_terms, search_query)
        if products is None:
            products = path_b_download(client, field_defs, mfr_terms, cat_terms)
    else:
        products = path_b_download(client, field_defs, None, None)
    print(f"      {len(products)} products fetched.")

    # Build Billbee Id → flattened product dict
    id_to_api: dict[int, dict] = {}
    for p in products:
        bid = _int_id(p.get("Id"))
        if bid is not None:
            id_to_api[bid] = p

    # ── Step 4: merge API data into sheet rows ────────────────────────────────
    print("[4/5] Building row updates ...")
    matched = skipped = 0
    # Work on a mutable copy of data_rows (lists, not tuples)
    merged_rows: list[list[str] | None] = [None] * len(data_rows)

    for i, row_values in enumerate(data_rows):
        # Pad/truncate to the current column width
        row = list(row_values) + [""] * (n_cols - len(row_values))
        row = row[:n_cols]

        bid = _int_id(row[id_col_idx])
        if bid is None:
            skipped += 1
            continue

        api_data = id_to_api.get(bid)
        if api_data is None:
            skipped += 1
            continue

        matched += 1
        for col_name, api_val in api_data.items():
            if col_name in NEVER_OVERWRITE:
                continue
            col_idx = header_to_idx.get(col_name)
            if col_idx is None:
                continue  # column not present in sheet (shouldn't happen)
            row[col_idx] = _to_str(api_val)

        merged_rows[i] = row

    print(f"      {matched} rows will be updated, {skipped} skipped.")

    # ── Step 5: write to sheet ────────────────────────────────────────────────
    print("[5/5] Writing updates to sheet ...")
    updates = []
    for i, row in enumerate(merged_rows):
        if row is None:
            continue
        sheet_row = i + 2  # +1 for header, +1 for 1-based
        updates.append({
            "range": f"{rowcol_to_a1(sheet_row, 1)}:{rowcol_to_a1(sheet_row, n_cols)}",
            "values": [row],
        })

    BATCH_SIZE = 200  # conservative — each row can be wide (73 cols)
    total_batches = (len(updates) + BATCH_SIZE - 1) // BATCH_SIZE
    for b, i in enumerate(range(0, len(updates), BATCH_SIZE), start=1):
        batch = updates[i:i + BATCH_SIZE]
        ws.batch_update(batch, value_input_option="RAW")
        print(f"      Batch {b}/{total_batches}: {len(batch)} rows written.")

    print(f"\n[done] {matched} rows refreshed, {skipped} skipped.")
    print(f"       Protected columns preserved: {sorted(NEVER_OVERWRITE)}")


if __name__ == "__main__":
    main()
