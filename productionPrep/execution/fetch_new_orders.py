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


def load_fetch_config() -> dict:
    """
    Load order fetch settings from config.yaml (merged with platform override).
    Exits with an error if the base config file is not found.
    Returns a dict with keys:
        state_ids (list[int]), lookback_hours (int),
        criterion (str), tag_name (str).
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
    criterion = str(cfg.get("orderFetchCriterion") or "orderStatus").strip()
    tag_name = str(cfg.get("tagNameForOrderFetching") or "").strip()

    return {
        "state_ids": state_ids,
        "lookback_hours": lookback_hours,
        "criterion": criterion,
        "tag_name": tag_name,
    }


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


def _order_has_tag(order: dict, tag_name: str) -> bool:
    """Return True if the order has a tag matching tag_name (case-insensitive)."""
    tag_lower = tag_name.lower()
    for t in (order.get("Tags") or []):
        if isinstance(t, str) and t.lower() == tag_lower:
            return True
        if isinstance(t, dict) and t.get("Name", "").lower() == tag_lower:
            return True
    return False


def fetch_orders_since(
    client,
    since: str,
    state_ids: list[int] | None = None,
    criterion: str = "orderStatus",
    tag_name: str = "",
) -> list[dict]:
    """
    Fetch orders from Billbee created since `since` (ISO-8601 string).

    criterion controls which orders are returned:
      'orderStatus'  — orders in state_ids (or all states if state_ids is empty)
      'tagSet'       — all orders (any state) where tag_name is set
      'tagNotSet'    — all orders (any state) where tag_name is NOT set

    All orders matching the criteria are returned, including cancelled/deleted
    orders and orders with no shipping address. Callers are responsible for
    detecting and reporting those cases.
    """
    if criterion == "tagSet":
        mode_desc = f"tag '{tag_name}' is set"
    elif criterion == "tagNotSet":
        mode_desc = f"tag '{tag_name}' is not set"
    else:
        mode_desc = f"states: {state_ids if state_ids else 'all'}"

    print(f"[fetch] Fetching orders since {since} ({mode_desc}) ...")

    seen_ids: set = set()
    orders: list[dict] = []
    total_api = 0

    def _accept(order: dict) -> bool:
        """Return True if the order has not been seen yet (deduplication only)."""
        oid = order.get("BillBeeOrderId") or order.get("Id")
        if oid in seen_ids:
            return False
        seen_ids.add(oid)
        return True

    def _collect(state_id=None):
        nonlocal total_api
        for order in client.get_orders(
            order_state_id=state_id,
            min_date=since,
            page_size=250,
        ):
            total_api += 1
            if not _accept(order):
                continue
            orders.append(order)

    if criterion == "tagSet":
        if not tag_name:
            print("[fetch] WARNING: orderFetchCriterion is 'tagSet' but tagNameForOrderFetching is empty — fetching nothing.")
            return []
        # Fetch all orders, keep those with the tag
        _collect()
        tagged = [o for o in orders if _order_has_tag(o, tag_name)]
        print(f"[fetch] {total_api} orders from API, {len(tagged)} have tag '{tag_name}'")
        return tagged

    elif criterion == "tagNotSet":
        if not tag_name:
            print("[fetch] WARNING: orderFetchCriterion is 'tagNotSet' but tagNameForOrderFetching is empty — fetching nothing.")
            return []
        # Fetch all orders, keep those without the tag
        _collect()
        untagged = [o for o in orders if not _order_has_tag(o, tag_name)]
        print(f"[fetch] {total_api} orders from API, {len(untagged)} do not have tag '{tag_name}'")
        return untagged

    else:
        # Default: orderStatus
        if state_ids:
            for sid in state_ids:
                _collect(sid)
        else:
            _collect()
        print(f"[fetch] {total_api} orders from API, {len(orders)} fetched")
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
