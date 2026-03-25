"""
Thin wrappers around the Billbee-Artikelmanager execution scripts.
Calls them as subprocesses so we stay decoupled from their import paths.

Product catalog is cached on disk at ~/.cache/upstitch/telegram-bot/products_<MFR>.json
so repeated lookups are instant. Call refresh_cache(manufacturer) to re-fetch from Billbee.
"""
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

log = logging.getLogger("billbee")

_REPO = Path(os.environ.get(
    "BILLBEE_ARTIKELMANAGER_PATH",
    Path(__file__).parent.parent / "Billbee-Artikelmanager",
))
_VENV_PYTHON = Path.home() / ".local" / "share" / "upstitch-venvs" / "Billbee-Artikelmanager" / "bin" / "python"
_PYTHON = str(_VENV_PYTHON) if _VENV_PYTHON.exists() else sys.executable

_CACHE_DIR = Path.home() / ".cache" / "upstitch" / "telegram-bot"


def _cache_path(manufacturer: str) -> Path:
    return _CACHE_DIR / f"products_{manufacturer.upper()}.json"


def _run(script: str, *args: str) -> dict:
    cmd = [_PYTHON, str(_REPO / "execution" / script), *args]
    env = os.environ.copy()
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(_REPO) + (":" + existing if existing else "")
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(_REPO), env=env)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"Script {script} failed")
    return json.loads(result.stdout)


def _load_cache(manufacturer: str) -> list[dict] | None:
    p = _cache_path(manufacturer)
    if p.exists():
        return json.loads(p.read_text())
    return None


def _save_cache(manufacturer: str, products: list[dict]) -> None:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _cache_path(manufacturer).write_text(json.dumps(products))


def refresh_cache(manufacturer: str) -> list[dict]:
    """Fetch all products from Billbee and save to disk cache. Returns the product list."""
    log.info("[%s] Fetching products from Billbee…", manufacturer)
    t0 = time.time()
    data = _run("gui_read_billbee_products.py", "--manufacturers", manufacturer)
    errors = data.get("errors") or []
    if errors:
        raise RuntimeError("; ".join(e.get("error", str(e)) for e in errors))
    products = data.get("products") or []
    _save_cache(manufacturer, products)
    elapsed = time.time() - t0
    log.info("[%s] Cache updated — %d products fetched in %.1fs", manufacturer, len(products), elapsed)
    return products


def _get_all_products(manufacturer: str) -> list[dict]:
    """Return cached products, fetching from Billbee if no cache exists yet."""
    cached = _load_cache(manufacturer)
    if cached is not None:
        return cached
    return refresh_cache(manufacturer)


def _match(product: dict, filters: dict) -> bool:
    # Searchable text: all attribute fields + sku + title (covers English names in SKU)
    searchable = " ".join(str(product.get(f) or "") for f in
                          ("category", "size", "variant", "color", "sku", "title")).lower()
    for key, val in filters.items():
        if not val:
            continue
        val_lower = val.lower()
        field_val = (product.get(key) or "").lower()
        # Match against the specific field first; fall back to full searchable text
        if val_lower not in field_val and val_lower not in searchable:
            return False
    return True


def get_live_stock(products: list[dict]) -> dict[str, float | None]:
    """
    Fetch current stock from Billbee for a list of products.
    Returns {sku: stock_float} — stock is None if Billbee returned an error for that SKU.
    """
    payload = json.dumps([{"sku": p["sku"], "billbeeId": p["billbeeId"]} for p in products])
    data = _run("gui_get_stock.py", "--products", payload)
    result = {}
    for sku, info in (data.get("stocks") or {}).items():
        result[sku] = info.get("stock")  # None on error
    return result


def find_products(manufacturer: str, **filters) -> list[dict]:
    """
    Search cached products matching manufacturer + optional attribute filters.
    filters: category, size, variant, color (all strings, case-insensitive substring match)
    Returns list of product dicts.
    """
    all_products = _get_all_products(manufacturer)
    if not filters:
        return all_products
    return [p for p in all_products if _match(p, filters)]


def update_stock(sku: str, billbee_id: int, delta: float | None = None,
                 new_quantity: float | None = None,
                 reason: str = "Telegram bot: manual correction") -> dict:
    """
    Update stock for a single product. Provide either delta or new_quantity.
    Returns {"ok": True, "sku": ..., "previousStock": ..., "newStock": ...}.
    """
    args = ["--sku", sku, "--billbee-id", str(billbee_id), "--reason", reason]
    if delta is not None:
        args += ["--delta", str(delta)]
    elif new_quantity is not None:
        args += ["--new-quantity", str(new_quantity)]
    else:
        raise ValueError("Provide delta or new_quantity")

    return _run("gui_update_stock.py", *args)
