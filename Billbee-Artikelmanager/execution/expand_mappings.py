"""
Read the 'upload' tab of an existing Google Sheet, collect all unique values
from the attribute columns, and expand mappings/products.yaml with any values that
are not already represented as a canonical key.

New entries are added with an empty tokens list so the user can fill them in.

Usage:
  python execution/expand_mappings.py --sheet-url URL
"""

import argparse
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

from google_sheets_client import open_sheet, read_tab

MAPPINGS_FILE = Path(__file__).parent.parent / "mappings" / "products.yaml"
TAB_NAME = "ProductList"

# Sheet column → top-level section in products.yaml
# (sizes are handled separately since they're per-category)
COLUMN_TO_SECTION = {
    "Custom Field Produktkategorie": "categories",
    "Custom Field Produktvariante":  "variants",
    "Custom Field Produktfarbe":     "colors",
}
SIZE_COLUMN = "Custom Field Produktgröße"


def _load_yaml() -> dict:
    with open(MAPPINGS_FILE, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _save_yaml(data: dict) -> None:
    with open(MAPPINGS_FILE, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, sort_keys=False, default_flow_style=False)


def _existing_canonicals(section: dict) -> set[str]:
    """Return all canonical keys (lower-cased for comparison) from a section."""
    return {k.lower() for k in section.keys()}


def _existing_tokens(section: dict) -> set[str]:
    """Return all tokens (lower-cased) that already map into any canonical in the section."""
    tokens = set()
    for attrs in section.values():
        if isinstance(attrs, dict):
            for t in attrs.get("tokens", []):
                tokens.add(str(t).lower())
    return tokens


def collect_unique_values(rows: list[dict], column: str) -> set[str]:
    """
    Collect all unique non-empty cell values from a column.

    Values may be comma-separated (compound categories like "rucksack, flasche"),
    so each component is returned as a separate entry.
    """
    values: set[str] = set()
    for row in rows:
        raw = str(row.get(column) or "").strip()
        if not raw:
            continue
        for part in raw.split(","):
            part = part.strip()
            if part:
                values.add(part)
    return values


def expand_section(section: dict, new_values: set[str], section_name: str) -> tuple[list[str], list[str]]:
    """
    Add new entries (with tokens: []) to `section` for any value in `new_values`
    that is not already a canonical key and is not already covered by an existing token.

    Returns (added, skipped) lists for reporting.
    """
    canonicals = _existing_canonicals(section)
    tokens = _existing_tokens(section)
    added = []
    skipped = []

    for value in sorted(new_values):
        key = value.lower()
        if key in canonicals or key in tokens:
            skipped.append(value)
        else:
            # Add as a new canonical with no tokens yet
            section[value] = {"tokens": []}
            added.append(value)

    return added, skipped


def expand_sizes(sizes: dict, new_values: set[str]) -> tuple[list[str], list[str]]:
    """
    Sizes are stored per category. Collect all existing canonicals and tokens
    across all categories and add unknown values to a special '__unknown__' key
    so the user knows to assign them to the right category.
    """
    all_canonicals: set[str] = set()
    all_tokens: set[str] = set()
    for cat_sizes in sizes.values():
        for canonical, attrs in cat_sizes.items():
            all_canonicals.add(str(canonical).lower())
            if isinstance(attrs, dict):
                for t in attrs.get("tokens", []):
                    all_tokens.add(str(t).lower())

    added = []
    skipped = []

    for value in sorted(new_values):
        key = value.lower()
        if key in all_canonicals or key in all_tokens:
            skipped.append(value)
        else:
            # Append to an '__unknown__' pseudo-category so the user sees them
            if "__unknown__" not in sizes:
                sizes["__unknown__"] = {}
            if value not in sizes["__unknown__"]:
                sizes["__unknown__"][value] = {"tokens": []}
                added.append(value)

    return added, skipped


def main():
    parser = argparse.ArgumentParser(
        description="Expand mappings/products.yaml with unique values from the sheet."
    )
    parser.add_argument("--sheet-url", required=True, help="URL of the Google Sheet.")
    args = parser.parse_args()

    print(f"[1/4] Opening sheet ...")
    spreadsheet = open_sheet(args.sheet_url)
    print(f"      {spreadsheet.title}")

    print(f"[2/4] Reading '{TAB_NAME}' tab ...")
    rows = read_tab(spreadsheet, TAB_NAME)
    print(f"      {len(rows)} rows loaded.")

    if not rows:
        print("[warn] Tab is empty. Nothing to do.")
        sys.exit(0)

    print(f"[3/4] Collecting unique values ...")
    col_values: dict[str, set[str]] = {}
    for col in list(COLUMN_TO_SECTION.keys()) + [SIZE_COLUMN]:
        values = collect_unique_values(rows, col)
        col_values[col] = values
        print(f"      {col}: {len(values)} unique value(s): {sorted(values)}")

    print(f"[4/4] Expanding mappings/products.yaml ...")
    data = _load_yaml()

    total_added = 0

    for col, section_name in COLUMN_TO_SECTION.items():
        section = data.setdefault(section_name, {})
        added, skipped = expand_section(section, col_values[col], section_name)
        if added:
            print(f"      [{section_name}] Added: {added}")
        if skipped:
            print(f"      [{section_name}] Already covered (skipped): {skipped}")
        total_added += len(added)

    # Sizes
    sizes = data.setdefault("sizes", {})
    added_sizes, skipped_sizes = expand_sizes(sizes, col_values[SIZE_COLUMN])
    if added_sizes:
        print(f"      [sizes/__unknown__] Added (assign to category manually): {added_sizes}")
    if skipped_sizes:
        print(f"      [sizes] Already covered (skipped): {skipped_sizes}")
    total_added += len(added_sizes)

    if total_added:
        _save_yaml(data)
        print(f"\n[done] {total_added} new placeholder entries written to {MAPPINGS_FILE}")
        print(f"       Please edit {MAPPINGS_FILE} to fill in the 'tokens' lists.")
    else:
        print(f"\n[done] No new entries needed — all values already covered by existing mappings.")


if __name__ == "__main__":
    main()
