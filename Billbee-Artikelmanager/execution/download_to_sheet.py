"""
Download Billbee products → Google Sheet (tab: "downloaded").

Usage:
  python execution/download_to_sheet.py [--manufacturer MFR] [--category CAT]

Filtering:
  --manufacturer and --category are expanded via mappings/products.yaml so that all
  known tokens (abbreviations, synonyms, historical names) are checked across the
  SKU, native Manufacturer field, and Title of each product.

  Path A (filtered, fast): POST /api/v1/search for matching IDs, then fetch each
  product individually. Falls back to Path B when > MAX_INDIVIDUAL_FETCHES results.

  Path B (full download): paginate all ~10k products, filter locally.

Output:
  Prints the Google Sheet URL to stdout.
"""

import argparse
import sys
from datetime import date
from pathlib import Path

import gspread

sys.path.insert(0, str(Path(__file__).parent.parent))

from billbee_client import BillbeeClient
from google_sheets_client import create_sheet, open_sheet, write_tab
from execution.mappings_loader import Mappings

# If search returns more IDs than this, fall back to full download
MAX_INDIVIDUAL_FETCHES = 100

COLUMNS_TO_UPLOAD_TAB = "ColumnsToUpload"

# Columns pre-checked by default — the fields the pipeline actively manages.
_DEFAULT_CHECKED = {
    "Custom Field Produktkategorie",
    "Custom Field Produktgröße",
    "Custom Field Produktvariante",
    "Custom Field Produktfarbe",
    "TARIC Code",
    "Country of origin",
    "BOM_SKUs",
}


# ------------------------------------------------------------------
# Term expansion
# ------------------------------------------------------------------

def build_mfr_terms(query: str, mappings: Mappings) -> list[str]:
    """
    Expand a manufacturer query to all known tokens (case-insensitive).
    E.g. "TRX" → ["trx", "trixie", "trixie baby"]
    """
    terms = {query.lower()}
    # Direct lookup by canonical code (e.g. "TRX")
    canonical = query.upper()
    entry = mappings.manufacturers.get(canonical)
    if not entry:
        # Try lookup by token (e.g. "trixie" → "TRX")
        canonical = mappings.canonical_manufacturer(query) or ""
        entry = mappings.manufacturers.get(canonical)
    if entry:
        for token in entry.get("tokens", []):
            terms.add(token.lower())
        terms.add(canonical.lower())
    return sorted(terms)


def build_cat_terms(query: str, mappings: Mappings) -> list[str]:
    """
    Expand a category query to all known tokens (case-insensitive).
    E.g. "handtuch" → ["handtuch", "ht", "towel", "tow", "tw", "towl"]
    """
    terms = {query.lower()}
    entry = mappings.categories.get(query.lower())
    if not entry:
        canonical = mappings.canonical_category(query) or query.lower()
        entry = mappings.categories.get(canonical)
        terms.add(canonical.lower())
    if entry:
        for token in entry.get("tokens", []):
            terms.add(token.lower())
    return sorted(terms)


# ------------------------------------------------------------------
# Filtering
# ------------------------------------------------------------------

def _extract_title_text(product: dict) -> str:
    """Concatenate all language variants of Title into one searchable string."""
    title_list = product.get("Title") or []
    return " ".join((t.get("Text") or "") for t in title_list).lower()


def matches_filter(product: dict, mfr_terms: list[str] | None, cat_terms: list[str] | None) -> bool:
    """
    Return True if the product matches the given term lists.

    Manufacturer: checked against SKU, native Manufacturer field, and Title.
    Category: checked against SKU only (category tokens are SKU abbreviations).
    All matches are case-insensitive substring checks.
    """
    if mfr_terms:
        sku = str(product.get("SKU") or "").lower()
        mfr_native = str(product.get("Manufacturer") or "").lower()
        title = _extract_title_text(product)
        if not any(term in sku or term in mfr_native or term in title for term in mfr_terms):
            return False

    if cat_terms:
        sku = str(product.get("SKU") or "").lower()
        if not any(term in sku for term in cat_terms):
            return False

    return True


# ------------------------------------------------------------------
# Product flattening
# ------------------------------------------------------------------

def flatten_product(product: dict, field_defs: dict[int, str]) -> dict:
    """
    Flatten a raw Billbee product dict into a spreadsheet row (key→value).

    - Title/ShortDescription: extract DE text
    - BillOfMaterial: count + pipe-separated SKU list
    - Stocks: StockCurrent from first entry
    - CustomFields: expanded to named columns via field_defs
    - Images/Sources/InvoiceText: dropped (too noisy)
    """
    p = {}

    p["Id"] = product.get("Id", "")
    p["SKU"] = product.get("SKU", "")
    p["EAN"] = product.get("EAN", "")
    p["Manufacturer"] = product.get("Manufacturer", "")
    p["Type"] = product.get("Type", "")        # 1=physical, 2=listing/BOM
    p["IsDeactivated"] = product.get("IsDeactivated", "")
    p["IsDigital"] = product.get("IsDigital", "")

    def get_text(field_list):
        if not field_list:
            return ""
        for entry in field_list:
            if entry.get("LanguageCode") == "DE" and entry.get("Text"):
                return entry["Text"]
        return (field_list[0].get("Text") or "") if field_list else ""

    p["Title DE"] = get_text(product.get("Title", []))
    p["Short description DE"] = get_text(product.get("ShortDescription", []))
    p["Long description DE"] = get_text(product.get("Description", []))

    p["Price gross"] = product.get("Price", "")
    p["CostPrice gross"] = product.get("CostPrice", "")
    p["Price net"] = product.get("Net", "")         # API field "Net" = net price
    p["CostPrice net"] = product.get("CostPriceNet", "")
    p["VAT index"] = product.get("VatIndex", "")
    stocks = product.get("Stocks") or []
    p["Stock current Standard"] = stocks[0].get("StockCurrent", "") if stocks else ""
    p["Stock min Standard"]     = stocks[0].get("StockWarning",  "") if stocks else ""
    p["Stock target Standard"]  = stocks[0].get("StockDesired",  "") if stocks else ""
    p["Stock place Standard"]   = stocks[0].get("StockCode",     "") if stocks else ""

    p["IsBom"] = "TRUE" if int(product.get("Type") or 0) == 2 else "FALSE"
    bom = product.get("BillOfMaterial") or []
    p["BOM_Count"] = len(bom)
    p["BOM_SKUs"] = " | ".join(b.get("SKU", "") for b in bom)

    sources = product.get("Sources") or []
    p["Sources"] = ", ".join(s.get("Source", "") for s in sources if s.get("Source"))
    src1 = sources[0] if sources else {}
    p["Source 1 Shop Id"]         = src1.get("SourceEntryId", "")
    p["Source 1 Partner"]         = src1.get("Source", "")
    p["Source 1 Source Id"]       = src1.get("SourceId", "")
    p["Source 1 Stocksync active"] = src1.get("StockSyncActive", "")
    p["Source 1 Stock min"]       = src1.get("StockMin", "")
    p["Source 1 Stock Max"]       = src1.get("StockMax", "")
    p["Source 1 Units per item"]  = src1.get("UnitsPerItem", "")

    tags = product.get("Tags") or []
    p["Tags DE"] = ", ".join(t.get("Text", "") for t in tags if t.get("Text"))

    p["Materials DE"] = get_text(product.get("Materials", []))

    p["Category1"] = (product.get("Category1") or {}).get("Name", "")
    p["Category2"] = (product.get("Category2") or {}).get("Name", "")
    p["Category3"] = (product.get("Category3") or {}).get("Name", "")

    p["Condition"]     = product.get("Condition", "")
    p["Units per item"] = product.get("UnitsPerItem", "")
    p["Unit"]          = product.get("Unit", "")
    p["Delivery time"] = product.get("DeliveryTime", "")
    p["Shipping product"] = product.get("ShippingProductId", "")

    images = product.get("Images") or []
    images_sorted = sorted(images, key=lambda img: img.get("Position", 99))
    for n in range(1, 9):
        p[f"Image {n}"] = images_sorted[n - 1].get("Url", "") if n <= len(images_sorted) else ""

    custom_fields = product.get("CustomFields") or []
    for cf in custom_fields:
        field_id = cf.get("DefinitionId") or cf.get("Id")   # DefinitionId maps to field_defs
        name = field_defs.get(field_id, f"CustomField_{field_id}")
        p[f"Custom Field {name}"] = cf.get("Value", "")
    for name in field_defs.values():
        if f"Custom Field {name}" not in p:
            p[f"Custom Field {name}"] = ""

    p["Weight (g) net"]   = product.get("WeightNet", "") or product.get("Weight", "")
    p["Weight (g) gross"] = product.get("Weight", "")   # Billbee: Weight = gross weight
    p["LengthCm"]         = product.get("LengthCm", "")
    p["WidthCm"]          = product.get("WidthCm", "")
    p["HeightCm"]         = product.get("HeightCm", "")
    p["Country of origin"] = product.get("CountryOfOrigin", "")
    p["TARIC Code"]        = product.get("TaricNumber", "")

    p["Action"] = ""   # staging column: set to 'delete' by dedup scripts

    return p


# ------------------------------------------------------------------
# Download paths
# ------------------------------------------------------------------

def path_a_download(
    client: BillbeeClient,
    field_defs: dict,
    mfr_terms: list[str] | None,
    cat_terms: list[str] | None,
    search_query: str,
) -> list[dict] | None:
    """
    Path A: search by query → fetch full product records → exact local filter.
    Returns None to signal fallback to Path B.
    """
    print(f"[Path A] Searching for: '{search_query}' ...")
    raw_results = client.search_products(search_query, debug_dir=Path(".tmp"))

    if not raw_results:
        print("[Path A] No search results — falling back to Path B.")
        return None

    print(f"[Path A] Search returned {len(raw_results)} hit(s).")
    if len(raw_results) > MAX_INDIVIDUAL_FETCHES:
        print(f"[Path A] Too many hits ({len(raw_results)} > {MAX_INDIVIDUAL_FETCHES}) — falling back to Path B.")
        return None

    print(f"[Path A] Fetching {len(raw_results)} full product record(s) ...")
    products = []
    for i, hit in enumerate(raw_results, 1):
        full = client.get_product_by_id(hit["Id"])
        if matches_filter(full, mfr_terms, cat_terms):
            products.append(flatten_product(full, field_defs))
        if i % 10 == 0:
            print(f"  ... {i}/{len(raw_results)} fetched, {len(products)} match")

    print(f"[Path A] Done. {len(products)} product(s) after exact filter.")
    return products


def path_b_download(
    client: BillbeeClient,
    field_defs: dict,
    mfr_terms: list[str] | None,
    cat_terms: list[str] | None,
) -> list[dict]:
    """Path B: paginate full catalog, filter locally."""
    print("[Path B] Downloading full product catalog ...")
    results = []
    count = 0
    for raw in client.get_all_products():
        count += 1
        if matches_filter(raw, mfr_terms, cat_terms):
            results.append(flatten_product(raw, field_defs))
        if count % 500 == 0:
            print(f"  ... {count} processed, {len(results)} match")
    print(f"[Path B] Done. {count} total, {len(results)} match.")
    return results


# ------------------------------------------------------------------
# Entry point
# ------------------------------------------------------------------

def _update_columns_to_upload_tab(
    spreadsheet: gspread.Spreadsheet,
    all_columns: list[str],
) -> None:
    """
    Create or update the ColumnsToUpload tab.

    Column A: column name from the 'downloaded' tab.
    Column B: checkbox — whether this column should be uploaded to Billbee.

    Behaviour:
    - If the tab does not exist: create it with all columns; default-checked
      columns (see _DEFAULT_CHECKED) are pre-ticked.
    - If the tab exists: preserve existing checkbox values; append any new
      columns that appeared in the latest download (unchecked by default);
      rows for columns that no longer exist are left in place (harmless).
    """
    # Try to read existing tab
    existing: dict[str, bool] = {}   # col_name → checked
    try:
        ws = spreadsheet.worksheet(COLUMNS_TO_UPLOAD_TAB)
        data = ws.get_all_values()
        for row in data[1:]:   # skip header
            if row:
                col_name = row[0]
                checked  = (row[1].upper() == "TRUE") if len(row) > 1 else False
                existing[col_name] = checked
        ws_exists = True
    except gspread.exceptions.WorksheetNotFound:
        ws_exists = False

    # Merge: start from existing, add new columns (unchecked or default-checked)
    merged: list[tuple[str, bool]] = []
    seen: set[str] = set()

    # Keep existing rows in their original order
    for col_name, checked in existing.items():
        merged.append((col_name, checked))
        seen.add(col_name)

    # Append new columns from this download
    for col in all_columns:
        if col not in seen:
            default = col in _DEFAULT_CHECKED
            merged.append((col, default))
            seen.add(col)

    # Build the matrix: header + data rows
    matrix = [["Column", "Upload to Billbee"]] + [[name, val] for name, val in merged]
    n_data = len(merged)

    if ws_exists:
        ws.clear()
    else:
        ws = spreadsheet.add_worksheet(
            title=COLUMNS_TO_UPLOAD_TAB, rows=n_data + 5, cols=3
        )

    ws.update(matrix, value_input_option="RAW")

    # Apply checkbox data validation to column B (rows 2 onward)
    spreadsheet.batch_update({"requests": [
        # Bold header
        {
            "repeatCell": {
                "range": {
                    "sheetId": ws.id,
                    "startRowIndex": 0, "endRowIndex": 1,
                    "startColumnIndex": 0, "endColumnIndex": 2,
                },
                "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
                "fields": "userEnteredFormat(textFormat)",
            }
        },
        # Checkbox validation on column B data rows
        {
            "setDataValidation": {
                "range": {
                    "sheetId": ws.id,
                    "startRowIndex": 1,
                    "endRowIndex": 1 + n_data,
                    "startColumnIndex": 1,
                    "endColumnIndex": 2,
                },
                "rule": {
                    "condition": {"type": "BOOLEAN"},
                    "strict": True,
                    "showCustomUi": True,
                },
            }
        },
        # Column A width
        {
            "updateDimensionProperties": {
                "range": {
                    "sheetId": ws.id, "dimension": "COLUMNS",
                    "startIndex": 0, "endIndex": 1,
                },
                "properties": {"pixelSize": 220},
                "fields": "pixelSize",
            }
        },
        # Column B width
        {
            "updateDimensionProperties": {
                "range": {
                    "sheetId": ws.id, "dimension": "COLUMNS",
                    "startIndex": 1, "endIndex": 2,
                },
                "properties": {"pixelSize": 150},
                "fields": "pixelSize",
            }
        },
    ]})
    print(f"[ok] '{COLUMNS_TO_UPLOAD_TAB}' tab: {n_data} columns ({sum(v for _, v in merged)} pre-checked).")


def main():
    parser = argparse.ArgumentParser(description="Download Billbee products to a Google Sheet.")
    parser.add_argument("--manufacturer", help="Manufacturer code or name (e.g. TRX or trixie). Expanded via mappings.")
    parser.add_argument("--category", help="Category name or token (e.g. rucksack or bp). Expanded via mappings.")
    parser.add_argument("--sheet-url", help="URL of an existing Google Sheet to reuse (overwrites 'downloaded' tab). If omitted, a new sheet is created.")
    args = parser.parse_args()

    mappings = Mappings()
    client = BillbeeClient()

    print("[1/4] Fetching custom field definitions ...")
    field_defs = client.get_custom_field_definitions()
    print(f"      {len(field_defs)} field(s): {list(field_defs.values())}")

    # Expand filter terms
    mfr_terms = build_mfr_terms(args.manufacturer, mappings) if args.manufacturer else None
    cat_terms = build_cat_terms(args.category, mappings) if args.category else None

    if mfr_terms:
        print(f"      Manufacturer terms: {mfr_terms}")
    if cat_terms:
        print(f"      Category terms: {cat_terms}")

    print("[2/4] Downloading products ...")
    if mfr_terms or cat_terms:
        # Use the raw user query for the search endpoint (searches SKU + title text)
        search_query = " ".join(filter(None, [args.manufacturer, args.category]))
        products = path_a_download(client, field_defs, mfr_terms, cat_terms, search_query)
        if products is None:
            products = path_b_download(client, field_defs, mfr_terms, cat_terms)
    else:
        products = path_b_download(client, field_defs, None, None)

    if not products:
        print("[warn] No products found. Exiting.")
        sys.exit(0)

    if args.sheet_url:
        print(f"[3/4] Opening existing Google Sheet ...")
        spreadsheet = open_sheet(args.sheet_url)
    else:
        today = date.today().strftime("%Y-%m-%d")
        sheet_title = f"Billbee Artikelmanager {today}"
        print(f"[3/4] Creating Google Sheet: '{sheet_title}' ...")
        spreadsheet = create_sheet(sheet_title)

    print("[4/4] Writing to 'downloaded' tab ...")
    write_tab(spreadsheet, "downloaded", products)

    print(f"[4/4] Updating '{COLUMNS_TO_UPLOAD_TAB}' tab ...")
    _update_columns_to_upload_tab(spreadsheet, list(products[0].keys()))

    print(f"\n[done] Sheet URL: {spreadsheet.url}")


if __name__ == "__main__":
    main()
