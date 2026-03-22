"""
Synchronise the 'upload' tab from pipeline changes in the 'pipeline' tab.

What this script does
---------------------
1. Remove rows from 'upload' whose SKU is not in 'pipeline' (~5 665 non-TRX rows).
2. Rows with Action='delete' in the pipeline are excluded from 'upload' and written
   to the 'SKUStoDelete' tab instead (listings before physicals).
3. For every kept row, overwrite pipeline-managed columns:
     Custom Field Produktkategorie / -größe / -variante / -farbe
     TARIC Code, Country of origin
     Weight (g) net, Weight (g) gross
     Price gross
   (Only transferred when the pipeline value is non-empty; upload value kept otherwise.)
4. For listing rows (IsBom=TRUE): rebuild Subarticle columns from pipeline BOM_SKUs.
   Each BOM SKU is resolved to its physical product's Id, SKU, Title DE, Amount=1.
5. For physical rows: clear Subarticle columns (physicals have no BOM).
6. Write 'SKUStoDelete' tab (SKU, Id, Type, Title DE) for Action=delete rows.
   Use upload_to_billbee.py --delete --execute to carry out deletions via the API.

The 'upload' tab is kept in clean Billbee XLSX format — no management columns added.
If a previous run left an 'Action' column in the upload tab it is stripped automatically.

BOM SKU matching
----------------
The pipeline normalises dashes (e.g. TRX-Bottle-350-dino) while Billbee sometimes
stores double dashes (TRX-Bottle-350--dino).  The lookup collapses multiple dashes
to one for matching, then uses the canonical Billbee SKU for Subarticle 1 SKU etc.

Usage
-----
  python execution/sync_pipeline_to_upload.py --sheet-url URL
"""

import argparse
import re
import sys
from pathlib import Path

import gspread
from gspread.utils import rowcol_to_a1

sys.path.insert(0, str(Path(__file__).parent.parent))
from google_sheets_client import open_sheet

# Pipeline columns copied to upload (when non-empty in pipeline).
PIPELINE_FIELDS: list[str] = [
    "Custom Field Produktkategorie",
    "Custom Field Produktgröße",
    "Custom Field Produktvariante",
    "Custom Field Produktfarbe",
    "TARIC Code",
    "Country of origin",
    "Weight (g) net",
    "Weight (g) gross",
    "Price gross",
]

# Subarticle field order (used both for reading and writing).
SUB_FIELDS = ["Id", "SKU", "Name", "Amount"]

SKUS_TO_DELETE_TAB = "SKUStoDelete"


# ── helpers ──────────────────────────────────────────────────────────────────

def _norm(sku: str) -> str:
    """Collapse multiple dashes to one for fuzzy SKU matching."""
    return re.sub(r"-{2,}", "-", sku.strip())


def _write_chunked(ws, headers: list[str], data: list[list], chunk_size: int = 300) -> None:
    """Write header + data to ws in row-level chunks without clearing first."""
    n_cols = len(headers)
    last_col = rowcol_to_a1(1, n_cols)[:-1]   # e.g. "DS" from "DS1"

    ws.update(values=[headers], range_name="A1", value_input_option="RAW")

    total = (len(data) + chunk_size - 1) // chunk_size
    for b, i in enumerate(range(0, len(data), chunk_size), start=1):
        chunk = data[i:i + chunk_size]
        start = i + 2
        end   = start + len(chunk) - 1
        ws.update(
            values=chunk,
            range_name=f"A{start}:{last_col}{end}",
            value_input_option="RAW",
        )
        print(f"    chunk {b}/{total}: rows {start}–{end}")


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Sync pipeline changes into the upload tab.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--sheet-url", required=True)
    args = parser.parse_args()

    ss = open_sheet(args.sheet_url)
    print(f"Sheet: {ss.title}")

    # ── 1. Read pipeline tab ──────────────────────────────────────────────────
    print("\n[1/6] Reading 'pipeline' tab …")
    ws_pipeline = ss.worksheet("pipeline")
    p_raw = ws_pipeline.get_all_values()
    p_headers = p_raw[0]
    p_rows = [dict(zip(p_headers, r)) for r in p_raw[1:]]
    pipeline_skus: set[str] = {r["SKU"] for r in p_rows if r.get("SKU")}
    pipeline_by_sku: dict[str, dict] = {r["SKU"]: r for r in p_rows if r.get("SKU")}
    print(f"  {len(p_rows)} rows, {len(pipeline_skus)} unique SKUs.")

    # ── 2. Read upload tab ────────────────────────────────────────────────────
    print("\n[2/6] Reading 'ProductList' tab …")
    ws_upload = ss.worksheet("ProductList")
    u_raw = ws_upload.get_all_values()
    u_headers: list[str] = u_raw[0]
    u_data: list[list[str]] = u_raw[1:]
    print(f"  {len(u_data)} rows, {len(u_headers)} columns.")

    # Strip 'Action' column if a previous run left it in the upload tab
    if "Action" in u_headers:
        action_pos = u_headers.index("Action")
        u_headers = [h for h in u_headers if h != "Action"]
        u_data = [
            [cell for j, cell in enumerate(row) if j != action_pos]
            for row in u_data
        ]
        print("  Stripped existing 'Action' column (keeping upload clean).")

    sku_idx   = u_headers.index("SKU")
    id_idx    = u_headers.index("Id")
    title_idx = u_headers.index("Title DE")
    isbom_idx = u_headers.index("IsBom") if "IsBom" in u_headers else None

    # Build full upload lookup (for BOM resolution) — key: normalized SKU → row
    u_by_sku_exact: dict[str, list[str]] = {}
    u_by_sku_norm:  dict[str, list[str]] = {}
    for row in u_data:
        sku = row[sku_idx].strip() if len(row) > sku_idx else ""
        if sku:
            u_by_sku_exact[sku] = row
            u_by_sku_norm[_norm(sku)] = row

    def lookup_physical(bom_sku: str) -> list[str] | None:
        return u_by_sku_exact.get(bom_sku) or u_by_sku_norm.get(_norm(bom_sku))

    # ── 3. Find Subarticle columns ────────────────────────────────────────────
    # Map: n → {field: col_index}
    sub_col_map: dict[int, dict[str, int]] = {}
    sub_re = re.compile(r"Subarticle (\d+) (.+)")
    for i, h in enumerate(u_headers):
        m = sub_re.fullmatch(h)
        if m:
            n, field = int(m.group(1)), m.group(2)
            sub_col_map.setdefault(n, {})[field] = i
    max_existing_sub = max(sub_col_map) if sub_col_map else 0

    # Determine how many Subarticle slots we need
    max_bom_size = max(
        (len([s for s in r.get("BOM_SKUs", "").split("|") if s.strip()])
         for r in p_rows), default=0
    )
    print(f"  Max BOM size in pipeline: {max_bom_size}")
    print(f"  Subarticle slots in upload: {max_existing_sub}")

    # Add Subarticle N columns if needed
    new_headers = list(u_headers)
    if max_bom_size > max_existing_sub:
        for n in range(max_existing_sub + 1, max_bom_size + 1):
            for field in SUB_FIELDS:
                col_name = f"Subarticle {n} {field}"
                new_headers.append(col_name)
                sub_col_map.setdefault(n, {})[field] = len(new_headers) - 1
        print(f"  Added Subarticle {max_existing_sub + 1}–{max_bom_size} columns.")
    else:
        print(f"  Existing Subarticle slots sufficient.")

    n_cols = len(new_headers)

    # ── 4. Filter + update rows ────────────────────────────────────────────────
    print("\n[3/6] Filtering and applying pipeline changes …")
    kept = skipped = deleted_count = bom_warnings = 0
    new_data: list[list[str]] = []
    delete_rows_info: list[dict] = []

    for u_row in u_data:
        # Pad / truncate to new column width
        row = (list(u_row) + [""] * (n_cols - len(u_row)))[:n_cols]
        sku = row[sku_idx].strip()

        if sku not in pipeline_skus:
            skipped += 1
            continue

        pl = pipeline_by_sku[sku]

        # Rows marked for deletion go to SKUStoDelete, not upload
        if str(pl.get("Action", "")).strip().lower() == "delete":
            delete_rows_info.append({
                "SKU":      sku,
                "Id":       row[id_idx].strip()    if len(row) > id_idx    else "",
                "Type":     str(pl.get("Type", "")).strip(),
                "Title DE": row[title_idx].strip() if len(row) > title_idx else "",
            })
            deleted_count += 1
            continue

        # ── Transfer pipeline-managed fields ──
        for field in PIPELINE_FIELDS:
            pl_val = str(pl.get(field, "")).strip()
            if pl_val and field in new_headers:
                row[new_headers.index(field)] = pl_val

        # ── Handle Subarticle / BOM ──
        is_listing = (
            (isbom_idx is not None and row[isbom_idx].strip().upper() == "TRUE")
            or str(pl.get("IsBom", "")).strip().upper() == "TRUE"
            or str(pl.get("Type", "")).strip() == "2"
        )
        bom_skus_str = str(pl.get("BOM_SKUs", "")).strip()
        bom_skus = [s.strip() for s in bom_skus_str.split("|") if s.strip()] if bom_skus_str else []

        # Always clear all Subarticle columns first
        for n_slot, fields in sub_col_map.items():
            for col_idx in fields.values():
                if col_idx < n_cols:
                    row[col_idx] = ""

        if is_listing and bom_skus:
            for slot, bom_sku in enumerate(bom_skus, start=1):
                phys = lookup_physical(bom_sku)
                if phys is None:
                    print(f"  [warn] BOM SKU {bom_sku!r} not found — skipping (SKU={sku})")
                    bom_warnings += 1
                    continue

                phys_id   = phys[id_idx].strip()    if len(phys) > id_idx    else ""
                phys_sku  = phys[sku_idx].strip()   if len(phys) > sku_idx   else bom_sku
                phys_name = phys[title_idx].strip()  if len(phys) > title_idx else ""

                cols = sub_col_map.get(slot, {})
                if "Id"     in cols: row[cols["Id"]]     = phys_id
                if "SKU"    in cols: row[cols["SKU"]]    = phys_sku
                if "Name"   in cols: row[cols["Name"]]   = phys_name
                if "Amount" in cols: row[cols["Amount"]] = "1"

        new_data.append(row)
        kept += 1

    print(f"  Kept: {kept}  |  To delete: {deleted_count}  |  "
          f"Removed (not in pipeline): {skipped}  |  BOM warnings: {bom_warnings}")

    # ── 5. Resize + write upload tab ──────────────────────────────────────────
    print("\n[4/6] Resizing upload worksheet if needed …")
    needed_rows = len(new_data) + 1
    if ws_upload.row_count < needed_rows or ws_upload.col_count != n_cols:
        ws_upload.resize(rows=needed_rows + 10, cols=n_cols)
        print(f"  Resized to {needed_rows + 10} rows × {n_cols} cols.")

    print(f"\n[5/6] Writing {len(new_data)} rows × {n_cols} columns to 'ProductList' tab …")
    _write_chunked(ws_upload, new_headers, new_data)

    # Clear any leftover rows below our new data
    last_written_row = len(new_data) + 1
    if ws_upload.row_count > last_written_row + 1:
        ws_upload.batch_clear([f"A{last_written_row + 1}:{rowcol_to_a1(ws_upload.row_count, n_cols)}"])
        print(f"  Cleared leftover rows {last_written_row + 1}+.")

    # ── 6. Write SKUStoDelete tab ─────────────────────────────────────────────
    print(f"\n[6/6] Writing {len(delete_rows_info)} rows to '{SKUS_TO_DELETE_TAB}' tab …")

    # Listings first (delete BOM consumers before physicals)
    delete_rows_info.sort(key=lambda r: (0 if r["Type"] == "2" else 1, r["SKU"]))
    del_headers = ["SKU", "Id", "Type", "Title DE"]
    del_data = [[r["SKU"], r["Id"], r["Type"], r["Title DE"]] for r in delete_rows_info]

    try:
        ws_del = ss.worksheet(SKUS_TO_DELETE_TAB)
        ws_del.clear()
        if ws_del.col_count < len(del_headers):
            ws_del.resize(cols=len(del_headers))
    except gspread.exceptions.WorksheetNotFound:
        ws_del = ss.add_worksheet(
            title=SKUS_TO_DELETE_TAB,
            rows=max(len(del_data) + 5, 10),
            cols=len(del_headers),
        )

    ws_del.update(
        values=[del_headers] + del_data,
        range_name="A1",
        value_input_option="RAW",
    )
    print(f"  {len(del_data)} rows written (listings first).")

    print(f"\n[done]  upload tab:      {len(new_data)} rows, {n_cols} columns.")
    print(f"        {SKUS_TO_DELETE_TAB}: {len(del_data)} rows to delete.")
    if bom_warnings:
        print(f"[!] {bom_warnings} BOM SKU(s) could not be resolved — check warnings above.")
    if del_data:
        print(f"\n[info] To delete these products from Billbee via API:")
        print(f"       python execution/upload_to_billbee.py --sheet-url URL --delete --execute")


if __name__ == "__main__":
    main()
