"""
For rows whose SKU is in listing format but BOM_SKUs is empty, derive the
correct physical BOM item(s) from the listing SKU, look them up among the
physical products already in the sheet, and fill BOM_SKUs and BOM_Count.

Also ensures that every listing row (Subarticle 1 SKU filled or IsBom=TRUE)
has IsBom=TRUE and all Source N Stocksync active columns set to 1.  This
metadata pass runs even when all BOM data is already filled.

Usage:
  python execution/fill_bom_skus.py --sheet-url URL
  python execution/fill_bom_skus.py --sheet-url URL --dry-run
  python execution/fill_bom_skus.py --sheet-url URL --yes-all
"""

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from google_sheets_client import open_sheet, read_tab, write_tab
from execution.mappings_loader import Mappings
from execution.sku_parser import parse_sku, derive_listing_bom_items

_DEFAULT_TAB  = "ProductList"
COL_BOM_SKUS  = "BOM_SKUs"
COL_BOM_COUNT = "BOM_Count"
COL_TYPE      = "Type"


# ──────────────────────────────────────────────────────────────────────────────
# Physical product index
# ──────────────────────────────────────────────────────────────────────────────

def _is_physical(row: dict) -> bool:
    """Return True if the row represents a physical product (IsBom != TRUE)."""
    return str(row.get("IsBom") or "").strip().upper() != "TRUE"


def _stocksync_issues(row: dict) -> list[tuple[str, str]]:
    """
    Return (col_name, expected_value) for each Source N Stocksync active column
    whose current value differs from the expected value.

    Expected value:
      "1"  — when Source N Shop Id is non-empty (active source → sync on)
      ""   — when Source N Shop Id is empty     (no source → sync off / clear)
    """
    result = []
    for key in row:
        m = re.fullmatch(r"Source (\d+) Stocksync active", key)
        if not m:
            continue
        n        = m.group(1)
        shop_id  = str(row.get(f"Source {n} Shop Id") or "").strip()
        expected = "1" if shop_id else ""
        actual   = str(row.get(key) or "").strip()
        if actual != expected:
            result.append((key, expected))
    return result


def build_physical_index(rows: list[dict], mappings: Mappings) -> dict[tuple, list[str]]:
    """
    Index physical products by their canonical attribute tuple.
    Key:   (category, size, variant, color)  — all lowercase, "" when missing.
    Value: list of matching SKU strings (usually exactly one per key).
    """
    index: dict[tuple, list[str]] = {}
    for row in rows:
        if not _is_physical(row):
            continue
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
        index.setdefault(key, []).append(sku)
    return index


def lookup_physical_sku(
    category: str | None,
    size: str | None,
    variant: str | None,
    color: str | None,
    index: dict[tuple, list[str]],
) -> list[str]:
    key = (
        (category or "").lower(),
        (size     or "").lower(),
        (variant  or "").lower(),
        (color    or "").lower(),
    )
    return index.get(key, [])


# ──────────────────────────────────────────────────────────────────────────────
# Metadata fix (IsBom + Stocksync active)
# ──────────────────────────────────────────────────────────────────────────────

def find_metadata_issues(rows: list[dict]) -> list[dict]:
    """
    Detect rows where IsBom or Stocksync active is set incorrectly.

    Source of truth: Subarticle 1 SKU being filled means the row is a listing
    (IsBom should be TRUE and Stocksync active should be 1).

    Returns a list of issue dicts:
        row_idx          int
        sku              str
        isbom_actual     str    current IsBom value
        isbom_fix        bool   True if IsBom needs correcting
        stocksync_cols   list[str]  Stocksync columns whose value != "1"
    """
    issues = []
    for row_idx, row in enumerate(rows):
        has_sub = bool(str(row.get("Subarticle 1 SKU") or "").strip())
        if not has_sub:
            continue  # not a confirmed listing row — skip

        isbom_actual = str(row.get("IsBom") or "").strip().upper()
        isbom_fix    = isbom_actual != "TRUE"

        bad_stocksync = _stocksync_issues(row)  # list of (col, expected_value)

        if isbom_fix or bad_stocksync:
            issues.append({
                "row_idx":        row_idx,
                "sku":            str(row.get("SKU") or "").strip(),
                "isbom_actual":   isbom_actual or "(empty)",
                "isbom_fix":      isbom_fix,
                "stocksync_fixes": bad_stocksync,  # [(col, expected_value), ...]
            })

    return issues


def apply_metadata_fixes(rows: list[dict], issues: list[dict]) -> None:
    """
    Apply IsBom and Stocksync corrections for the given issue list.
    Modifies rows in-place.
    """
    for issue in issues:
        row = rows[issue["row_idx"]]
        if issue["isbom_fix"]:
            row["IsBom"] = "TRUE"
        for col, expected in issue["stocksync_fixes"]:
            row[col] = expected


# ──────────────────────────────────────────────────────────────────────────────
# Proposal computation
# ──────────────────────────────────────────────────────────────────────────────

def compute_proposals(rows: list[dict], mappings: Mappings, physical_rows: list[dict] | None = None) -> list[dict]:
    """
    For each listing-format row with an empty BOM_SKUs cell, derive the
    expected BOM items and look up the matching physical SKUs.

    Returns a list of proposal dicts:
        row_idx           int
        listing_sku       str
        bom_items         list[dict]   derived from listing SKU
        resolved_skus     list[str]    found physical SKUs (one per BOM item)
        unresolved_items  list[dict]   BOM items with no matching physical SKU
        can_fill          bool         True when all items were resolved
        proposed_bom_skus str          pipe-joined resolved SKUs
        proposed_bom_count int
        ambiguous         list[tuple]  (item_idx, [sku1, sku2, …]) when >1 match
    """
    physical_index = build_physical_index(
        physical_rows if physical_rows is not None else rows, mappings
    )
    proposals: list[dict] = []

    for row_idx, row in enumerate(rows):
        sku      = str(row.get("SKU")       or "").strip()
        bom_cell = str(row.get(COL_BOM_SKUS) or "").strip()

        # Skip rows that already have BOM data in either format
        sub1 = str(row.get("Subarticle 1 SKU") or "").strip()
        if bom_cell or sub1:
            continue  # already has BOM data — skip

        parsed = parse_sku(sku, mappings)
        if parsed["sku_format"] != "listing":
            continue  # not a listing-format SKU — skip

        bom_items = derive_listing_bom_items(sku, mappings)
        if not bom_items:
            continue

        resolved_skus:    list[str]   = []
        unresolved_items: list[dict]  = []
        ambiguous:        list[tuple] = []

        for i, item in enumerate(bom_items):
            matches = lookup_physical_sku(
                item["category"], item["size"], item["variant"], item["color"],
                physical_index,
            )
            if len(matches) == 1:
                resolved_skus.append(matches[0])
            elif len(matches) > 1:
                resolved_skus.append(matches[0])   # use first; flag as ambiguous
                ambiguous.append((i, matches))
            else:
                resolved_skus.append("")            # placeholder
                unresolved_items.append(item)

        can_fill = not unresolved_items

        proposals.append({
            "row_idx":           row_idx,
            "listing_sku":       sku,
            "bom_items":         bom_items,
            "resolved_skus":     resolved_skus,
            "unresolved_items":  unresolved_items,
            "ambiguous":         ambiguous,
            "can_fill":          can_fill,
            "proposed_bom_skus": " | ".join(s for s in resolved_skus if s),
            "proposed_bom_count": len([s for s in resolved_skus if s]),
        })

    return proposals


# ──────────────────────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Fill missing BOM_SKUs and fix listing row metadata (IsBom, Stocksync)."
    )
    parser.add_argument("--sheet-url", required=True)
    parser.add_argument("--dry-run",  action="store_true",
                        help="Show proposals without writing.")
    parser.add_argument("--yes-all",  action="store_true",
                        help="Approve all resolvable rows without prompting.")
    parser.add_argument("--tab", default=_DEFAULT_TAB,
                        help=f"Tab to read and write (default: '{_DEFAULT_TAB}').")
    parser.add_argument("--lookup-tab", default=None,
                        help="Tab to read physical SKUs from for BOM resolution "
                             "(default: same as --tab).")
    args = parser.parse_args()

    mappings = Mappings()

    print("Opening sheet ...")
    spreadsheet = open_sheet(args.sheet_url)
    rows = read_tab(spreadsheet, args.tab)
    print(f"      {len(rows)} rows loaded.")

    physical_rows: list[dict] | None = None
    lookup_tab = args.lookup_tab or args.tab
    if lookup_tab != args.tab:
        physical_rows = read_tab(spreadsheet, lookup_tab)
        print(f"      Physical index from '{lookup_tab}': {len(physical_rows)} rows.")
    print()

    print("Computing BOM proposals ...")
    proposals = compute_proposals(rows, mappings, physical_rows=physical_rows)
    meta_issues = find_metadata_issues(rows)

    can_fill    = [p for p in proposals if p["can_fill"]]
    cannot_fill = [p for p in proposals if not p["can_fill"]]

    if not proposals and not meta_issues:
        print("Nothing to do — all listing rows are complete and correct.")
        return

    if proposals:
        print(f"Found {len(proposals)} listing row(s) with empty BOM data:")
        print(f"  {len(can_fill)} fully resolvable")
        print(f"  {len(cannot_fill)} with at least one unresolved physical SKU")
    if meta_issues:
        print(f"  {len(meta_issues)} listing row(s) with incorrect metadata:")
        for issue in meta_issues:
            parts = []
            if issue["isbom_fix"]:
                parts.append(f"IsBom={issue['isbom_actual']} → TRUE")
            for col, expected in issue["stocksync_fixes"]:
                parts.append(f"{col} → {expected!r}")
            print(f"    {issue['sku']}: {', '.join(parts)}")
    print()

    # ── Report unresolvable rows ──────────────────────────────────────────────
    if cannot_fill:
        print("=== UNRESOLVABLE (no matching physical product in sheet) ===")
        for p in cannot_fill:
            print(f"  Listing: {p['listing_sku']}")
            for item in p["unresolved_items"]:
                print(
                    f"    missing: category={item['category']}"
                    f"  size={item['size']}"
                    f"  variant={item['variant']}"
                    f"  color={item['color']}"
                )
        print()

    # ── Dry run ───────────────────────────────────────────────────────────────
    if args.dry_run:
        if can_fill:
            print("=== PROPOSALS (dry run — no changes written) ===")
            for p in can_fill:
                print(f"  {p['listing_sku']}")
                print(f"    BOM_SKUs:  {p['proposed_bom_skus']}")
                print(f"    BOM_Count: {p['proposed_bom_count']}")
                if p["ambiguous"]:
                    for idx, matches in p["ambiguous"]:
                        print(f"    [warn] BOM item {idx+1} matched {len(matches)} physical SKUs: {matches}")
        return

    # ── Interactive / auto-approve ────────────────────────────────────────────
    proposal_map: dict[int, dict] = {}
    approved_row_indices: list[int] = []

    if can_fill:
        if args.yes_all:
            print("=== AUTO-APPROVING ALL RESOLVABLE ROWS ===")
            for p in can_fill:
                print(f"  {p['listing_sku']}")
                print(f"    BOM_SKUs: {p['proposed_bom_skus']}  (count={p['proposed_bom_count']})")
                if p["ambiguous"]:
                    for idx, matches in p["ambiguous"]:
                        print(f"    [warn] BOM item {idx+1}: used '{matches[0]}', other matches: {matches[1:]}")
                approved_row_indices.append(p["row_idx"])
                proposal_map[p["row_idx"]] = p
            print()
        else:
            for i, p in enumerate(can_fill, 1):
                print(f"[{i}/{len(can_fill)}] {p['listing_sku']}")
                print(f"         BOM_SKUs:  {p['proposed_bom_skus']}")
                print(f"         BOM_Count: {p['proposed_bom_count']}")
                if p["ambiguous"]:
                    for idx, matches in p["ambiguous"]:
                        print(f"         [warn] BOM item {idx+1} matched multiple: {matches}")
                print()

                while True:
                    raw = input("         Apply? [y]es / [n]o / [q]uit: ").strip().lower()
                    if raw in ("y", "yes", "n", "no", "q", "quit", ""):
                        break
                print()

                if raw in ("q", "quit"):
                    print("Aborted.")
                    break
                if raw in ("y", "yes"):
                    approved_row_indices.append(p["row_idx"])
                    proposal_map[p["row_idx"]] = p

    # ── Apply ─────────────────────────────────────────────────────────────────
    rows_copy = [dict(r) for r in rows]

    if approved_row_indices:
        print(f"Applying {len(approved_row_indices)} BOM update(s) ...")
        sku_to_row_data: dict[str, dict] = {
            str(r.get("SKU") or "").strip(): r
            for r in (physical_rows if physical_rows is not None else rows)
            if _is_physical(r) and str(r.get("SKU") or "").strip()
        }
        for row_idx in approved_row_indices:
            p = proposal_map[row_idx]
            rows_copy[row_idx]["IsBom"] = "TRUE"
            slot = 1
            for sku in p["resolved_skus"]:
                if not sku:
                    continue
                phys = sku_to_row_data.get(sku, {})
                rows_copy[row_idx][f"Subarticle {slot} SKU"]    = sku
                rows_copy[row_idx][f"Subarticle {slot} Id"]     = str(phys.get("Id") or "")
                rows_copy[row_idx][f"Subarticle {slot} Name"]   = str(phys.get("Title DE") or "")
                rows_copy[row_idx][f"Subarticle {slot} Amount"] = "1"
                slot += 1

    if meta_issues:
        apply_metadata_fixes(rows_copy, meta_issues)
        print(f"Fixed metadata on {len(meta_issues)} listing row(s).")

    if not approved_row_indices and not meta_issues:
        print("No changes applied.")
        return

    write_tab(spreadsheet, args.tab, rows_copy)
    print(f"[done] Sheet URL: {spreadsheet.url}")


if __name__ == "__main__":
    main()
