"""
Import a Billbee XLSX export as a 'new' tab, filter out already-known
articles, and append the genuinely new rows to the 'upload' tab.

Steps
-----
1. Open the Google Sheet by name ("Billbee Artikelmanager {MFR}") or URL.
2. Read the XLSX file.
3. Collect all existing Ids from the 'upload' tab.
4. Filter XLSX rows:
     - SKU must contain the manufacturer token (case-insensitive)
     - Id must NOT already exist in the 'upload' tab
5. Write only the filtered (genuinely new) rows to the 'new' tab.
   This makes the 'new' tab a permanent reference for the export step.
6. Append the filtered rows to the 'upload' tab (matched by column name;
   management columns Type / BOM_Count / BOM_SKUs / Action are derived).
7. Write the sheet URL to .tmp/sheet_url.txt for subsequent pipeline steps.

Usage
-----
  python execution/import_new_articles.py \\
      --xlsx-file backups/Billbee_Artikelexport_2026-03-01.xlsx \\
      --manufacturer TRX

  # Override sheet lookup:
  python execution/import_new_articles.py \\
      --xlsx-file ... --manufacturer TRX --sheet-url URL
  python execution/import_new_articles.py \\
      --xlsx-file ... --manufacturer TRX --sheet-name "Billbee Artikelmanager TRX"
"""

import argparse
import re
import sys
from pathlib import Path

import gspread
from gspread.utils import rowcol_to_a1
import openpyxl

sys.path.insert(0, str(Path(__file__).parent.parent))

from google_sheets_client import open_sheet, open_sheet_by_name

TAB_NEW    = "new"
TAB_UPLOAD = "ProductList"

# Management columns added to 'upload' that are not in the raw XLSX.
_MGMT_COLS = ["Type", "BOM_Count", "BOM_SKUs", "Action"]


# ──────────────────────────────────────────────────────────────────────────────
# XLSX reading (mirrors import_from_xlsx.read_xlsx)
# ──────────────────────────────────────────────────────────────────────────────

def _read_xlsx(path: Path) -> tuple[list[str], list[list[str]]]:
    """Read XLSX; return (headers, data_rows) as strings. Empty rows skipped."""
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb.active
    raw_rows = list(ws.iter_rows(values_only=True))
    wb.close()

    if not raw_rows:
        raise ValueError("XLSX file is empty")

    headers = [str(h).strip() if h is not None else "" for h in raw_rows[0]]
    n = len(headers)
    data: list[list[str]] = []
    for raw in raw_rows[1:]:
        if not any(v is not None and str(v).strip() for v in raw):
            continue
        row = [str(v).strip() if v is not None else "" for v in raw]
        data.append((row + [""] * n)[:n])
    return headers, data


# ──────────────────────────────────────────────────────────────────────────────
# Management column derivation
# ──────────────────────────────────────────────────────────────────────────────

def _subarticle_skus(headers: list[str], row: list[str]) -> list[str]:
    pairs: list[tuple[int, str]] = []
    for col, val in zip(headers, row):
        m = re.fullmatch(r"Subarticle (\d+) SKU", col)
        if m and val.strip():
            pairs.append((int(m.group(1)), val.strip()))
    pairs.sort()
    return [v for _, v in pairs]


def _derive_mgmt(headers: list[str], row: list[str]) -> dict[str, str]:
    """Return the four management column values derived from an XLSX row."""
    d = dict(zip(headers, row))
    is_bom   = d.get("IsBom", "").strip().upper()
    sub_skus = _subarticle_skus(headers, row)
    return {
        "Type":      "2" if is_bom == "TRUE" else "1",
        "BOM_Count": str(len(sub_skus)),
        "BOM_SKUs":  " | ".join(sub_skus),
        "Action":    "",
    }


# ──────────────────────────────────────────────────────────────────────────────
# Sheet helpers
# ──────────────────────────────────────────────────────────────────────────────

def _write_tab(
    ss: gspread.Spreadsheet,
    tab_name: str,
    headers: list[str],
    data: list[list[str]],
) -> None:
    """Create or overwrite a worksheet with headers + data rows."""
    n_rows = len(data) + 1
    n_cols = len(headers)
    try:
        ws = ss.worksheet(tab_name)
        ws.clear()
        if ws.col_count < n_cols:
            ws.resize(cols=n_cols)
    except gspread.exceptions.WorksheetNotFound:
        ws = ss.add_worksheet(title=tab_name, rows=n_rows + 10, cols=n_cols)
    ws.update(values=[headers] + data, range_name="A1", value_input_option="RAW")


def _col_letter(n: int) -> str:
    """1-based column index → letter(s), e.g. 1→'A', 27→'AA'."""
    return rowcol_to_a1(1, n)[:-1]


def _save_url(url: str) -> None:
    tmp = Path(".tmp")
    tmp.mkdir(exist_ok=True)
    (tmp / "sheet_url.txt").write_text(url, encoding="utf-8")


def _normalize_id(val: str) -> str:
    """Normalize a Billbee Id for comparison.

    openpyxl reads large integers as floats (e.g. 300000068127156 → 3.0e+14),
    so str() gives '300000068127156.0'.  Google Sheets returns '300000068127156'.
    Converting through int(float()) makes both sides canonical.
    Non-numeric values are returned unchanged.
    """
    val = val.strip()
    if not val:
        return val
    try:
        return str(int(float(val)))
    except (ValueError, OverflowError):
        return val


# ──────────────────────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import new Billbee articles from XLSX into the 'upload' tab.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--xlsx-file",    required=True, type=Path,
                        help="Path to the Billbee XLSX export file.")
    parser.add_argument("--manufacturer", required=True,
                        help="Manufacturer code, e.g. TRX. Used to filter SKUs.")
    grp = parser.add_mutually_exclusive_group()
    grp.add_argument("--sheet-url",  help="Google Sheet URL (takes precedence).")
    grp.add_argument("--sheet-name", help="Google Sheet title (exact match in Drive).")
    args = parser.parse_args()

    if not args.xlsx_file.exists():
        print(f"[error] File not found: {args.xlsx_file}")
        sys.exit(1)

    # ── Open sheet ────────────────────────────────────────────────────────────
    if args.sheet_url:
        print(f"Opening sheet by URL ...")
        ss = open_sheet(args.sheet_url)
    elif args.sheet_name:
        print(f"Opening sheet: '{args.sheet_name}' ...")
        ss = open_sheet_by_name(args.sheet_name)
    else:
        name = f"Billbee Artikelmanager {args.manufacturer}"
        print(f"Opening sheet: '{name}' ...")
        ss = open_sheet_by_name(name)
    print(f"  {ss.title}")

    # ── [1/4] Read XLSX ───────────────────────────────────────────────────────
    print(f"\n[1/4] Reading '{args.xlsx_file.name}' ...")
    new_headers, new_data = _read_xlsx(args.xlsx_file)
    print(f"  {len(new_headers)} columns, {len(new_data)} rows.")

    # ── [2/4] Read existing Ids from 'upload' tab ─────────────────────────────
    print(f"\n[2/4] Reading existing Ids from '{TAB_UPLOAD}' tab ...")
    upload_ws      = ss.worksheet(TAB_UPLOAD)
    upload_raw     = upload_ws.get_all_values()
    upload_headers = upload_raw[0] if upload_raw else []
    upload_data    = upload_raw[1:] if len(upload_raw) > 1 else []

    id_col_up = upload_headers.index("Id") if "Id" in upload_headers else None

    upload_ids: set[str] = set()
    if id_col_up is not None:
        for row in upload_data:
            val = row[id_col_up].strip() if len(row) > id_col_up else ""
            if val:
                upload_ids.add(_normalize_id(val))
    print(f"  {len(upload_ids)} existing Ids in '{TAB_UPLOAD}'.")

    # ── [3/4] Filter XLSX rows ────────────────────────────────────────────────
    print(f"\n[3/4] Filtering: SKU contains '{args.manufacturer}', Id not in upload ...")
    id_col_new  = new_headers.index("Id")  if "Id"  in new_headers else None
    sku_col_new = new_headers.index("SKU") if "SKU" in new_headers else None

    mfr_upper      = args.manufacturer.upper()
    genuinely_new: list[list[str]] = []
    skipped_mfr    = 0
    skipped_dup    = 0

    for row in new_data:
        sku = row[sku_col_new].strip() if (sku_col_new is not None and len(row) > sku_col_new) else ""
        pid = row[id_col_new].strip()  if (id_col_new  is not None and len(row) > id_col_new)  else ""

        if mfr_upper not in sku.upper():
            skipped_mfr += 1
            continue
        if pid and _normalize_id(pid) in upload_ids:
            skipped_dup += 1
            continue

        genuinely_new.append(row)

    print(f"  {len(genuinely_new)} new rows"
          f"  ({skipped_mfr} wrong manufacturer, {skipped_dup} already in upload).")

    if not genuinely_new:
        print("\n[info] No new articles to add.")
        _save_url(ss.url)
        print(f"  Sheet URL saved to .tmp/sheet_url.txt")
        return

    print("\n  New articles:")
    for row in genuinely_new:
        sku = row[sku_col_new] if sku_col_new is not None else "?"
        pid = row[id_col_new]  if id_col_new  is not None else "?"
        print(f"    SKU={sku}  Id={pid}")

    # ── [4/4] Write filtered rows to 'new' tab ────────────────────────────────
    print(f"\n[4/4] Writing {len(genuinely_new)} filtered rows to '{TAB_NEW}' tab ...")
    _write_tab(ss, TAB_NEW, new_headers, genuinely_new)
    print(f"  {len(genuinely_new)} rows written.")

    # ── Save sheet URL for pipeline steps ─────────────────────────────────────
    _save_url(ss.url)
    print(f"\n[done] {len(genuinely_new)} new article(s) written to '{TAB_NEW}' tab.")
    print(f"  Sheet: {ss.url}")
    print(f"  URL saved to .tmp/sheet_url.txt")


if __name__ == "__main__":
    main()
