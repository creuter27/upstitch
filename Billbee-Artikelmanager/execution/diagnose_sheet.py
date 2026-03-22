"""
Read the 'upload' tab and report rows where attribute columns contain
values that belong to the wrong column (e.g. a variant token in the size
column, or a size token in the variant column).

Usage:
  python execution/diagnose_sheet.py --sheet-url URL
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from google_sheets_client import open_sheet, read_tab
from execution.mappings_loader import Mappings

TAB_NAME = "ProductList"

COL_GROESSE  = "Custom Field Produktgröße"
COL_VARIANTE = "Custom Field Produktvariante"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sheet-url", required=True)
    args = parser.parse_args()

    mappings = Mappings()

    print("Opening sheet ...")
    spreadsheet = open_sheet(args.sheet_url)
    rows = read_tab(spreadsheet, TAB_NAME)
    print(f"{len(rows)} rows loaded.\n")

    # Collect all known size tokens (across all categories, flattened)
    all_size_tokens: set[str] = set()
    for cat_sizes in mappings.sizes.values():
        for canonical, attrs in cat_sizes.items():
            all_size_tokens.add(canonical.lower())
            for t in (attrs.get("tokens") or []):
                all_size_tokens.add(str(t).lower())

    # Known variant tokens (canonical + tokens)
    all_variant_tokens: set[str] = set()
    for canonical, attrs in mappings.variants.items():
        all_variant_tokens.add(canonical.lower())
        for t in (attrs.get("tokens") or []):
            all_variant_tokens.add(str(t).lower())

    problems: list[dict] = []

    for row in rows:
        sku       = str(row.get("SKU") or "").strip()
        bom_skus  = str(row.get("BOM_SKUs") or "").strip()
        groesse   = str(row.get(COL_GROESSE)  or "").strip()
        variante  = str(row.get(COL_VARIANTE) or "").strip()
        issues    = []

        # Variant column contains something that looks like a size token
        if variante.lower() in all_size_tokens:
            issues.append(f"Produktvariante='{variante}' looks like a size token")

        # Size column contains something that looks like a variant token
        if groesse.lower() in all_variant_tokens:
            issues.append(f"Produktgröße='{groesse}' looks like a variant token")

        if issues:
            problems.append({
                "SKU": sku,
                "BOM_SKUs": bom_skus,
                COL_GROESSE: groesse,
                COL_VARIANTE: variante,
                "issues": issues,
            })

    if not problems:
        print("No inconsistencies found.")
        return

    print(f"{'SKU':<40} {'BOM_SKUs':<50} {COL_GROESSE:<12} {COL_VARIANTE:<14}  Issue")
    print("-" * 140)
    for p in problems:
        bom_short = (p["BOM_SKUs"][:47] + "...") if len(p["BOM_SKUs"]) > 50 else p["BOM_SKUs"]
        for issue in p["issues"]:
            print(f"{p['SKU']:<40} {bom_short:<50} {p[COL_GROESSE]:<12} {p[COL_VARIANTE]:<14}  {issue}")

    print(f"\n{len(problems)} row(s) with inconsistencies.")


if __name__ == "__main__":
    main()
