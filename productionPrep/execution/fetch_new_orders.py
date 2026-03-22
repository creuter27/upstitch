"""
Fetch new orders from Billbee.

Order state and lookback settings are read from config/morning_fetch.yaml
in this project's root.

Config keys used:
  order_state_id  — int or list of ints (e.g. 3 or [3, 13])
  lookback_hours  — how far before last_run to look (default: 48)

Usage (standalone):
    python execution/fetch_new_orders.py --since 2026-01-01
"""

import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import yaml

DATA_DIR = Path(__file__).parent.parent / "data"
LAST_RUN_FILE = DATA_DIR / "last_run.json"

# Order states to always skip regardless of config (cancelled, deleted)
_ALWAYS_SKIP = {6, 8}


def load_fetch_config() -> dict:
    """
    Load order fetch settings from config.yaml (merged with platform override).
    Exits with an error if the base config file is not found.
    Returns a dict with keys: state_ids (list[int]), lookback_hours (int).
    """
    from execution.config_loader import load_config, _CONFIG_DIR
    if not (_CONFIG_DIR / "config.yaml").exists():
        print(
            f"\nERROR: Config file not found:\n"
            f"  {_CONFIG_DIR / 'config.yaml'}\n",
            file=sys.stderr,
        )
        sys.exit(1)

    cfg = load_config()

    raw = cfg.get("order_state_id")
    state_ids = [int(s) for s in (raw if isinstance(raw, list) else [raw])] if raw is not None else []
    lookback_hours = int(cfg.get("lookback_hours", 48))

    return {"state_ids": state_ids, "lookback_hours": lookback_hours}


def load_last_run() -> datetime | None:
    """Return the last run datetime (UTC) or None if no previous run."""
    if LAST_RUN_FILE.exists():
        with open(LAST_RUN_FILE, encoding="utf-8") as f:
            data = json.load(f)
        ts = data.get("last_run")
        if ts:
            return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return None


def save_last_run(dt: datetime | None = None) -> None:
    """Write the current UTC time (or given datetime) to last_run.json."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ts = (dt or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with open(LAST_RUN_FILE, "w", encoding="utf-8") as f:
        json.dump({"last_run": ts}, f)


def compute_since(lookback_hours: int, override: str | None = None) -> str:
    """
    Calculate the fetch start datetime.

    If override is given (e.g. from --since CLI arg), use that directly.
    Otherwise: last_run - lookback_hours. If no last run exists, use
    lookback_hours before now.
    """
    if override:
        return override

    last_run = load_last_run()
    if last_run:
        since_dt = last_run - timedelta(hours=lookback_hours)
    else:
        since_dt = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)

    return since_dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch_orders_since(client, since: str, state_ids: list[int] | None = None) -> list[dict]:
    """
    Fetch orders from Billbee created since `since` (ISO-8601 string).

    If state_ids is provided and non-empty, only orders in those states are
    returned (one API call per state, deduplicated by BillBeeOrderId).
    If state_ids is empty/None, all states except cancelled/deleted are returned.

    Orders with no ShippingAddress are skipped.
    """
    print(f"[fetch] Fetching orders since {since} "
          f"(states: {state_ids if state_ids else 'all'}) ...")

    seen_ids: set = set()
    orders: list[dict] = []
    total_api = 0

    def _collect(state_id=None):
        nonlocal total_api
        for order in client.get_orders(
            order_state_id=state_id,
            min_date=since,
            page_size=250,
        ):
            total_api += 1
            oid = order.get("BillBeeOrderId") or order.get("Id")
            if oid in seen_ids:
                continue
            seen_ids.add(oid)

            state = order.get("OrderStateId") or order.get("State") or 0
            if state in _ALWAYS_SKIP:
                continue

            addr = order.get("ShippingAddress") or {}
            if not any(addr.values()):
                continue

            orders.append(order)

    if state_ids:
        for sid in state_ids:
            _collect(sid)
    else:
        _collect()

    print(f"[fetch] {total_api} orders from API, {len(orders)} eligible")
    return orders


if __name__ == "__main__":
    import argparse
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from execution.billbee_client import BillbeeClient
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")

    parser = argparse.ArgumentParser(description="Fetch new orders from Billbee")
    parser.add_argument("--since", help="ISO date override, e.g. 2026-01-01")
    args = parser.parse_args()

    cfg = load_fetch_config()
    since = compute_since(cfg["lookback_hours"], override=args.since)
    client = BillbeeClient()
    orders = fetch_orders_since(client, since, state_ids=cfg["state_ids"])
    print(f"\nFetched {len(orders)} orders.")
