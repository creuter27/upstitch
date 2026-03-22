"""
Find duplicate listing rows (same SKU string appearing in more than one row),
display them with their Source (platform) info, and let the user choose which
to keep.

Decision rule for automatic suggestion (priority order):
  1. If exactly one entry has a non-empty Sources value → keep that one.
  2. Otherwise (all have sources, or none do) → keep the one with the lowest
     Billbee Id (the original import; duplicates get higher IDs on re-import).

Non-kept duplicates are marked with Action='delete'.  Because all duplicates
share the same SKU string, BOM_SKUs references in other rows do not need to
be updated — the surviving row carries the identical SKU.

NOTE: The 'Sources' column is populated by download_to_sheet.py.

Usage:
  python execution/dedup_listings.py --sheet-url URL
  python execution/dedup_listings.py --sheet-url URL --dry-run
  python execution/dedup_listings.py --sheet-url URL --yes-all
"""

import argparse
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from google_sheets_client import open_sheet, read_tab, write_tab
from execution.mappings_loader import Mappings
from execution.sku_parser import parse_sku

TAB_NAME   = "ProductList"
COL_ACTION = "Action"
COL_SOURCES = "Sources"


# ──────────────────────────────────────────────────────────────────────────────
# Core logic
# ──────────────────────────────────────────────────────────────────────────────

def find_duplicate_listing_groups(rows: list[dict], mappings: Mappings) -> list[list[dict]]:
    """
    Group listing-format rows by their exact SKU string.
    Returns only groups with >= 2 members.

    Each entry carries '_row_idx' (int).
    """
    groups: dict[str, list[dict]] = defaultdict(list)

    for row_idx, row in enumerate(rows):
        sku = str(row.get("SKU") or "").strip()
        if not sku:
            continue
        if parse_sku(sku, mappings)["sku_format"] != "listing":
            continue
        entry = dict(row)
        entry["_row_idx"] = row_idx
        groups[sku].append(entry)

    return [g for g in groups.values() if len(g) > 1]


def _sources(entry: dict) -> str:
    """Return the Sources cell value, stripped."""
    return str(entry.get(COL_SOURCES) or "").strip()


def _suggest_keep_index(group: list[dict]) -> int:
    """
    Return the 0-based index to suggest keeping.

    Priority:
      1. Exactly one entry has Sources → keep that one.
      2. Tie (all have sources / none do) → keep the entry with the lowest
         Billbee Id (the original import; re-imports get higher IDs).
    """
    with_sources = [i for i, e in enumerate(group) if _sources(e)]
    if len(with_sources) == 1:
        return with_sources[0]

    # Tie-break by lowest numeric Billbee Id
    def _id_sort_key(i: int) -> tuple:
        raw = str(group[i].get("Id") or "").strip()
        try:
            return (0, int(raw))
        except ValueError:
            return (1, raw)  # non-numeric IDs sort after numeric ones

    return min(range(len(group)), key=_id_sort_key)


# ──────────────────────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Find and mark duplicate listing rows for deletion."
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

    print("Finding duplicate listing SKUs ...")
    groups = find_duplicate_listing_groups(rows, mappings)

    if not groups:
        print("No duplicate listing SKUs found.")
        return

    print(f"Found {len(groups)} group(s) with duplicate listing SKUs.\n")

    mark_delete: list[int] = []  # row indices to mark

    for g_idx, group in enumerate(groups, 1):
        sku = str(group[0].get("SKU") or "").strip()
        suggested = _suggest_keep_index(group)

        print(f"[{g_idx}/{len(groups)}] {sku}")

        for i, entry in enumerate(group):
            billbee_id  = str(entry.get("Id") or "").strip()
            sources_val = _sources(entry)
            bom_cell    = str(entry.get("BOM_SKUs") or "").strip()
            action_cell = str(entry.get(COL_ACTION) or "").strip()

            marker = ""
            if suggested is not None and i == suggested:
                marker = "  (suggested: keep)"

            source_str = f"  source='{sources_val}'" if sources_val else "  source=(none)"
            bom_str    = f"  bom='{bom_cell}'"       if bom_cell    else "  bom=(none)"
            action_str = f"  action='{action_cell}'"  if action_cell else ""

            print(f"  [{i + 1}] id={billbee_id}{source_str}{bom_str}{action_str}{marker}")

        print()

        if args.dry_run:
            continue

        # ── Auto-approve or interactive ──────────────────────────────────────
        default_label = f" (default: {suggested + 1})"

        if args.yes_all:
            choice = suggested + 1
            kept_id = str(group[choice - 1].get("Id") or "").strip()
            print(f"  Auto-keeping id={kept_id} (suggestion accepted)\n")
        else:
            while True:
                prompt = f"  Keep which? [1-{len(group)}]{default_label} / [s]kip: "
                raw = input(prompt).strip().lower()

                if raw in ("s", "skip"):
                    print("  Skipped.\n")
                    break

                if raw == "":
                    choice = suggested + 1
                    break

                try:
                    choice = int(raw)
                    if 1 <= choice <= len(group):
                        break
                    print(f"  Enter a number between 1 and {len(group)}.")
                except ValueError:
                    print("  Enter a number or 's' to skip.")

            if raw in ("s", "skip"):
                continue

            kept_entry = group[choice - 1]
            kept_id    = str(kept_entry.get("Id") or "").strip()
            print(f"  Keeping id={kept_id}\n")

        for i, entry in enumerate(group):
            if i + 1 == choice:
                continue
            mark_delete.append(entry["_row_idx"])

    if not mark_delete:
        print("No changes to apply.")
        return

    rows_copy = [dict(r) for r in rows]

    # Ensure Action column is present on every row (survives as a sheet column)
    for row in rows_copy:
        if COL_ACTION not in row:
            row[COL_ACTION] = ""

    for row_idx in mark_delete:
        rows_copy[row_idx][COL_ACTION] = "delete"

    print(
        f"Applying changes: "
        f"{len(mark_delete)} listing row(s) marked Action='delete' ..."
    )
    write_tab(spreadsheet, TAB_NAME, rows_copy)
    print(f"[done] Sheet URL: {spreadsheet.url}")


if __name__ == "__main__":
    main()
