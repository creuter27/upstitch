"""
Find duplicate physical products (same category/size/variant/color attributes)
in the 'downloaded' sheet tab, report them with BOM reference counts, and let
the user choose which one to keep.

Non-kept duplicates are marked with Action='delete' so the future upload script
can issue Billbee DELETE API calls for them.  Any BOM_SKUs references to the
deleted entries are updated to point to the surviving SKU in the same pass.

Usage:
  python execution/dedup_physical.py --sheet-url URL
  python execution/dedup_physical.py --sheet-url URL --dry-run
"""

import argparse
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from google_sheets_client import open_sheet, read_tab, write_tab
from execution.mappings_loader import Mappings
from execution.sku_parser import parse_sku

TAB_NAME  = "ProductList"
COL_BOM   = "BOM_SKUs"
COL_ACTION = "Action"


# ──────────────────────────────────────────────────────────────────────────────
# Core logic
# ──────────────────────────────────────────────────────────────────────────────

def find_duplicate_groups(rows: list[dict], mappings: Mappings) -> list[list[dict]]:
    """
    Group physical-format rows by their attribute tuple.
    Returns only groups with >= 2 members (the duplicates).

    Each entry in a group is a dict with the original row fields plus
    '_row_idx' (int) for later updates.
    """
    groups: dict[tuple, list[dict]] = defaultdict(list)

    for row_idx, row in enumerate(rows):
        sku = str(row.get("SKU") or "").strip()
        if not sku:
            continue
        if parse_sku(sku, mappings)["sku_format"] != "physical":
            continue

        key = (
            str(row.get("Custom Field Produktkategorie") or "").strip().lower(),
            str(row.get("Custom Field Produktgröße")     or "").strip().lower(),
            str(row.get("Custom Field Produktvariante")  or "").strip().lower(),
            str(row.get("Custom Field Produktfarbe")     or "").strip().lower(),
        )

        entry = dict(row)
        entry["_row_idx"] = row_idx
        groups[key].append(entry)

    return [g for g in groups.values() if len(g) > 1]


def find_bom_references(rows: list[dict], sku: str) -> list[str]:
    """Return listing SKUs whose BOM_SKUs cell contains this physical SKU."""
    refs = []
    for row in rows:
        bom_cell = str(row.get(COL_BOM) or "").strip()
        if not bom_cell:
            continue
        bom_skus = [s.strip() for s in bom_cell.split("|") if s.strip()]
        if sku in bom_skus:
            listing_sku = str(row.get("SKU") or "").strip()
            refs.append(listing_sku)
    return refs


def _default_keep_index(group: list[dict], rows: list[dict]) -> int:
    """
    Suggest which entry to keep: most BOM references, then shortest SKU,
    then alphabetically.  Returns 0-based index into group.
    """
    scored = []
    for i, entry in enumerate(group):
        sku = str(entry.get("SKU") or "").strip()
        ref_count = len(find_bom_references(rows, sku))
        scored.append((-ref_count, len(sku), sku, i))
    scored.sort()
    return scored[0][3]


# ──────────────────────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Find and mark duplicate physical products for deletion."
    )
    parser.add_argument("--sheet-url", required=True)
    parser.add_argument("--dry-run", action="store_true",
                        help="Report duplicates without writing changes.")
    parser.add_argument("--yes-all", action="store_true",
                        help="Auto-accept the suggested entry for every group without prompting.")
    args = parser.parse_args()

    mappings = Mappings()

    print("Opening sheet ...")
    spreadsheet = open_sheet(args.sheet_url)
    rows = read_tab(spreadsheet, TAB_NAME)
    print(f"      {len(rows)} rows loaded.\n")

    print("Finding duplicate physical products ...")
    groups = find_duplicate_groups(rows, mappings)

    if not groups:
        print("No duplicate physical products found.")
        return

    print(f"Found {len(groups)} group(s) with duplicate physical products.\n")

    # row_idx → kept_sku  (for BOM re-linking) / row_idx → "delete"
    mark_delete: dict[int, str]  = {}   # row_idx → kept SKU (for reference update)
    sku_replacements: dict[str, str] = {}  # old_sku → kept_sku

    for g_idx, group in enumerate(groups, 1):
        attrs = group[0]
        print(f"[{g_idx}/{len(groups)}] Duplicate group")
        print(
            f"  Attributes:"
            f"  category='{attrs.get('Custom Field Produktkategorie', '')}'"
            f"  size='{attrs.get('Custom Field Produktgröße', '')}'"
            f"  variant='{attrs.get('Custom Field Produktvariante', '')}'"
            f"  color='{attrs.get('Custom Field Produktfarbe', '')}'"
        )
        print()

        default_idx = _default_keep_index(group, rows)

        for i, entry in enumerate(group):
            sku = str(entry.get("SKU") or "").strip()
            refs = find_bom_references(rows, sku)
            marker = "  (suggested)" if i == default_idx else ""
            print(f"  [{i + 1}] {sku}   ({len(refs)} listing reference(s)){marker}")
            for ref in refs:
                print(f"        → {ref}")

        print()

        if args.dry_run:
            continue

        # ── Auto-approve or interactive ──────────────────────────────────────
        if args.yes_all:
            choice = default_idx + 1
            kept_sku = str(group[choice - 1].get("SKU") or "").strip()
            print(f"  Auto-keeping: {kept_sku}\n")
        else:
            while True:
                prompt = (
                    f"  Keep which? [1-{len(group)}]"
                    f" (default: {default_idx + 1}) / [s]kip: "
                )
                raw = input(prompt).strip().lower()

                if raw in ("s", "skip"):
                    print("  Skipped.\n")
                    break

                if raw == "":
                    choice = default_idx + 1
                    break

                try:
                    choice = int(raw)
                    if 1 <= choice <= len(group):
                        break
                    print(f"  Enter a number between 1 and {len(group)}.")
                except ValueError:
                    print(f"  Enter a number or 's' to skip.")

            if raw in ("s", "skip"):
                continue

            kept_entry = group[choice - 1]
            kept_sku   = str(kept_entry.get("SKU") or "").strip()
            print(f"  Keeping: {kept_sku}\n")

        for i, entry in enumerate(group):
            if i + 1 == choice:
                continue
            old_sku = str(entry.get("SKU") or "").strip()
            row_idx = entry["_row_idx"]
            mark_delete[row_idx] = kept_sku
            if old_sku and old_sku != kept_sku:
                sku_replacements[old_sku] = kept_sku

    if not mark_delete:
        print("No changes to apply.")
        return

    # ── Apply ─────────────────────────────────────────────────────────────────
    n_marked  = len(mark_delete)
    n_updated = 0

    rows_copy = [dict(r) for r in rows]

    # Ensure Action column is present on every row (survives as a sheet column)
    for row in rows_copy:
        if COL_ACTION not in row:
            row[COL_ACTION] = ""

    # Mark non-kept physical rows for deletion
    for row_idx in mark_delete:
        rows_copy[row_idx][COL_ACTION] = "delete"

    # Re-link BOM_SKUs references
    for row in rows_copy:
        bom_cell = str(row.get(COL_BOM) or "").strip()
        if not bom_cell:
            continue
        bom_skus = [s.strip() for s in bom_cell.split("|") if s.strip()]
        new_bom_skus = [sku_replacements.get(s, s) for s in bom_skus]
        if new_bom_skus != bom_skus:
            row[COL_BOM] = " | ".join(new_bom_skus)
            n_updated += 1

    print(
        f"Applying changes: "
        f"{n_marked} row(s) marked Action='delete', "
        f"{n_updated} BOM_SKUs cell(s) updated ..."
    )
    write_tab(spreadsheet, TAB_NAME, rows_copy)
    print(f"[done] Sheet URL: {spreadsheet.url}")


if __name__ == "__main__":
    main()
