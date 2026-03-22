"""
Export pipeline-enriched articles to a Billbee-import XLSX file.

How it works
------------
After the pipeline scripts have enriched the tab rows directly, this script:

1. Reads all rows from the source tab (already enriched in-place).
2. Reads the 'ProductList' tab to build the SKU→BillbeeId map for BOM resolution.
3. Uses the 'ColumnsToUpload' tab to select export columns.
4. Builds a Billbee-import-compatible XLSX (same format as export_to_billbee_xlsx.py).
5. Saves the file and reports the path.

Usage
-----
  python execution/export_new_articles.py --sheet-url URL
  python execution/export_new_articles.py --sheet-url URL --tab new
  python execution/export_new_articles.py --sheet-url URL --output my_file.xlsx
"""

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from google_sheets_client import open_sheet, read_tab, read_tab_visible
from execution.export_to_billbee_xlsx import (
    read_columns_to_upload,
    build_xlsx,
    _SKIP_COLS,
)

_DEFAULT_TAB = "ProductList"
TAB_UPLOAD   = "ProductList"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export pipeline-enriched articles to a Billbee XLSX file.",
    )
    parser.add_argument("--sheet-url", required=True,
                        help="URL of the Google Sheet.")
    parser.add_argument("--tab", default=_DEFAULT_TAB,
                        help=f"Tab to read articles from (default: '{_DEFAULT_TAB}').")
    parser.add_argument("--output",
                        help="Output file path. Default: billbee_new_YYYY-MM-DD.xlsx")
    args = parser.parse_args()

    tab = args.tab

    print("Opening sheet ...")
    ss = open_sheet(args.sheet_url)
    print(f"  {ss.title}")

    # ── [1/4] Read source tab — only visible rows (respects active filter) ────
    print(f"\n[1/4] Reading '{tab}' tab (visible rows only) ...")
    try:
        records = read_tab_visible(ss, tab)
    except Exception:
        print(f"[error] '{tab}' tab not found.")
        sys.exit(1)

    if not records:
        print(f"[warn] '{tab}' tab is empty — nothing to export.")
        sys.exit(0)

    print(f"  {len(records)} row(s) to export:")
    for r in records:
        print(f"    SKU={r.get('SKU', '?')}  Id={r.get('Id', '?')}")

    # ── [2/4] Read ProductList tab for BOM SKU→Id resolution ─────────────────
    if tab == TAB_UPLOAD:
        # source tab IS ProductList — no need to read it again
        all_records = list(records)
    else:
        print(f"\n[2/4] Reading '{TAB_UPLOAD}' tab for BOM Id resolution ...")
        upload_records = read_tab(ss, TAB_UPLOAD)
        print(f"  {len(upload_records)} rows.")
        all_records = list(records) + list(upload_records)

    # ── [3/4] Column selection ────────────────────────────────────────────────
    print(f"\n[3/4] Reading column selection from 'ColumnsToUpload' tab ...")
    checked_cols = read_columns_to_upload(ss)

    seen:        set[str]  = set()
    export_cols: list[str] = []
    for col in ("Id", "SKU"):          # always first
        export_cols.append(col)
        seen.add(col)
    for col in checked_cols:
        if col not in seen and col not in _SKIP_COLS:
            export_cols.append(col)
            seen.add(col)
    print(f"  {len(export_cols)} export columns.")

    # ── [4/4] Build and save XLSX ─────────────────────────────────────────────
    print(f"\n[4/4] Building XLSX for {len(records)} row(s) ...")
    wb = build_xlsx(records, all_records, export_cols)

    output_path = (
        Path(args.output) if args.output
        else Path(f"billbee_new_{date.today().strftime('%Y-%m-%d')}.xlsx")
    )
    wb.save(output_path)

    print(f"\n[done] Saved: {output_path}")
    print(f"  {len(records)} article(s) exported.")
    print()
    print("Next step: Billbee → Artikel → Importieren → Billbee XLSX → select file")


if __name__ == "__main__":
    main()
