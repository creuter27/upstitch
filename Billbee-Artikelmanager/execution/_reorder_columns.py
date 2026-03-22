"""
One-time migration: reorder columns in the 'downloaded' tab to match the
Billbee XLSX export column order, with management-only columns appended at end.

Also fixes two missed renames from the earlier migration:
  Produktionsinfos  → Custom Field Produktionsinfos
  Produktdesign     → Custom Field Produktdesign

And removes the legacy 'Tags' column (superseded by 'Tags DE').

Usage:
  python execution/_reorder_columns.py --sheet-url URL [--sheet-url URL2 ...]
"""
import argparse
import sys
from pathlib import Path

from gspread.utils import rowcol_to_a1

sys.path.insert(0, str(Path(__file__).parent.parent))
from google_sheets_client import open_sheet

# Canonical Billbee XLSX column order (verified Mar 2026).
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

# Preferred order for management/pipeline columns (appended after Billbee columns).
MANAGEMENT_COLUMNS: list[str] = [
    "Type",
    "IsDeactivated",
    "IsDigital",
    "BOM_Count",
    "BOM_SKUs",
    "Sources",
    "Custom Field Produktvariante",
    "Custom Field Produktionsinfos",   # after rename below
    "Custom Field Produktdesign",     # after rename below
    "Action",
]

# In-memory renames applied before reordering.
# Old name → new name (for columns missed by the earlier rename migration).
RENAMES: dict[str, str] = {
    "Produktionsinfos": "Custom Field Produktionsinfos",
    "Produktdesign":    "Custom Field Produktdesign",
}

# Columns to drop entirely (legacy columns superseded by renamed equivalents).
DROP: set[str] = {"Tags"}   # superseded by "Tags DE"


def _compute_new_order(headers: list[str]) -> list[str]:
    """
    Return a new column order:
      1. BILLBEE_XLSX_COLUMNS (those present in headers, in XLSX order)
      2. MANAGEMENT_COLUMNS (those present in headers, in preferred order)
      3. Any remaining columns not covered above (in their current relative order)
    """
    header_set = set(headers)
    seen: set[str] = set()
    new_order: list[str] = []

    for col in BILLBEE_XLSX_COLUMNS:
        if col in header_set and col not in seen:
            new_order.append(col)
            seen.add(col)

    for col in MANAGEMENT_COLUMNS:
        if col in header_set and col not in seen:
            new_order.append(col)
            seen.add(col)

    # Catch-all: any columns not accounted for
    for col in headers:
        if col not in seen:
            new_order.append(col)
            seen.add(col)

    return new_order


def _write_in_chunks(ws, matrix: list[list[str]], chunk_size: int = 200) -> None:
    """Write header + data to ws in row-level chunks (no clear needed)."""
    n_cols = len(matrix[0])
    last_col_a1 = rowcol_to_a1(1, n_cols).rstrip("1")  # e.g. "BU"

    # Header row
    ws.update(
        values=[matrix[0]],
        range_name="A1",
        value_input_option="RAW",
    )

    # Data rows in chunks
    data = matrix[1:]
    total_batches = (len(data) + chunk_size - 1) // chunk_size
    for b, i in enumerate(range(0, len(data), chunk_size), start=1):
        chunk = data[i:i + chunk_size]
        start_row = i + 2          # +1 for 1-based, +1 for header
        end_row   = start_row + len(chunk) - 1
        rng = f"A{start_row}:{last_col_a1}{end_row}"
        ws.update(values=chunk, range_name=rng, value_input_option="RAW")
        print(f"    Batch {b}/{total_batches}: rows {start_row}–{end_row}")


def migrate_downloaded_tab(ss) -> list[str]:
    """Reorder (and fix) the 'downloaded' tab. Returns the new header list."""
    ws = ss.worksheet("downloaded")
    all_values = ws.get_all_values()
    if not all_values:
        print("  [warn] 'downloaded' tab is empty — skipping.")
        return []

    headers = all_values[0]
    data_rows = all_values[1:]
    print(f"  'downloaded': {len(headers)} columns, {len(data_rows)} rows.")

    # Step 1: apply renames in-memory
    renamed_headers = []
    for h in headers:
        renamed_headers.append(RENAMES.get(h, h))
    renamed_count = sum(1 for old, new in zip(headers, renamed_headers) if old != new)
    if renamed_count:
        print(f"  Renamed {renamed_count} header(s): "
              + ", ".join(f"{o!r}→{n!r}" for o, n in zip(headers, renamed_headers) if o != n))

    # Step 2: drop unwanted columns
    keep_indices = [i for i, h in enumerate(renamed_headers) if h not in DROP]
    if len(keep_indices) < len(renamed_headers):
        dropped = [renamed_headers[i] for i in range(len(renamed_headers)) if i not in set(keep_indices)]
        print(f"  Dropping {len(dropped)} column(s): {dropped}")
    renamed_headers = [renamed_headers[i] for i in keep_indices]
    data_rows = [[row[i] if i < len(row) else "" for i in keep_indices] for row in data_rows]

    # Step 3: compute new column order
    new_order = _compute_new_order(renamed_headers)
    if new_order == renamed_headers:
        print("  Already in correct order — no reorder needed.")
        return new_order

    print(f"  Reordering to {len(new_order)} columns ...")
    old_idx = {h: i for i, h in enumerate(renamed_headers)}
    new_matrix = [new_order]
    for row in data_rows:
        new_row = [row[old_idx[col]] if old_idx.get(col) is not None and old_idx[col] < len(row) else ""
                   for col in new_order]
        new_matrix.append(new_row)

    # Step 4: resize sheet if column count changed
    current_n_cols = ws.col_count
    if len(new_order) != current_n_cols:
        ws.resize(cols=len(new_order))
        print(f"  Grid resized: {current_n_cols} → {len(new_order)} columns.")

    print(f"  Writing {len(new_matrix) - 1} rows …")
    _write_in_chunks(ws, new_matrix)
    print(f"  ✓ 'downloaded' tab reordered.")
    return new_order


def migrate_columns_to_upload(ss, new_header_order: list[str]) -> None:
    """Reorder rows in ColumnsToUpload to match the new header order."""
    try:
        ws = ss.worksheet("ColumnsToUpload")
    except Exception:
        print("  ColumnsToUpload tab not found — skipping.")
        return

    data = ws.get_all_values()
    if len(data) < 2:
        return

    col_header = data[0]   # e.g. ["Column", "Upload to Billbee"]
    rows = data[1:]        # each: [col_name, "TRUE"/"FALSE"]

    # Build col_name → checked mapping (apply renames here too)
    existing: dict[str, str] = {}
    for row in rows:
        if row:
            name = RENAMES.get(row[0], row[0])
            checked = row[1] if len(row) > 1 else "FALSE"
            existing[name] = checked

    # Remove dropped columns
    for d in DROP:
        existing.pop(d, None)

    # Build new row order: same order as new_header_order, then any extras
    seen: set[str] = set()
    new_rows = []
    for col in new_header_order:
        if col in existing:
            new_rows.append([col, existing[col]])
            seen.add(col)
    for col, checked in existing.items():
        if col not in seen:
            new_rows.append([col, checked])

    new_matrix = [col_header] + new_rows
    ws.clear()
    ws.update(values=new_matrix, range_name="A1", value_input_option="RAW")
    print(f"  ✓ ColumnsToUpload reordered ({len(new_rows)} rows).")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sheet-url", required=True, action="append", dest="urls")
    args = parser.parse_args()

    for url in args.urls:
        print(f"\nOpening: {url}")
        ss = open_sheet(url)
        print(f"  Title: {ss.title}")
        new_order = migrate_downloaded_tab(ss)
        if new_order:
            migrate_columns_to_upload(ss, new_order)

    print("\n[done]")


if __name__ == "__main__":
    main()
