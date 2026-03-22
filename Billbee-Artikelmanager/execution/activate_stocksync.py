"""
Activate StockSync for all sources of newly imported articles.

For each article in the 'new' tab (identified by Billbee Id), fetches the
current product from the Billbee API, sets StockSyncActive=True for every
source that has a SourceEntryId, and PATCHes the product back.

Run AFTER the XLSX has been imported into Billbee so the articles exist.

Usage:
  python execution/activate_stocksync.py --sheet-url URL
  python execution/activate_stocksync.py --sheet-url URL --dry-run
  python execution/activate_stocksync.py --sheet-url URL --tab new
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from billbee_client import BillbeeClient
from google_sheets_client import open_sheet, read_tab

_DEFAULT_TAB = "new"


def _int_id(val) -> int | None:
    try:
        return int(float(str(val))) if str(val).strip() else None
    except (ValueError, TypeError):
        return None


def main():
    parser = argparse.ArgumentParser(
        description="Activate StockSync for all sources of newly imported articles."
    )
    parser.add_argument("--sheet-url", required=True,
                        help="URL of the Google Sheet.")
    parser.add_argument("--tab", default=_DEFAULT_TAB,
                        help=f"Tab to read article IDs from (default: '{_DEFAULT_TAB}').")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show which articles would be updated without making API calls.")
    args = parser.parse_args()

    print("Opening sheet ...")
    ss = open_sheet(args.sheet_url)
    rows = read_tab(ss, args.tab)
    print(f"      {len(rows)} rows loaded from '{args.tab}' tab.")

    # Collect rows that have a Billbee Id
    articles: list[tuple[int, str]] = []
    for row in rows:
        bid = _int_id(row.get("Id"))
        if bid is not None:
            articles.append((bid, str(row.get("SKU") or "").strip()))

    if not articles:
        print("No rows with a Billbee Id found. Nothing to do.")
        return

    print(f"      {len(articles)} article(s) with Billbee Id.")
    print()

    if args.dry_run:
        print("[DRY-RUN] Would activate StockSync for the following articles:")
        for bid, sku in articles:
            print(f"  Id={bid}  SKU={sku}")
        return

    client = BillbeeClient()

    ok = 0
    already_ok = 0
    errors = 0

    for i, (bid, sku) in enumerate(articles, 1):
        print(f"[{i}/{len(articles)}] Id={bid}  SKU={sku}")

        try:
            product = client.get_product_by_id(bid)
        except Exception as e:
            print(f"    [ERROR] GET failed: {e}")
            errors += 1
            continue

        sources = product.get("Sources") or []
        if not sources:
            print(f"    [skip] No sources found on this product.")
            already_ok += 1
            continue

        for j, src in enumerate(sources, 1):
            print(f"    Source {j}: SourceEntryId={src.get('SourceEntryId')!r}  "
                  f"StockSyncActive={src.get('StockSyncActive')!r}  "
                  f"Source={src.get('Source')!r}")

        # Find sources that need updating: has a platform name but StockSyncActive is not True.
        # NOTE: SourceEntryId may be None for newly imported articles (no shop listing yet);
        #       Source (platform name, e.g. 'etsy_v3') is the correct indicator.
        to_activate = [
            src for src in sources
            if str(src.get("Source") or "").strip()
            and src.get("StockSyncActive") is not True
        ]

        if not to_activate:
            print(f"    [ok] StockSync already active on all {len(sources)} source(s).")
            already_ok += 1
            continue

        # Build updated sources list with StockSyncActive=True
        updated_sources = []
        for src in sources:
            if str(src.get("Source") or "").strip() and src.get("StockSyncActive") is not True:
                src = dict(src)
                src["StockSyncActive"] = True
            updated_sources.append(src)

        try:
            client.patch_product(bid, {"Sources": updated_sources})
            print(f"    [ok] StockSync activated on {len(to_activate)} source(s).")
            ok += 1
        except Exception as e:
            print(f"    [ERROR] PATCH failed: {e}")
            errors += 1

    print()
    print(f"Done: {ok} updated, {already_ok} already ok / no sources, {errors} error(s).")

    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
