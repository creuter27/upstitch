#!/usr/bin/env python3
"""
Fetch live stock levels from Billbee for a list of products.

Input : --products JSON array of {sku, billbeeId} objects
Output: JSON object {"stocks": {sku: {stock, stockId}}, "errors": [...]} to stdout.

Usage:
  python execution/gui_get_stock.py \
      --products '[{"sku":"TRX-bp-s-bear","billbeeId":12345},...]'
"""
import argparse
import json
import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
_repo = os.path.dirname(os.path.dirname(_here))
sys.path.insert(0, _here)
sys.path.insert(0, os.path.join(_repo, "billbee-python-client"))

from billbee_client import BillbeeClient  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--products", required=True,
                        help='JSON array of {sku, billbeeId}')
    args = parser.parse_args()

    products = json.loads(args.products)
    client = BillbeeClient()

    stocks: dict[str, dict] = {}
    errors: list[dict] = []

    for p in products:
        sku = str(p.get("sku", "")).strip()
        billbee_id = p.get("billbeeId")

        if not sku:
            continue
        if not billbee_id:
            stocks[sku] = {"stock": None, "stockId": 0, "error": "missing billbeeId"}
            continue

        try:
            product = client.get_product_by_id(int(billbee_id))
            stock_list = product.get("Stocks") or []
            stock_current = float(stock_list[0].get("StockCurrent", 0)) if stock_list else 0.0
            stock_id      = int(stock_list[0].get("Id", 0))             if stock_list else 0
            stocks[sku] = {"stock": stock_current, "stockId": stock_id}
        except Exception as exc:
            stocks[sku] = {"stock": None, "stockId": 0, "error": str(exc)}
            errors.append({"sku": sku, "error": str(exc)})

    print(json.dumps({"stocks": stocks, "errors": errors}, ensure_ascii=False))


if __name__ == "__main__":
    main()
