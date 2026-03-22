"""
Assign TARIC codes and country of origin to all product rows in the Google Sheet.

Logic
-----
1. Filter to eligible rows (Produktkategorie non-empty, Action != 'delete').
   Both physical rows (no BOM_SKUs) and listing rows are included.
2. Group by (Manufacturer, Produktkategorie).
3. For each group, find the "winning" category (lowest valueInSet in
   mappings/products.yaml) and look up the TARIC code automatically.
4. Write TaricNumber and CountryOfOrigin to every row in the group.
5. Write a summary tab ('taric_summary') to the sheet for review.

IMPORTANT: The TARIC codes in the suggestion table are indicative.  Always
verify the final codes with a customs specialist or the official TARIC database
before submitting import/export declarations.

Usage:
  python execution/assign_taric.py --sheet-url URL
"""

import argparse
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

from google_sheets_client import open_sheet, read_tab, write_tab
from execution.mappings_loader import Mappings
from execution.specs_loader import SpecsLoader

MATERIALS_FILE  = Path(__file__).parent.parent / "mappings" / "materials.yaml"
SUMMARY_TAB     = "taric_summary"

_DEFAULT_TAB = "ProductList"
COL_TARIC  = "TARIC Code"
COL_COO    = "Country of origin"
COL_MFR    = "Manufacturer"
COL_CAT    = "Custom Field Produktkategorie"
COL_BOM    = "BOM_SKUs"
COL_ACTION = "Action"


# ──────────────────────────────────────────────────────────────────────────────
# Material normalisation
# ──────────────────────────────────────────────────────────────────────────────

def _load_material_map(path: Path) -> dict[str, str]:
    with open(path, encoding="utf-8") as f:
        data: dict[str, list[str]] = yaml.safe_load(f) or {}
    result: dict[str, str] = {}
    for key, synonyms in data.items():
        result[key.lower()] = key
        for syn in synonyms or []:
            result[str(syn).lower()] = key
    return result


_MATERIAL_MAP: dict[str, str] = _load_material_map(MATERIALS_FILE)


def _normalize_material(material: str) -> str:
    return _MATERIAL_MAP.get(material.lower().strip(), "other")


# ──────────────────────────────────────────────────────────────────────────────
# TARIC suggestion table
# (category_lower, material_key) → (code, description)
# IMPORTANT: indicative only — verify with customs specialist.
# ──────────────────────────────────────────────────────────────────────────────

_TARIC_TABLE: dict[tuple[str, str], tuple[str, str]] = {
    # Rucksack / Backpack
    ("rucksack",       "textile"): ("4202929100", "Rucksacks of man-made textile fibres"),
    ("rucksack",       "other"):   ("4202929900", "Rucksacks, other materials"),

    # Schulranzen / School satchel
    ("schulranzen",    "textile"): ("4202129900", "School satchels of textile materials"),
    ("schulranzen",    "other"):   ("4202129900", "School satchels, other"),

    # Trolley
    ("trolley",        "textile"): ("4202129900", "Trolleys / school bags on wheels"),
    ("trolley",        "other"):   ("4202129900", "Trolleys / school bags on wheels"),

    # Flasche / Bottle
    ("flasche",        "metal"):   ("7323930090", "Table/kitchen articles of stainless steel"),
    ("flasche",        "plastic"): ("3924100090", "Tableware and kitchenware of plastics"),
    ("flasche",        "other"):   ("7323930090", "Table/kitchen articles of stainless steel"),

    # Handtuch / Towel
    ("handtuch",       "textile"): ("6302609000", "Toilet/kitchen linen of other textile materials"),
    ("handtuch",       "other"):   ("6302609000", "Toilet/kitchen linen, other"),

    # Sportbeutel / Gym bag / Turnbeutel
    ("sportbeutel",    "textile"): ("6307900099", "Other made-up textile articles"),
    ("sportbeutel",    "other"):   ("6307900099", "Other made-up textile articles"),

    # Brotdose / Lunch box
    ("brotdose",       "plastic"): ("3924100090", "Tableware and kitchenware of plastics"),
    ("brotdose",       "metal"):   ("7323930090", "Kitchen articles of stainless steel"),
    ("brotdose",       "other"):   ("3924100090", "Tableware and kitchenware of plastics"),

    # Federmäppchen / Pencil case
    ("federmaepchen",  "textile"): ("4205009000", "Other articles of textile/leather"),
    ("federmaepchen",  "other"):   ("4205009000", "Other articles of leather/composition leather"),

    # Bus (Bustasche?)
    ("bus",            "other"):   ("4202929900", "Travel bags, other materials"),
    ("bus",            "textile"): ("4202929900", "Travel bags, other materials"),
    ("bus",            "wood"):    ("4202929900", "Travel bags, other materials"),

    # Motorikschleife / Activity toy
    ("motorikschleife", "wood"):   ("9503001000", "Toys of wood"),
    ("motorikschleife", "other"):  ("9503009900", "Toys, NES"),

    # Rassel / Rattle
    ("rassel",         "other"):   ("9503009900", "Toys, NES"),
    ("rassel",         "plastic"): ("9503009900", "Toys of plastic"),

    # Flugzeug / Toy plane
    ("flugzeug",       "wood"):    ("9503001000", "Toys of wood"),
    ("flugzeug",       "other"):   ("9503009900", "Toys, NES"),

    # Spielzeug / Toys (generic class used via taricCategory override)
    ("spielzeug",      "wood"):    ("9503001000", "Toys of wood"),
    ("spielzeug",      "plastic"): ("9503009900", "Toys, NES"),
    ("spielzeug",      "other"):   ("9503009900", "Toys, NES"),
}


def _lookup_taric(category: str, material: str) -> tuple[str, str] | None:
    key = (category.lower().strip(), material)
    if key in _TARIC_TABLE:
        return _TARIC_TABLE[key]
    fallback = (category.lower().strip(), "other")
    return _TARIC_TABLE.get(fallback)


# ──────────────────────────────────────────────────────────────────────────────
# Winning-category resolution
# ──────────────────────────────────────────────────────────────────────────────

def _winning_category(
    mfr: str,
    cat_str: str,
    mappings: Mappings,
    specs: SpecsLoader,
) -> tuple[str, str, str, str, str]:
    """
    Returns (winning_cat, taric_cat, material_raw, material_key, countryOfOrigin).

    material and countryOfOrigin are sourced from product_specs.yaml
    (per manufacturer × category) with fallback to products.yaml category defaults.
    taric_cat uses the optional 'taricCategory' override from products.yaml.
    """
    cats = [c.strip() for c in cat_str.split(",") if c.strip()]
    if not cats:
        return ("", "", "other", "other", "")

    best_cat = cats[0]
    best_value = float("inf")
    for cat in cats:
        entry = mappings.categories.get(cat.lower(), {})
        value = entry.get("valueInSet", 999)
        if value < best_value:
            best_value = value
            best_cat = cat

    entry = mappings.categories.get(best_cat.lower(), {})
    taric_cat = entry.get("taricCategory", best_cat).lower()

    # Look up material and countryOfOrigin from product_specs.yaml first,
    # fall back to the category defaults in products.yaml.
    spec = specs.lookup(mfr, best_cat)
    material_raw = spec.get("material") or entry.get("material", "other")
    material_key = _normalize_material(material_raw)
    coo          = spec.get("countryOfOrigin") or entry.get("countryOfOrigin", "")

    return (best_cat, taric_cat, material_raw, material_key, coo)


# ──────────────────────────────────────────────────────────────────────────────
# Grouping helpers
# ──────────────────────────────────────────────────────────────────────────────

def _group_key(row: dict) -> tuple[str, str]:
    return (
        str(row.get(COL_MFR) or "").strip(),
        str(row.get(COL_CAT) or "").strip(),
    )


def _is_eligible_row(row: dict) -> bool:
    """Any non-deleted row with a Produktkategorie — physical or listing."""
    cat    = str(row.get(COL_CAT)    or "").strip()
    action = str(row.get(COL_ACTION) or "").strip().lower()
    return bool(cat) and action != "delete"


# ──────────────────────────────────────────────────────────────────────────────
# Summary tab
# ──────────────────────────────────────────────────────────────────────────────

_BLUE_BG = {"red": 0.24, "green": 0.52, "blue": 0.78}
_BLUE_FG = {"red": 1.0,  "green": 1.0,  "blue": 1.0}
_RED_BG  = {"red": 1.0,  "green": 0.87, "blue": 0.87}

SUMMARY_COLUMNS = [
    "Manufacturer",
    "Custom Field Produktkategorie",
    "Winning Category",
    "TARIC Class",
    "Material",
    "TARIC Code",
    "TARIC Description",
    "Country of origin",
    "Rows Affected",
    "Status",
]


def _write_summary_tab(spreadsheet, summary_rows: list[dict]) -> None:
    """Write the taric_summary tab with formatting."""
    import gspread

    matrix = [SUMMARY_COLUMNS] + [
        [str(r.get(c, "")) for c in SUMMARY_COLUMNS]
        for r in summary_rows
    ]
    n_data = len(summary_rows)

    try:
        ws = spreadsheet.worksheet(SUMMARY_TAB)
        ws.clear()
    except gspread.exceptions.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title=SUMMARY_TAB, rows=n_data + 5, cols=len(SUMMARY_COLUMNS) + 1)

    ws.update(matrix, value_input_option="RAW")

    col_widths = [160, 220, 150, 130, 120, 120, 340, 130, 90, 80]

    # Find "NO MATCH" rows (0-based data row indices, +1 for header)
    no_match_rows = [
        i + 1 for i, r in enumerate(summary_rows)
        if r.get("Status") == "NO MATCH"
    ]

    requests = [
        # Bold blue header
        {
            "repeatCell": {
                "range": {
                    "sheetId": ws.id,
                    "startRowIndex": 0, "endRowIndex": 1,
                    "startColumnIndex": 0, "endColumnIndex": len(SUMMARY_COLUMNS),
                },
                "cell": {
                    "userEnteredFormat": {
                        "backgroundColor": _BLUE_BG,
                        "textFormat": {"bold": True, "foregroundColor": _BLUE_FG},
                    }
                },
                "fields": "userEnteredFormat(backgroundColor,textFormat)",
            }
        },
        # Freeze header row
        {
            "updateSheetProperties": {
                "properties": {"sheetId": ws.id, "gridProperties": {"frozenRowCount": 1}},
                "fields": "gridProperties.frozenRowCount",
            }
        },
    ]

    # Red background on NO MATCH rows
    for row_idx in no_match_rows:
        requests.append({
            "repeatCell": {
                "range": {
                    "sheetId": ws.id,
                    "startRowIndex": row_idx, "endRowIndex": row_idx + 1,
                    "startColumnIndex": 0, "endColumnIndex": len(SUMMARY_COLUMNS),
                },
                "cell": {"userEnteredFormat": {"backgroundColor": _RED_BG}},
                "fields": "userEnteredFormat(backgroundColor)",
            }
        })

    # Column widths
    for col_idx, width in enumerate(col_widths):
        requests.append({
            "updateDimensionProperties": {
                "range": {
                    "sheetId": ws.id, "dimension": "COLUMNS",
                    "startIndex": col_idx, "endIndex": col_idx + 1,
                },
                "properties": {"pixelSize": width},
                "fields": "pixelSize",
            }
        })

    spreadsheet.batch_update({"requests": requests})
    no_match_count = len(no_match_rows)
    print(f"[ok] '{SUMMARY_TAB}' tab: {n_data} groups"
          + (f"  ({no_match_count} NO MATCH — highlighted in red)" if no_match_count else ""))


# ──────────────────────────────────────────────────────────────────────────────
# Main logic
# ──────────────────────────────────────────────────────────────────────────────

def assign_taric(
    rows: list[dict],
    mappings: Mappings,
    specs: SpecsLoader | None = None,
) -> tuple[list[dict], int, list[dict]]:
    """
    Auto-assign TARIC codes and CoO from mappings.
    Returns (updated_rows, n_updated, summary_rows).
    """
    eligible_rows = [r for r in rows if _is_eligible_row(r)]
    print(f"Eligible rows to analyse: {len(eligible_rows)} "
          f"({sum(1 for r in eligible_rows if str(r.get(COL_BOM) or '').strip())} listings, "
          f"{sum(1 for r in eligible_rows if not str(r.get(COL_BOM) or '').strip())} physical)")

    groups: dict[tuple[str, str], list[int]] = {}
    for i, row in enumerate(rows):
        if not _is_eligible_row(row):
            continue
        groups.setdefault(_group_key(row), []).append(i)

    print(f"Groups (manufacturer × category): {len(groups)}\n")

    assignments: dict[tuple[str, str], tuple[str, str]] = {}
    summary_rows: list[dict] = []

    _specs = specs or SpecsLoader(mappings=mappings)

    for key, indices in sorted(groups.items()):
        mfr, cat_str = key
        winning_cat, taric_cat, material_raw, material_key, coo = _winning_category(
            mfr, cat_str, mappings, _specs
        )
        result = _lookup_taric(taric_cat, material_key) if taric_cat else None

        taric_code = result[0] if result else ""
        taric_desc = result[1] if result else ""
        status     = "OK" if result else "NO MATCH"

        mat_display = (material_raw if material_raw == material_key
                       else f"{material_raw} → {material_key}")
        cat_display = (f"{winning_cat} → {taric_cat}"
                       if taric_cat != winning_cat.lower() else winning_cat)

        print(f"  {mfr or '(no mfr)':15s}  {cat_display:35s}  "
              f"mat={mat_display!r:25s}  [{taric_code}]  {status}")

        assignments[key] = (taric_code, coo)
        summary_rows.append({
            "Manufacturer":                    mfr,
            "Custom Field Produktkategorie":   cat_str,
            "Winning Category":                cat_display,
            "TARIC Class":                     taric_cat if taric_cat != winning_cat.lower() else "",
            "Material":                        mat_display,
            "TARIC Code":                      taric_code,
            "TARIC Description":               taric_desc,
            "Country of origin":               coo,
            "Rows Affected":                   len(indices),
            "Status":                          status,
        })

    # Apply to rows
    n_updated = 0
    for i, row in enumerate(rows):
        if not _is_eligible_row(row):
            continue
        key = _group_key(row)
        taric_code, country = assignments.get(key, ("", ""))
        rows[i] = {**row, COL_TARIC: taric_code, COL_COO: country}
        n_updated += 1

    return rows, n_updated, summary_rows


def main():
    parser = argparse.ArgumentParser(
        description="Assign TARIC codes and country of origin to listing products in the Google Sheet."
    )
    parser.add_argument("--sheet-url", required=True, help="URL of the Google Sheet.")
    parser.add_argument("--tab", default=_DEFAULT_TAB,
                        help=f"Tab to read and write (default: '{_DEFAULT_TAB}').")
    args = parser.parse_args()

    mappings = Mappings()
    specs    = SpecsLoader(mappings=mappings)

    print("Opening sheet …")
    spreadsheet = open_sheet(args.sheet_url)
    rows = read_tab(spreadsheet, args.tab)
    print(f"  {len(rows)} rows loaded.\n")

    rows, n_updated, summary_rows = assign_taric(rows, mappings=mappings, specs=specs)

    print(f"\nWriting {n_updated} updated rows (TaricNumber + CountryOfOrigin) …")
    print("  (Both physical and listing rows are updated.)")
    write_tab(spreadsheet, args.tab, rows)

    print(f"Writing summary tab …")
    _write_summary_tab(spreadsheet, summary_rows)

    print("[done]")


if __name__ == "__main__":
    main()
