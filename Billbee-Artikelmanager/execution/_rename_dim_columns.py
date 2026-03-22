"""
One-time migration: rename LengthMm/WidthMm/HeightMm → LengthCm/WidthCm/HeightCm
and divide existing non-empty values by 10 in the 'downloaded' tab.

Usage:
  python execution/_rename_dim_columns.py --sheet-url URL [--sheet-url URL2 ...]
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from google_sheets_client import open_sheet

TAB_NAME = "downloaded"
RENAMES = {"LengthMm": "LengthCm", "WidthMm": "WidthCm", "HeightMm": "HeightCm"}


def migrate_sheet(spreadsheet):
    ws = spreadsheet.worksheet(TAB_NAME)
    all_values = ws.get_all_values()
    if not all_values:
        print("  [skip] Tab is empty.")
        return

    headers = all_values[0]
    print(f"  Headers: {headers[:10]}...")

    # Find columns to rename
    col_indices = {}  # old_name → 0-based column index
    for old_name in RENAMES:
        if old_name in headers:
            col_indices[old_name] = headers.index(old_name)

    if not col_indices:
        print("  [skip] No LengthMm/WidthMm/HeightMm columns found.")
        return

    print(f"  Found columns to migrate: {list(col_indices.keys())}")

    # Rename header cells
    for old_name, col_idx in col_indices.items():
        new_name = RENAMES[old_name]
        # gspread is 1-based
        ws.update_cell(1, col_idx + 1, new_name)
        print(f"  Renamed header col {col_idx+1}: {old_name!r} → {new_name!r}")

    # Divide existing values by 10 in data rows
    updates = []  # (row_1based, col_1based, new_value)
    for row_idx, row in enumerate(all_values[1:], start=2):
        for old_name, col_idx in col_indices.items():
            if col_idx < len(row):
                raw = row[col_idx].strip()
                if raw:
                    try:
                        new_val = round(float(raw) / 10, 2)
                        updates.append((row_idx, col_idx + 1, new_val))
                    except ValueError:
                        pass  # non-numeric, leave as-is

    if updates:
        # Batch update using cell_list
        cell_list = ws.range(1, 1, len(all_values), len(headers))
        cell_map = {(c.row, c.col): c for c in cell_list}
        for row_i, col_i, val in updates:
            if (row_i, col_i) in cell_map:
                cell_map[(row_i, col_i)].value = val
        # Only update the changed cells
        changed = [cell_map[(r, c)] for r, c, _ in updates if (r, c) in cell_map]
        ws.update_cells(changed, value_input_option="USER_ENTERED")
        print(f"  Updated {len(changed)} dimension cells (÷10).")
    else:
        print("  No non-empty dimension values to convert.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sheet-url", required=True, action="append", dest="urls",
                        help="Sheet URL (repeat for multiple sheets)")
    args = parser.parse_args()

    for url in args.urls:
        print(f"\nOpening: {url}")
        ss = open_sheet(url)
        print(f"  Title: {ss.title}")
        migrate_sheet(ss)

    print("\n[done]")


if __name__ == "__main__":
    main()
