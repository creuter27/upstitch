"""
Local cache of all Billbee products and custom field definitions.

Fetches via:
  - client.get_custom_field_definitions() → {def_id: field_name}
  - client.get_all_products()             → all product dicts

Cache file: .tmp/product_cache.json  (no TTL — caller decides when to refresh)

Usage:
    from execution.product_cache import load_or_refresh, cache_exists, cache_age_str
    by_id, by_sku, field_defs = load_or_refresh(client, force=False)
    product = by_id.get(12345)
    product = by_sku.get("TRX-BP-big-baer")
"""

import json
import time
from datetime import datetime, timezone
from pathlib import Path

CACHE_FILE = Path(__file__).parent.parent / ".tmp" / "product_cache.json"


def cache_exists() -> bool:
    return CACHE_FILE.exists()


def cache_age_str() -> str:
    """Human-readable age of the cache file, e.g. '2h 14m' or 'unknown'."""
    if not CACHE_FILE.exists():
        return "no cache"
    try:
        with open(CACHE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        cached_at = data.get("cached_at", 0)
        age_s = int(time.time() - cached_at)
        if age_s < 60:
            return f"{age_s}s"
        if age_s < 3600:
            return f"{age_s // 60}m"
        h, m = divmod(age_s // 60, 60)
        return f"{h}h {m}m" if m else f"{h}h"
    except Exception:
        return "unknown"


def _load_cache() -> tuple[dict, dict, dict]:
    with open(CACHE_FILE, encoding="utf-8") as f:
        data = json.load(f)
    by_id = {}
    by_sku = {}
    for p in data.get("products", []):
        pid = p.get("Id")
        sku = p.get("SKU") or ""
        if pid:
            by_id[int(pid)] = p
        if sku:
            by_sku[sku] = p
    field_defs = {int(k): v for k, v in data.get("field_defs", {}).items()}
    return by_id, by_sku, field_defs


def _save_cache(products: list[dict], field_defs: dict) -> None:
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(
            {
                "cached_at": time.time(),
                "cached_at_iso": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "field_defs": {str(k): v for k, v in field_defs.items()},
                "products": products,
            },
            f,
            ensure_ascii=False,
        )


def load_or_refresh(client, force: bool = False) -> tuple[dict, dict, dict]:
    """
    Load product cache from disk (force=False) or fetch fresh data from Billbee (force=True).

    Returns
    -------
    by_id : dict[int, product_dict]
    by_sku : dict[str, product_dict]
    field_defs : dict[int, str]   e.g. {42: "Produktkategorie", 43: "Produktgröße"}
    """
    if not force and CACHE_FILE.exists():
        print("[product_cache] Using cached product data")
        return _load_cache()

    print("[product_cache] Refreshing product cache from Billbee API ...")
    start = time.monotonic()

    # 1. Custom field definitions
    field_defs = client.get_custom_field_definitions()
    print(f"[product_cache]   {len(field_defs)} custom field definitions")

    # 2. All products
    products = []
    for p in client.get_all_products():
        products.append(p)
        if len(products) % 500 == 0:
            print(f"[product_cache]   {len(products)} products fetched so far ...")

    elapsed = time.monotonic() - start
    print(f"[product_cache]   {len(products)} products total ({elapsed:.1f}s)")

    _save_cache(products, field_defs)
    return _load_cache()
