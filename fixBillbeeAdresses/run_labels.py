"""
Standalone shipping label creation.

Fetches the same orders as the address fixer (same state filter and lookback
window from config/morning_fetch.yaml) and creates DHL labels for all of them.

Usage:
    .venv/bin/python run_labels.py
    .venv/bin/python run_labels.py --since 2026-03-12
    .venv/bin/python run_labels.py --dry-run

Label folder and provider settings come from config/settings.yaml.
"""

import argparse
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml
from dotenv import load_dotenv
from rich.console import Console

_PROJECT_ROOT = Path(__file__).parent

load_dotenv(_PROJECT_ROOT / ".env")

sys.path.insert(0, str(_PROJECT_ROOT))


def _resolve_folder(path_str: str) -> Path:
    """Resolve a folder path from settings. Relative paths resolve from the project root."""
    p = Path(path_str).expanduser()
    if not p.is_absolute():
        p = _PROJECT_ROOT / p
    return p
from execution.billbee_client import BillbeeClient
from execution.fetch_new_orders import fetch_orders_since, load_fetch_config, compute_since
from execution.create_labels import create_labels_with_polling

console = Console()

_RICH_TAG_RE = re.compile(r"\[/?[^\]]*\]")


def main() -> None:
    parser = argparse.ArgumentParser(description="Create shipping labels for recent orders")
    parser.add_argument("--since", metavar="DATE",
                        help="Override lookback date, e.g. 2026-03-12")
    parser.add_argument("--dry-run", action="store_true",
                        help="Fetch orders and resolve providers but do not create labels")
    args = parser.parse_args()

    # Load settings
    _settings_file = Path(__file__).parent / "config" / "settings.yaml"
    _settings: dict = {}
    if _settings_file.exists():
        with open(_settings_file, encoding="utf-8") as f:
            _settings = yaml.safe_load(f) or {}

    _labels_cfg = _settings.get("shipping_labels") or {}
    label_folder = _resolve_folder(_labels_cfg.get("label_folder") or "labels")
    provider_id = int(_labels_cfg.get("shipping_provider_id") or 0)
    product_id = int(_labels_cfg.get("shipping_provider_product_id") or 0)
    timeout_minutes = int(_labels_cfg.get("polling_timeout_minutes") or 15)

    # Log file
    run_start = datetime.now(timezone.utc)
    log_dir = Path(__file__).parent / "logs"
    log_dir.mkdir(exist_ok=True)
    log_path = log_dir / f"labels_{run_start.strftime('%Y-%m-%d_%H%M%S')}.log"
    log_file = open(log_path, "w", encoding="utf-8", buffering=1)

    def _log(msg: str) -> None:
        log_file.write(_RICH_TAG_RE.sub("", msg) + "\n")

    def _clog(msg: str) -> None:
        console.print(msg)
        log_file.write(_RICH_TAG_RE.sub("", msg) + "\n")

    _log(f"=== run_labels started {run_start.strftime('%Y-%m-%d %H:%M:%S')} UTC ===")
    if args.dry_run:
        _clog("[yellow bold]DRY RUN — no labels will be created[/]")

    # Fetch orders
    fetch_cfg = load_fetch_config()
    since = compute_since(fetch_cfg["lookback_hours"], override=args.since)
    _clog(
        f"[dim]Fetching orders since {since}  "
        f"(states={fetch_cfg['state_ids'] or 'all'})[/]"
    )

    client = BillbeeClient()
    orders = fetch_orders_since(client, since, state_ids=fetch_cfg["state_ids"])
    _clog(f"[dim]Orders found: {len(orders)}[/]")
    _log(f"Orders fetched: {len(orders)}  (since {since})")

    if not orders:
        _clog("[green]No orders found — nothing to label.[/]")
        _log("=== run_labels finished — no orders ===")
        log_file.close()
        return

    # Show order list
    for o in orders:
        num = o.get("OrderNumber") or o.get("BillBeeOrderId")
        provider = o.get("ShippingProviderName") or "?"
        product = o.get("ShippingProviderProductName") or "?"
        _clog(f"  [dim]{num}  {provider} / {product}[/]")

    if args.dry_run:
        _clog("\n[yellow]Dry run — skipping label creation.[/]")
        log_file.close()
        return

    _clog(f"\n[cyan]Polling for labels — {len(orders)} order(s) → {label_folder}[/]")

    _transitions_cfg = _settings.get("order_state_transitions") or {}
    after_label_state = int(_transitions_cfg.get("after_label") or 0)

    stats = create_labels_with_polling(
        client, orders, label_folder,
        provider_id=provider_id,
        product_id=product_id,
        after_label_state=after_label_state,
        timeout_minutes=timeout_minutes,
        initial_wait=False,  # manual run — check immediately, wait between retries
        console=console,
        log_fn=lambda msg: log_file.write(msg + "\n"),
    )

    summary = (
        f"Labels: {stats['created']} created, "
        f"{stats['skipped']} skipped (already labeled), "
        f"{stats['failed']} failed"
    )
    _clog(f"\n[dim]{summary}[/]")

    errors = stats.get("errors") or []
    if errors:
        lines = [f"[bold red]✗ {len(errors)} problem(s) require attention:[/]\n"]
        for err in errors:
            lines.append(
                f"  [bold]{err['order_number']}[/]  "
                f"[yellow]{err['operation']}[/]  —  {err['error']}"
            )
        from rich.panel import Panel
        console.print(Panel("\n".join(lines), border_style="red", title="[bold red]Errors[/]"))
        log_file.write("\n=== ERRORS ===\n")
        for err in errors:
            log_file.write(f"  {err['order_number']}  [{err['operation']}]  {err['error']}\n")

    run_end = datetime.now(timezone.utc)
    _log(
        f"=== run_labels finished {run_end.strftime('%Y-%m-%d %H:%M:%S')} UTC "
        f"({(run_end - run_start).seconds}s) ==="
    )
    log_file.close()
    console.print(f"[dim]Log saved: {log_path}[/]")


if __name__ == "__main__":
    main()
