"""
Upload a modified Google Sheet back to Billbee.

API limitation (confirmed Feb 2026)
------------------------------------
Billbee's public REST API only exposes PATCH /api/v1/products/{id} for product
updates.  PATCH supports ONLY these eight fields:

  SKU, ShortText, ShortDescription, EAN, Description,
  Manufacturer, Weight (gross), WeightNet

The following fields CANNOT be updated via the API — they require Billbee's
XLSX article import (Artikel → Importieren → Billbee XLSX) instead:

  CustomFields (Produktkategorie, Produktgröße, Produktvariante, Produktfarbe)
  BillOfMaterial (BOM_SKUs)
  TaricNumber, CountryOfOrigin, LengthCm, WidthCm, HeightCm, CostPrice, Price

Columns controlled by 'ColumnsToUpload' tab
--------------------------------------------
Only columns whose checkbox is TRUE are sent.  Non-patchable columns are
listed once at startup as a warning and then silently skipped per row.

Deletions (only with --delete flag)
-------------------------------------
Products listed in the 'SKUStoDelete' tab are removed from Billbee (listings
first, then physicals).  Verify the updates in the Billbee UI before running
--delete.  The SKUStoDelete tab is written by sync_pipeline_to_upload.py.

Safety model
------------
* --dry-run is the DEFAULT.  Pass --execute to make real API calls.
* On any HTTP error the script stops immediately (no silent partial uploads).

Usage
-----
  # Dry-run (default):
  python execution/upload_to_billbee.py --sheet-url URL

  # Execute PATCH updates (no deletions):
  python execution/upload_to_billbee.py --sheet-url URL --execute

  # Execute updates + delete Action=delete rows:
  python execution/upload_to_billbee.py --sheet-url URL --execute --delete

  # Test a single SKU first:
  python execution/upload_to_billbee.py --sheet-url URL --sku TRX-Backp-cord-crocodile --execute
"""

import argparse
import json
import sys
from pathlib import Path

import gspread

sys.path.insert(0, str(Path(__file__).parent.parent))

from billbee_client import BillbeeClient
from google_sheets_client import open_sheet, read_tab

TAB_NAME              = "ProductList"
COLUMNS_TO_UPLOAD_TAB = "ColumnsToUpload"
SKUS_TO_DELETE_TAB    = "SKUStoDelete"

# Default upload columns used when ColumnsToUpload tab is missing
_DEFAULT_UPLOAD_COLS = {
    "Custom Field Produktkategorie",
    "Custom Field Produktgröße",
    "Custom Field Produktvariante",
    "Custom Field Produktfarbe",
    "TARIC Code",
    "Country of origin",
    "BOM_SKUs",
}

# ── Patchable fields ────────────────────────────────────────────────────────
# Sheet column name → Billbee PATCH field name.
# Only these columns from the sheet can actually be sent to Billbee via PATCH.
_PATCHABLE_MAP: dict[str, str] = {
    "SKU":              "SKU",
    "EAN":              "EAN",
    "Manufacturer":     "Manufacturer",
    "Weight (g) net":   "WeightNet",      # sheet net weight → Billbee WeightNet
    "Weight (g) gross": "Weight",         # sheet gross weight → Billbee Weight
    "Short description DE": "ShortText",  # Billbee PATCH uses "ShortText"
    "Description":      "Description",
}

# These Billbee PATCH fields must be sent as numbers.
_NUMERIC_PATCHABLE = {"WeightNet", "Weight"}

# ── Non-patchable fields (for user-visible warning) ─────────────────────────
# Sheet columns that users commonly check but that the Billbee API cannot update.
_NOT_PATCHABLE_VIA_API: set[str] = {
    "Custom Field Produktkategorie", "Custom Field Produktgröße",
    "Custom Field Produktvariante", "Custom Field Produktfarbe",
    "TARIC Code", "Country of origin", "BOM_SKUs",
    "LengthCm", "WidthCm", "HeightCm",
    "CostPrice gross", "Price gross",
}


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _int_id(val) -> int | None:
    """Parse a Billbee product Id from a sheet cell (may be float-string or int)."""
    try:
        return int(float(str(val))) if str(val).strip() else None
    except (ValueError, TypeError):
        return None


def _is_listing(row: dict) -> bool:
    # Type column (pipeline/SKUStoDelete): "2" = listing
    type_val = str(row.get("Type") or "").strip()
    if type_val:
        try:
            return int(float(type_val)) == 2
        except (ValueError, TypeError):
            pass
    # IsBom column (Billbee XLSX / upload tab): TRUE = listing
    return str(row.get("IsBom") or "").strip().upper() == "TRUE"


def _read_columns_to_upload(spreadsheet) -> set[str]:
    """
    Read the ColumnsToUpload tab and return the set of column names whose
    checkbox is TRUE.  Returns an empty set if the tab does not exist.
    """
    try:
        ws = spreadsheet.worksheet(COLUMNS_TO_UPLOAD_TAB)
        data = ws.get_all_values()
        checked: set[str] = set()
        for row in data[1:]:   # skip header
            if row and len(row) >= 2:
                col_name = row[0].strip()
                is_checked = row[1].strip().upper() == "TRUE"
                if col_name and is_checked:
                    checked.add(col_name)
        return checked
    except gspread.exceptions.WorksheetNotFound:
        return set()


def _read_skus_to_delete(spreadsheet) -> list[dict]:
    """
    Read the SKUStoDelete tab and return rows as dicts.
    Returns an empty list if the tab does not exist or has no data rows.
    Written by sync_pipeline_to_upload.py; columns: SKU, Id, Type, Title DE.
    """
    try:
        ws = spreadsheet.worksheet(SKUS_TO_DELETE_TAB)
        data = ws.get_all_values()
        if len(data) < 2:
            return []
        headers = data[0]
        return [
            dict(zip(headers, row))
            for row in data[1:]
            if any(v.strip() for v in row)
        ]
    except gspread.exceptions.WorksheetNotFound:
        return []


def _build_patch_body(row: dict, patchable_cols: set[str]) -> dict:
    """
    Build the PATCH request body for one sheet row.
    Only columns in patchable_cols (intersection of upload_cols and _PATCHABLE_MAP)
    are included.  Empty / None values are skipped.
    """
    patch: dict = {}
    for sheet_col in patchable_cols:
        billbee_key = _PATCHABLE_MAP[sheet_col]
        val = row.get(sheet_col)
        if val is None or str(val).strip() in ("", "None"):
            continue
        if billbee_key in _NUMERIC_PATCHABLE:
            try:
                val = float(str(val))
            except (ValueError, TypeError):
                continue   # skip malformed numeric
        else:
            val = str(val).strip()
        patch[billbee_key] = val
    return patch


# ──────────────────────────────────────────────────────────────────────────────
# Phase 1 — Update (PATCH)
# ──────────────────────────────────────────────────────────────────────────────

def run_updates(
    update_rows: list[dict],
    upload_cols: set[str],
    execute: bool,
    debug: bool = False,
) -> None:
    """PATCH patchable fields for all non-deleted rows."""
    patchable_cols  = {c for c in upload_cols if c in _PATCHABLE_MAP}
    skipped_cols    = {c for c in upload_cols if c in _NOT_PATCHABLE_VIA_API}
    unknown_cols    = upload_cols - patchable_cols - skipped_cols - {"BOM_SKUs"}

    if skipped_cols:
        print("\n[!] The following checked columns CANNOT be updated via the Billbee PATCH API:")
        for col in sorted(skipped_cols):
            print(f"    - {col}")
        print("    → Use Billbee's XLSX article import (Artikel → Importieren) for these.\n")

    if unknown_cols:
        print(f"[?] Unknown columns (ignored): {sorted(unknown_cols)}\n")

    if not patchable_cols:
        print("[warn] None of the checked columns are patchable via the Billbee API.")
        print("       Nothing to PATCH.  Skipping update phase.\n")
        return

    print(f"Patchable columns to send: {sorted(patchable_cols)}\n")

    mode   = "EXECUTE" if execute else "DRY-RUN"
    client = BillbeeClient() if execute else None
    n      = len(update_rows)
    updated = skipped = empty = 0

    for i, row in enumerate(update_rows, 1):
        pid  = _int_id(row.get("Id"))
        sku  = str(row.get("SKU") or "").strip()
        prefix = f"[{i}/{n}]"

        if pid is None:
            print(f"{prefix} SKIP (no Id)  SKU={sku!r}")
            skipped += 1
            continue

        patch = _build_patch_body(row, patchable_cols)

        if not patch:
            # Nothing to send for this row (all patchable values empty)
            empty += 1
            continue

        weight_str = f"  WeightNet={patch.get('WeightNet', '')}  Weight={patch.get('Weight', '')}" if (
            "WeightNet" in patch or "Weight" in patch) else ""
        print(f"{prefix} [{mode}] SKU={sku!r:35s}  Id={pid}{weight_str}")

        if debug:
            print(f"   PATCH body: {json.dumps(patch, ensure_ascii=False)}")

        if execute:
            try:
                client.patch_product(pid, patch)
                updated += 1
            except Exception as e:
                print(f"\n!! HTTP ERROR on SKU={sku!r} Id={pid}: {e}")
                resp = getattr(e, "response", None)
                if resp is not None:
                    try:
                        print(f"   Billbee response: {resp.json()}")
                    except Exception:
                        print(f"   Billbee response: {resp.text[:500]}")
                print("Stopping upload to avoid partial state.")
                sys.exit(1)
        else:
            updated += 1

    print()
    print(f"[{mode}] Updated: {updated}  No patchable values: {empty}  Skipped (no Id): {skipped}")


# ──────────────────────────────────────────────────────────────────────────────
# Phase 2 — Delete
# ──────────────────────────────────────────────────────────────────────────────

def run_deletes(
    delete_rows: list[dict],
    execute: bool,
    yes_all: bool,
) -> None:
    """Delete Action=delete rows from Billbee (listings first, then physicals)."""
    mode   = "EXECUTE" if execute else "DRY-RUN"
    client = BillbeeClient() if execute else None

    # listings first (avoids orphaned BOM references)
    ordered = sorted(delete_rows, key=lambda r: (0 if _is_listing(r) else 1))

    deleted = skipped = 0

    for row in ordered:
        pid  = _int_id(row.get("Id"))
        sku  = str(row.get("SKU") or "").strip()
        typ  = "listing" if _is_listing(row) else "physical"

        print(f"[{mode}] DELETE {typ:8s}  SKU={sku!r:35s}  Id={pid}")

        if execute:
            if not yes_all:
                answer = input("  Really delete? [y/N] ").strip().lower()
                if answer != "y":
                    print("  Skipped.")
                    skipped += 1
                    continue
            try:
                client.delete_product(pid)
                deleted += 1
            except Exception as e:
                print(f"\n!! HTTP ERROR deleting SKU={sku!r} Id={pid}: {e}")
                print("Stopping to avoid partial state.")
                sys.exit(1)
        else:
            deleted += 1

    print()
    if execute:
        print(f"[EXECUTE] Deleted: {deleted}  Skipped by user: {skipped}")
    else:
        print(f"[DRY-RUN] Would delete: {deleted}")


# ──────────────────────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Upload a modified Google Sheet to Billbee via PATCH.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "NOTE: Only 8 Billbee fields are patchable via the API:\n"
            "  SKU, EAN, Manufacturer, Weight, WeightNet,\n"
            "  ShortText, ShortDescription, Description.\n"
            "Custom fields, BOM, TaricNumber etc. require Billbee's XLSX import."
        ),
    )
    parser.add_argument("--sheet-url", required=True,
                        help="URL of the Google Sheet with the 'downloaded' tab.")
    parser.add_argument("--execute", action="store_true",
                        help="Actually call the Billbee API.  Default is dry-run.")
    parser.add_argument("--delete", action="store_true",
                        help="Also process Action=delete rows (permanent deletions). "
                             "Only takes effect with --execute.")
    parser.add_argument("--yes-all", action="store_true",
                        help="Skip per-deletion confirmation prompts (with --delete --execute).")
    parser.add_argument("--sku",
                        help="Process only this one SKU (useful for a quick test run).")
    parser.add_argument("--debug", action="store_true",
                        help="Print the PATCH body as JSON before each API call.")
    args = parser.parse_args()

    # ── Load sheet ───────────────────────────────────────────────────────────
    print("Opening sheet …")
    spreadsheet = open_sheet(args.sheet_url)
    rows = read_tab(spreadsheet, TAB_NAME)
    print(f"  {len(rows)} rows loaded.")

    print("Reading ColumnsToUpload tab …")
    upload_cols = _read_columns_to_upload(spreadsheet)
    if not upload_cols:
        print(f"  [warn] '{COLUMNS_TO_UPLOAD_TAB}' tab not found or no columns checked.")
        print(f"         Using default set: {sorted(_DEFAULT_UPLOAD_COLS)}")
        upload_cols = _DEFAULT_UPLOAD_COLS.copy()
    print(f"  {len(upload_cols)} column(s) selected: {sorted(upload_cols)}\n")

    # ── Split rows ────────────────────────────────────────────────────────────
    # All upload rows are candidates for update (delete rows are in SKUStoDelete)
    update_rows = [r for r in rows if _int_id(r.get("Id"))]

    print(f"Reading '{SKUS_TO_DELETE_TAB}' tab …")
    delete_rows = _read_skus_to_delete(spreadsheet)
    if not delete_rows:
        print(f"  '{SKUS_TO_DELETE_TAB}' tab not found or empty — no deletions queued.")
    else:
        print(f"  {len(delete_rows)} row(s) to delete.")

    listing_updates  = sum(1 for r in update_rows if _is_listing(r))
    physical_updates = sum(1 for r in update_rows if not _is_listing(r))
    listing_deletes  = sum(1 for r in delete_rows if _is_listing(r))
    physical_deletes = sum(1 for r in delete_rows if not _is_listing(r))

    print(f"  Rows to update : {len(update_rows)}"
          f"  ({physical_updates} physical, {listing_updates} listing)")
    print(f"  Rows to delete : {len(delete_rows)}"
          f"  ({physical_deletes} physical, {listing_deletes} listing)")

    if not args.execute:
        print("\n[DRY-RUN MODE]  No API calls will be made.  Pass --execute to upload.\n")

    # ── SKU filter ────────────────────────────────────────────────────────────
    if args.sku:
        filtered = [r for r in update_rows if str(r.get("SKU") or "").strip() == args.sku]
        if not filtered:
            print(f"[error] SKU {args.sku!r} not found in the update rows.")
            sys.exit(1)
        print(f"[--sku] Filtering to 1 row: {args.sku!r}\n")
        update_rows = filtered

    # ── Phase 1 — Update ─────────────────────────────────────────────────────
    print("=" * 70)
    print("PHASE 1 — PATCH patchable fields")
    print("=" * 70)
    run_updates(update_rows, upload_cols, execute=args.execute, debug=args.debug)

    # ── Phase 2 — Delete (optional) ──────────────────────────────────────────
    if args.sku:
        pass   # skip delete phase in single-SKU test mode
    elif delete_rows:
        if args.delete:
            print()
            print("=" * 70)
            print("PHASE 2 — Deletions")
            print("=" * 70)
            run_deletes(delete_rows, execute=args.execute, yes_all=args.yes_all)
        else:
            print()
            print(f"[info] {len(delete_rows)} row(s) marked Action=delete were NOT deleted.")
            print("       Re-run with --delete (and --execute) to remove them from Billbee.")
            print("       Tip: verify the PATCH updates in the Billbee UI first.")

    # ── Footer ────────────────────────────────────────────────────────────────
    print()
    print("=" * 70)
    if args.execute:
        print("[done] Upload complete.")
    else:
        print("[done] Dry-run complete — no changes were made.")


if __name__ == "__main__":
    main()
