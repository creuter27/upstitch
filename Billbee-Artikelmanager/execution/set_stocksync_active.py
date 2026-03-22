"""
Ensure 'Source N Stocksync active' = 1 for every listing row where the source
slot is populated.

What this script does
---------------------
For every row in the 'upload' tab where IsBom=TRUE (listing / BOM product):
  For each Source N slot (1–7) where at least one of
      Shop Id, Partner, Source Id
  is non-empty, set 'Source N Stocksync active' to "1" if it is not already.

Physical rows (IsBom != TRUE) are left untouched.

Usage
-----
  # Dry-run — show what would change without writing:
  python execution/set_stocksync_active.py --sheet-url URL

  # Apply changes:
  python execution/set_stocksync_active.py --sheet-url URL --execute
"""

import argparse
import re
import sys
from pathlib import Path

from gspread.utils import rowcol_to_a1

sys.path.insert(0, str(Path(__file__).parent.parent))
from google_sheets_client import open_sheet

# A source slot is considered "populated" if any of these sub-fields is non-empty.
IDENTIFIER_FIELDS = {"Shop Id", "Partner", "Source Id"}


def main():
    parser = argparse.ArgumentParser(
        description="Set Source N Stocksync active=1 for all populated listing sources.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--sheet-url", required=True)
    parser.add_argument(
        "--execute", action="store_true",
        help="Write changes to the sheet. Default is dry-run (read-only).",
    )
    args = parser.parse_args()

    ss = open_sheet(args.sheet_url)
    print(f"Sheet: {ss.title}")

    # ── Read upload tab ───────────────────────────────────────────────────────
    print("\nReading 'ProductList' tab …")
    ws = ss.worksheet("ProductList")
    raw = ws.get_all_values()
    headers = raw[0]
    data = raw[1:]
    print(f"  {len(data)} rows, {len(headers)} columns.")

    # ── Discover Source N groups ──────────────────────────────────────────────
    src_re = re.compile(r"Source (\d+) (.+)")
    src_groups: dict[int, dict[str, int]] = {}  # n → {sub_field: col_idx}
    for i, h in enumerate(headers):
        m = src_re.fullmatch(h)
        if m:
            n, field = int(m.group(1)), m.group(2)
            src_groups.setdefault(n, {})[field] = i

    if not src_groups:
        print("[error] No 'Source N …' columns found in upload tab.")
        sys.exit(1)

    print(f"  Found Source slots: {sorted(src_groups)}")

    isbom_idx = headers.index("IsBom") if "IsBom" in headers else None
    if isbom_idx is None:
        print("[error] 'IsBom' column not found in upload tab.")
        sys.exit(1)

    # ── Find cells that need updating ─────────────────────────────────────────
    # List of (sheet_row_1based, col_idx_0based, source_n, sku)
    to_fix: list[tuple[int, int, int, str]] = []

    sku_idx = headers.index("SKU") if "SKU" in headers else None

    for row_i, row in enumerate(data):
        isbom = row[isbom_idx].strip().upper() if len(row) > isbom_idx else ""
        if isbom != "TRUE":
            continue

        sku = row[sku_idx].strip() if (sku_idx is not None and len(row) > sku_idx) else ""

        for n, cols in src_groups.items():
            # Is this source slot populated?
            populated = any(
                row[cols[f]].strip()
                for f in IDENTIFIER_FIELDS
                if f in cols and len(row) > cols[f]
            )
            if not populated:
                continue

            sa_idx = cols.get("Stocksync active")
            if sa_idx is None:
                continue

            current = row[sa_idx].strip() if len(row) > sa_idx else ""
            if current != "1":
                sheet_row = row_i + 2  # +1 for header, +1 for 1-based
                to_fix.append((sheet_row, sa_idx, n, sku))

    mode = "EXECUTE" if args.execute else "DRY-RUN"
    print(f"\n[{mode}] {len(to_fix)} cell(s) need 'Stocksync active' set to 1.")

    if not to_fix:
        print("Nothing to do.")
        return

    # Show a preview (up to 20 lines)
    preview = to_fix[:20]
    print(f"\n  {'Sheet row':<12}  {'Source':<8}  {'Current→New':<15}  SKU")
    for sheet_row, col_i, n, sku in preview:
        cell = rowcol_to_a1(sheet_row, col_i + 1)
        print(f"  {cell:<12}  Source {n:<2}  '' → '1'          {sku}")
    if len(to_fix) > 20:
        print(f"  … and {len(to_fix) - 20} more.")

    if not args.execute:
        print(f"\n[DRY-RUN] Pass --execute to apply these changes.")
        return

    # ── Write changes via batch_update ────────────────────────────────────────
    print("\nWriting changes …")
    updates = [
        {
            "range": rowcol_to_a1(sheet_row, col_i + 1),
            "values": [["1"]],
        }
        for sheet_row, col_i, n, sku in to_fix
    ]

    BATCH_SIZE = 500
    total = (len(updates) + BATCH_SIZE - 1) // BATCH_SIZE
    for b, i in enumerate(range(0, len(updates), BATCH_SIZE), start=1):
        ws.batch_update(updates[i:i + BATCH_SIZE], value_input_option="RAW")
        print(f"  Batch {b}/{total}: {len(updates[i:i + BATCH_SIZE])} cells written.")

    print(f"\n[done] {len(to_fix)} cell(s) set to 1.")


if __name__ == "__main__":
    main()
