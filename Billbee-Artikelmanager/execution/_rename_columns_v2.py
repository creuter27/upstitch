"""
One-time migration: rename column headers in the 'downloaded' tab to match
the new Billbee XLSX naming convention.

Renames performed (header only — no value conversion needed):
  Produktkategorie   → Custom Field Produktkategorie
  Produktgröße       → Custom Field Produktgröße
  Produktvariante    → Custom Field Produktvariante
  Produktfarbe       → Custom Field Produktfarbe
  Weight             → Weight (g) net
  WeightGross        → Weight (g) gross
  Price              → Price gross
  CostPrice          → CostPrice gross
  TaricNumber        → TARIC Code
  CountryOfOrigin    → Country of origin

Also updates the ColumnsToUpload tab if it exists (column A values only).

Usage:
  python execution/_rename_columns_v2.py --sheet-url URL [--sheet-url URL2 ...]
"""
import argparse
import sys
from pathlib import Path

import gspread

sys.path.insert(0, str(Path(__file__).parent.parent))
from google_sheets_client import open_sheet

RENAMES: dict[str, str] = {
    "Produktkategorie":  "Custom Field Produktkategorie",
    "Produktgröße":      "Custom Field Produktgröße",
    "Produktvariante":   "Custom Field Produktvariante",
    "Produktfarbe":      "Custom Field Produktfarbe",
    "Weight":            "Weight (g) net",
    "WeightGross":       "Weight (g) gross",
    "Price":             "Price gross",
    "CostPrice":         "CostPrice gross",
    "TaricNumber":       "TARIC Code",
    "CountryOfOrigin":   "Country of origin",
}


def migrate_tab(ws) -> int:
    """Rename headers in worksheet ws. Returns number of headers renamed."""
    headers = ws.row_values(1)
    renamed = 0
    for col_idx, header in enumerate(headers, start=1):
        new_name = RENAMES.get(header)
        if new_name:
            ws.update_cell(1, col_idx, new_name)
            print(f"    col {col_idx}: {header!r} → {new_name!r}")
            renamed += 1
    return renamed


def migrate_columns_to_upload(spreadsheet) -> int:
    """Update column A of ColumnsToUpload tab. Returns number of cells renamed."""
    try:
        ws = spreadsheet.worksheet("ColumnsToUpload")
    except gspread.exceptions.WorksheetNotFound:
        print("  ColumnsToUpload tab not found — skipping.")
        return 0

    data = ws.get_all_values()
    renamed = 0
    for row_idx, row in enumerate(data[1:], start=2):   # skip header row
        if not row:
            continue
        old_name = row[0]
        new_name = RENAMES.get(old_name)
        if new_name:
            ws.update_cell(row_idx, 1, new_name)
            print(f"    ColumnsToUpload row {row_idx}: {old_name!r} → {new_name!r}")
            renamed += 1
    return renamed


def migrate_sheet(spreadsheet) -> None:
    print(f"  Title: {spreadsheet.title}")

    # downloaded tab
    try:
        ws = spreadsheet.worksheet("downloaded")
    except gspread.exceptions.WorksheetNotFound:
        print("  [skip] 'downloaded' tab not found.")
        return

    print("  Renaming headers in 'downloaded' tab …")
    n = migrate_tab(ws)
    print(f"  → {n} header(s) renamed.")

    # ColumnsToUpload tab
    print("  Updating 'ColumnsToUpload' tab …")
    m = migrate_columns_to_upload(spreadsheet)
    print(f"  → {m} row(s) updated.")


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
