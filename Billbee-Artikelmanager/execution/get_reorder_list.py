"""
Generate a reorder list from Billbee stock data.

For every product where both 'Stock min Standard' (StockWarning) and
'Stock target Standard' (StockDesired) are set: if current stock is below
target * factor, add to reorder list with qty = target * factor - current.

Writes results grouped by Manufacturer, ordered by Produktkategorie, to a new
tab named 'YY-MM-DD' in the Google Sheet 'Upstitch Reorders' (created if needed).

Usage:
  python execution/get_reorder_list.py
  python execution/get_reorder_list.py --factor 1.5
"""

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from billbee_client import BillbeeClient
from google_sheets_client import get_client, open_sheet_by_name, write_tab
import gspread


def _first_text(field_list) -> str:
    """Return the DE text from a Billbee multilingual list, falling back to the first entry."""
    if not field_list:
        return ""
    for entry in field_list:
        if entry.get("LanguageCode", "").lower() == "de":
            return entry.get("Text") or ""
    return (field_list[0].get("Text") or "") if field_list else ""


def _ask_factor() -> float:
    """Interactively ask for an optional reorder factor. Returns the factor (default 1.0)."""
    answer = input("Apply a reorder factor? (press Enter to skip, or enter a number like 1.5): ").strip()
    if not answer:
        return 1.0
    try:
        return float(answer)
    except ValueError:
        print(f"  Invalid number '{answer}', using 1.0.")
        return 1.0


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(
        description="Generate a reorder list from Billbee stock data.",
    )
    parser.add_argument("--factor", type=float, default=None,
                        help="Reorder factor (e.g. 1.5). If omitted, asked interactively.")
    args = parser.parse_args()

    if args.factor is not None:
        factor = args.factor
        print(f"Factor: {factor}")
    else:
        factor = _ask_factor()
        print(f"Using factor: {factor}")
    print()

    client = BillbeeClient()

    print("Fetching custom field definitions ...")
    field_defs = client.get_custom_field_definitions()  # {id: name}
    # Build reverse map: name -> id  (for lookup by name)
    kategorie_id = next(
        (fid for fid, name in field_defs.items() if "Produktkategorie" in name),
        None,
    )
    if kategorie_id is None:
        print("[warn] 'Produktkategorie' custom field not found — sorting will use empty string.")

    print("Fetching all products from Billbee (this may take a minute) ...")
    reorder_rows: list[dict] = []
    total = 0
    skipped = 0

    for product in client.get_all_products():
        total += 1
        stocks = product.get("Stocks") or []
        if not stocks:
            skipped += 1
            continue

        stock = stocks[0]
        stock_min    = stock.get("StockWarning")   # minimum
        stock_target = stock.get("StockDesired")   # target
        stock_current = stock.get("StockCurrent")

        # Skip if min or target not set
        if stock_min is None or stock_target is None:
            skipped += 1
            continue

        try:
            target_val   = float(stock_target)
            current_val  = float(stock_current) if stock_current is not None else 0.0
        except (TypeError, ValueError):
            skipped += 1
            continue

        threshold = target_val * factor
        if current_val >= threshold:
            continue  # enough stock

        qty = threshold - current_val

        # Resolve Produktkategorie from custom fields
        kategorie = ""
        if kategorie_id is not None:
            for cf in (product.get("CustomFields") or []):
                if cf.get("Definition", {}).get("Id") == kategorie_id:
                    kategorie = str(cf.get("Value") or "")
                    break

        reorder_rows.append({
            "Manufacturer":    product.get("Manufacturer") or "",
            "Produktkategorie": kategorie,
            "SKU":             product.get("SKU") or "",
            "Name":            _first_text(product.get("Title")),
            "Stock current":   round(current_val, 2),
            "Stock target":    round(target_val, 2),
            "Factor":          factor,
            "Reorder qty":     round(qty, 2),
        })

    print(f"  {total} products fetched, {skipped} skipped (no stock min/target set).")
    print(f"  {len(reorder_rows)} product(s) need reordering.")
    print()

    if not reorder_rows:
        print("Nothing to reorder. Done.")
        return

    # Sort: by Manufacturer, then by Produktkategorie
    reorder_rows.sort(key=lambda r: (r["Manufacturer"].lower(), r["Produktkategorie"].lower()))

    # Write to Google Sheet
    sheet_name = "Upstitch Reorders"
    tab_name   = date.today().strftime("%y-%m-%d")

    print(f"Opening / creating Google Sheet '{sheet_name}' ...")
    gc = get_client()
    try:
        ss = gc.open(sheet_name)
        print(f"  Found existing sheet.")
    except gspread.exceptions.SpreadsheetNotFound:
        ss = gc.create(sheet_name)
        print(f"  Created new sheet '{sheet_name}'.")

    print(f"Writing {len(reorder_rows)} rows to tab '{tab_name}' ...")
    write_tab(ss, tab_name, reorder_rows)

    # Build URL pointing directly to the new tab
    sheet_url = f"{ss.url}#gid={ss.worksheet(tab_name).id}"
    print()
    print(f"Done! {sheet_url}")

    import webbrowser
    webbrowser.open(sheet_url)


if __name__ == "__main__":
    main()
