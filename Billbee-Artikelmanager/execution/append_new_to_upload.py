"""
Append enriched 'new' tab rows to the 'upload' tab.

Run this after all pipeline scripts have enriched the 'new' tab.  This step
makes future pipeline runs aware of these articles (so they are not treated as
new again).

Management columns (Type, BOM_Count, BOM_SKUs, Action) are derived from the
enriched row data before appending.

Usage:
  python execution/append_new_to_upload.py --sheet-url URL
"""

import argparse
import re
import sys
from pathlib import Path

import gspread
from gspread.utils import rowcol_to_a1

sys.path.insert(0, str(Path(__file__).parent.parent))

from google_sheets_client import open_sheet, read_tab

TAB_NEW    = "new"
TAB_UPLOAD = "ProductList"

_MGMT_COLS = ["Type", "BOM_Count", "BOM_SKUs", "Action"]


def _col_letter(n: int) -> str:
    """1-based column index → letter(s)."""
    return rowcol_to_a1(1, n)[:-1]


def _derive_mgmt(row: dict) -> dict:
    """Derive management column values from an enriched row dict."""
    is_bom = str(row.get("IsBom") or "").strip().upper()

    pairs: list[tuple[int, str]] = []
    for key, val in row.items():
        m = re.fullmatch(r"Subarticle (\d+) SKU", key)
        if m and str(val).strip():
            pairs.append((int(m.group(1)), str(val).strip()))
    pairs.sort()
    sub_skus = [v for _, v in pairs]

    return {
        "Type":      "2" if is_bom == "TRUE" else "1",
        "BOM_Count": str(len(sub_skus)),
        "BOM_SKUs":  " | ".join(sub_skus),
        "Action":    "",
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Append enriched 'new' tab rows to the 'upload' tab.",
    )
    parser.add_argument("--sheet-url", required=True, help="URL of the Google Sheet.")
    args = parser.parse_args()

    print("Opening sheet ...")
    ss = open_sheet(args.sheet_url)
    print(f"  {ss.title}")

    # ── [1/3] Read 'new' tab ──────────────────────────────────────────────────
    print(f"\n[1/3] Reading '{TAB_NEW}' tab ...")
    new_rows = read_tab(ss, TAB_NEW)
    if not new_rows:
        print(f"[warn] '{TAB_NEW}' tab is empty — nothing to append.")
        sys.exit(0)
    print(f"  {len(new_rows)} row(s) to append.")

    # ── [2/3] Read 'upload' tab headers ──────────────────────────────────────
    print(f"\n[2/3] Reading '{TAB_UPLOAD}' tab headers ...")
    upload_ws  = ss.worksheet(TAB_UPLOAD)
    upload_raw = upload_ws.get_all_values()

    if not upload_raw:
        print(f"[error] '{TAB_UPLOAD}' tab is empty (no headers). "
              "Cannot determine column order.")
        sys.exit(1)

    upload_headers = upload_raw[0]
    upload_data    = upload_raw[1:] if len(upload_raw) > 1 else []
    print(f"  {len(upload_headers)} columns, {len(upload_data)} existing data rows.")

    # ── [3/3] Append enriched rows ────────────────────────────────────────────
    print(f"\n[3/3] Appending {len(new_rows)} row(s) to '{TAB_UPLOAD}' tab ...")

    appended: list[list[str]] = []
    for row in new_rows:
        merged = dict(row)
        merged.update(_derive_mgmt(row))
        appended.append([str(merged.get(col) or "") for col in upload_headers])

    start_row    = len(upload_data) + 2        # 1-based; +1 for header row
    end_row      = start_row + len(appended) - 1
    last_col_ltr = _col_letter(len(upload_headers))

    if upload_ws.row_count < end_row + 5:
        upload_ws.resize(rows=end_row + 10, cols=len(upload_headers))

    upload_ws.update(
        values=appended,
        range_name=f"A{start_row}:{last_col_ltr}{end_row}",
        value_input_option="RAW",
    )

    print(f"  Rows {start_row}–{end_row} written to '{TAB_UPLOAD}'.")
    for row in new_rows:
        print(f"    SKU={row.get('SKU', '?')}  Id={row.get('Id', '?')}")

    print(f"\n[done] {len(new_rows)} article(s) appended to '{TAB_UPLOAD}'.")


if __name__ == "__main__":
    main()
