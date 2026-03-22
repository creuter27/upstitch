"""
One-time migration:
1. Rename 4 column headers in 'downloaded' tab:
     Title             → Title DE
     ShortDescription  → Short description DE
     StockCurrent      → Stock current Standard
     StockWarning      → Stock min Standard
2. Append any Billbee XLSX column headers that are missing from the sheet
   (as empty columns — values will be filled on next download).
3. Update the ColumnsToUpload tab accordingly.

Usage:
  python execution/_add_missing_columns.py --sheet-url URL [--sheet-url URL2 ...]
"""
import argparse
import sys
import time
from pathlib import Path

import gspread

sys.path.insert(0, str(Path(__file__).parent.parent))
from google_sheets_client import open_sheet

# Step 1: simple renames (old → new)
RENAMES: dict[str, str] = {
    "Title":            "Title DE",
    "ShortDescription": "Short description DE",
    "StockCurrent":     "Stock current Standard",
    "StockWarning":     "Stock min Standard",
}

# Step 2: full ordered list of Billbee XLSX columns (verified Mar 2026).
# Columns already present in our sheet (or renamed above) will be skipped.
# Subarticle/Source/Image columns that require special handling at export time
# are included so they appear in the sheet and ColumnsToUpload.
BILLBEE_XLSX_COLUMNS: list[str] = [
    "Id", "SKU", "EAN", "IsBom",
    "Price gross", "CostPrice gross", "VAT index",
    "Manufacturer",
    "Category1", "Category2", "Category3",
    "TARIC Code", "Weight (g) gross", "Weight (g) net",
    "Country of origin",
    "Units per item", "Unit",
    "Shipping product", "Delivery time",
    "Title DE", "Short description DE", "Long description DE",
    "Materials DE", "Tags DE",
    "Invoice text DE", "Export description DE", "Basic attributes DE",
    "Image 1", "Image 2", "Image 3", "Image 4",
    "Image 5", "Image 6", "Image 7", "Image 8",
    "Stock current Standard", "Stock min Standard",
    "Stock target Standard", "Stock place Standard",
    "WidthCm", "HeightCm", "LengthCm",
    "Price net", "CostPrice net",
    "Condition",
    "Custom Field Produktkategorie", "Custom Field Produktgröße",
    "Custom Field Produktfarbe",
    "Source 1 Shop Id", "Source 1 Partner", "Source 1 Source Id",
    "Source 1 Stocksync active", "Source 1 Stock min", "Source 1 Stock Max",
    "Source 1 Units per item",
    "Subarticle 1 Id", "Subarticle 1 SKU", "Subarticle 1 Name", "Subarticle 1 Amount",
    "Resolve Parts", "Set Split Price", "Status",
]


def migrate_downloaded_tab(ss) -> list[str]:
    """
    Rename headers + append missing columns.
    Returns list of all column names after migration.
    """
    ws = ss.worksheet("downloaded")
    headers = ws.row_values(1)
    print(f"  'downloaded' tab: {len(headers)} columns before migration.")

    # --- Step 1: rename ---
    renamed = 0
    for col_idx, header in enumerate(headers, start=1):
        new_name = RENAMES.get(header)
        if new_name:
            ws.update_cell(1, col_idx, new_name)
            headers[col_idx - 1] = new_name
            print(f"    Renamed col {col_idx}: {header!r} → {new_name!r}")
            renamed += 1
    print(f"  → {renamed} header(s) renamed.")

    # --- Step 2: append missing ---
    current_set = set(headers)
    to_add = [col for col in BILLBEE_XLSX_COLUMNS if col not in current_set]
    if not to_add:
        print("  → No missing columns to add.")
    else:
        # Expand the grid to accommodate the new columns first
        needed_cols = len(headers) + len(to_add)
        ws.resize(cols=needed_cols)
        print(f"  Grid resized to {needed_cols} columns.")
        # Batch-write all new header cells in a single API call
        from gspread.utils import rowcol_to_a1
        cell_range = (
            f"{rowcol_to_a1(1, len(headers) + 1)}:"
            f"{rowcol_to_a1(1, needed_cols)}"
        )
        ws.update(values=[to_add], range_name=cell_range, value_input_option="RAW")
        for col in to_add:
            print(f"    Added: {col!r}")
            headers.append(col)
        print(f"  → {len(to_add)} column(s) added.")

    return headers


def migrate_columns_to_upload(ss, all_headers: list[str]) -> None:
    """
    Update the ColumnsToUpload tab:
    - Rename old column names to new names in column A
    - Append new columns (unchecked) for any headers missing from the tab
    """
    try:
        ws = ss.worksheet("ColumnsToUpload")
    except gspread.exceptions.WorksheetNotFound:
        print("  ColumnsToUpload tab not found — skipping.")
        return

    data = ws.get_all_values()
    existing: dict[str, bool] = {}   # col_name → checked
    for row in data[1:]:
        if row:
            col_name = row[0]
            checked  = row[1].strip().upper() == "TRUE" if len(row) > 1 else False
            existing[col_name] = checked

    renamed = 0
    for row_idx, row in enumerate(data[1:], start=2):
        if not row:
            continue
        old_name = row[0]
        new_name = RENAMES.get(old_name)
        if new_name:
            ws.update_cell(row_idx, 1, new_name)
            # Update in-memory
            existing[new_name] = existing.pop(old_name, False)
            print(f"    ColumnsToUpload row {row_idx}: {old_name!r} → {new_name!r}")
            renamed += 1
    print(f"  → {renamed} ColumnsToUpload row(s) renamed.")

    # Append missing rows (unchecked) — batch write for efficiency
    new_rows = []
    for col in all_headers:
        if col not in existing:
            new_rows.append([col, False])
            existing[col] = False
    if new_rows:
        ws.append_rows(new_rows, value_input_option="RAW")
        print(f"  → {len(new_rows)} new row(s) appended to ColumnsToUpload (unchecked).")
    else:
        print("  → No new rows needed in ColumnsToUpload.")


def migrate_sheet(ss) -> None:
    print(f"  Title: {ss.title}")
    print("  Migrating 'downloaded' tab …")
    all_headers = migrate_downloaded_tab(ss)
    print("  Migrating 'ColumnsToUpload' tab …")
    migrate_columns_to_upload(ss, all_headers)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sheet-url", required=True, action="append", dest="urls",
                        help="Sheet URL (repeat for multiple sheets)")
    args = parser.parse_args()

    for url in args.urls:
        print(f"\nOpening: {url}")
        ss = open_sheet(url)
        migrate_sheet(ss)

    print("\n[done]")


if __name__ == "__main__":
    main()
