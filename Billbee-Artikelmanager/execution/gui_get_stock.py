#!/usr/bin/env python3
"""
Fetch live stock levels from Billbee for a list of products.

Strategy:
  ≤ 150 products : individual get_product_by_id calls  (~0.6 s each)
  > 150 products : page through full catalog (250/page), match by billbeeId
                   (~155 s total, but far faster than 150+ individual calls)

Output (JSONL to stdout, one record per line):
  {"type":"stock",    "sku":"...", "stock":N, "stockId":N}
  {"type":"progress", "scanned":N, "total":M, "found":F}
  {"type":"error",    "data":{"sku":"...", "error":"..."}}
  {"type":"done",     "total":N}

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

PAGING_THRESHOLD = 150


def _emit(obj: dict) -> None:
    print(json.dumps(obj, ensure_ascii=False), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--products", required=True,
                        help='JSON array of {sku, billbeeId}')
    args = parser.parse_args()

    products = json.loads(args.products)
    client = BillbeeClient()

    # Build lookup: billbeeId (int) → sku
    id_to_sku: dict[int, str] = {}
    for p in products:
        bid = p.get("billbeeId")
        sku = str(p.get("sku", "")).strip()
        if bid and sku:
            id_to_sku[int(bid)] = sku

    found = 0

    if len(products) <= PAGING_THRESHOLD:
        # ── Small list: individual calls ──────────────────────────────────
        total = len(products)
        for i, p in enumerate(products):
            sku = str(p.get("sku", "")).strip()
            billbee_id = p.get("billbeeId")
            if not sku:
                continue
            if not billbee_id:
                _emit({"type": "stock", "sku": sku, "stock": None, "stockId": 0})
            else:
                try:
                    product = client.get_product_by_id(int(billbee_id))
                    stock_list = product.get("Stocks") or []
                    stock_current = float(stock_list[0].get("StockCurrent", 0)) if stock_list else 0.0
                    stock_id      = int(stock_list[0].get("Id", 0))             if stock_list else 0
                    _emit({"type": "stock", "sku": sku, "stock": stock_current, "stockId": stock_id})
                    found += 1
                except Exception as exc:
                    _emit({"type": "error", "data": {"sku": sku, "error": str(exc)}})
            _emit({"type": "progress", "scanned": i + 1, "total": total, "found": found})

    else:
        # ── Large list: page through full catalog ─────────────────────────
        remaining = set(id_to_sku.keys())
        page = 1
        page_size = 250
        total_rows: int | None = None

        while remaining:
            try:
                data = client._get("/products", params={"page": page, "pageSize": page_size})
            except Exception as exc:
                _emit({"type": "error", "data": {"sku": "*", "error": str(exc)}})
                break

            items = data.get("Data", [])
            if total_rows is None:
                total_rows = data.get("Paging", {}).get("TotalRows", 0)

            for item in items:
                bid = item.get("Id")
                if bid in remaining:
                    sku = id_to_sku[bid]
                    stock_list = item.get("Stocks") or []
                    stock_current = float(stock_list[0].get("StockCurrent", 0)) if stock_list else 0.0
                    stock_id      = int(stock_list[0].get("Id", 0))             if stock_list else 0
                    _emit({"type": "stock", "sku": sku, "stock": stock_current, "stockId": stock_id})
                    remaining.discard(bid)
                    found += 1

            scanned = (page - 1) * page_size + len(items)
            _emit({"type": "progress", "scanned": scanned,
                   "total": total_rows or 0, "found": found})

            if not items or scanned >= (total_rows or 0):
                break
            page += 1

        for bid in remaining:
            sku = id_to_sku[bid]
            _emit({"type": "error", "data": {"sku": sku, "error": "not found in Billbee catalog"}})

    _emit({"type": "done", "total": found})


if __name__ == "__main__":
    main()
