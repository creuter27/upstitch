"""
fetch_documents.py
------------------
Fetches invoices and/or delivery notes. Supports two sources:

  document_source: billbee        (default) — downloads PDFs via Billbee API
  document_source: google_drive   — syncs a Drive folder to the local output dir

Configuration: config/morning_fetch.yaml
Logs:          logs/fetchDocuments-{timestamp}.log

Usage (standalone):
    .venv/bin/python execution/fetch_documents.py
    .venv/bin/python execution/fetch_documents.py --dry-run
    .venv/bin/python execution/fetch_documents.py --since 2026-01-01T06:00:00
"""

import argparse
import json
import re
import sys
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).parent.parent
load_dotenv(PROJECT_ROOT / ".env")

sys.path.insert(0, str(PROJECT_ROOT))
from execution.billbee_client import BillbeeClient  # noqa: E402

DEFAULT_CONFIG = PROJECT_ROOT / "config" / "config.yaml"
DEFAULT_LOG_DIR = PROJECT_ROOT / "logs"

# Doc-type key → config key for output directory
OUTPUT_DIR_KEYS = {
    "invoice": "output_dir_invoice",
    "delivery-note": "output_dir_delivery_note",
}

# Amazon order number format: 302-1234567-7654321
_AMAZON_ORDER_RE = re.compile(r"^\d{3}-\d{7}-\d{7}$")
_AMAZON_SC_URL = "https://sellercentral.amazon.de/orders-v3/order/{}"
_MISSING_STATE_FILE = PROJECT_ROOT / "logs" / "missing_documents.json"

STATUS_DISPLAY = {
    "downloaded":          "downloaded",
    "downloaded (Amazon)": "downloaded (Amazon)",
    "would_download":      "would download",
    "already_present":     "already present",
    "missing":             "MISSING",
    "error":               "ERROR",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def print_missing_amazon_links(missing_order_numbers: list[str]) -> None:
    """Print Seller Central links for missing Amazon orders and save state for run_labels."""
    amazon = sorted(n for n in missing_order_numbers if _AMAZON_ORDER_RE.match(n))
    if amazon:
        print(f"\nMissing documents — {len(amazon)} Amazon order(s):")
        for on in amazon:
            url = _AMAZON_SC_URL.format(on)
            print(f"  {on}  {url}")
    # Always write state file (even empty list) so run_labels.py knows fetch ran
    _MISSING_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _MISSING_STATE_FILE.write_text(
        json.dumps({"missing": list(missing_order_numbers), "ts": datetime.now().isoformat()},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_config(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def resolve_cfg_path(raw: str) -> Path:
    """Resolve a path from config. Relative paths resolve from PROJECT_ROOT."""
    p = Path(raw)
    if raw.startswith("~") or p.is_absolute():
        return p.expanduser().resolve()
    return (PROJECT_ROOT / p).resolve()


def resolve_output_dir(cfg: dict, doc_type: str) -> Path:
    key = OUTPUT_DIR_KEYS[doc_type]
    out = resolve_cfg_path(cfg[key])
    out.mkdir(parents=True, exist_ok=True)
    return out


def build_filename(pattern: str, order: dict, doc_type: str, today: str) -> str:
    order_number = order.get("OrderNumber") or order.get("Id") or str(order.get("BillBeeOrderId", "unknown"))
    order_number = order_number.replace("/", "-").replace("\\", "-").replace(":", "-")
    return pattern.format(
        order_id=order.get("BillBeeOrderId", "unknown"),
        order_number=order_number,
        date=today,
        type=doc_type,
    )


def _safe_order_number(order: dict) -> str:
    on = order.get("OrderNumber") or order.get("Id") or str(order.get("BillBeeOrderId", "unknown"))
    return on.replace("/", "-").replace("\\", "-").replace(":", "-")


def write_pdf(pdf_bytes: bytes, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(pdf_bytes)


# ---------------------------------------------------------------------------
# Structured report log
# ---------------------------------------------------------------------------

def write_report_log(
    results: list[dict],
    fetch_invoice: bool,
    fetch_delivery_note: bool,
    dry_run: bool,
    log_dir: Path,
    run_ts: datetime,
) -> tuple[Path, str]:
    log_dir.mkdir(parents=True, exist_ok=True)
    ts_str = run_ts.strftime("%Y-%m-%d_%H-%M-%S")
    log_path = log_dir / f"fetchDocuments-{ts_str}.log"

    doc_types: list[str] = []
    if fetch_invoice:
        doc_types.append("invoice")
    if fetch_delivery_note:
        doc_types.append("delivery-note")

    summary_lines: list[str] = []
    ts_display = run_ts.strftime("%Y-%m-%d %H:%M:%S")
    header = f"=== Document Fetch — {ts_display} ==="
    if dry_run:
        header += " [DRY RUN]"
    summary_lines += [header, ""]

    summary_lines.append("SUMMARY")
    summary_lines.append(f"  Orders found: {len(results)}")
    for dt in doc_types:
        counts: dict[str, int] = {}
        for r in results:
            status = (r.get(dt) or {}).get("status") or "missing"
            counts[status] = counts.get(status, 0) + 1
        label = "Invoices" if dt == "invoice" else "Delivery notes"
        summary_lines.append(f"  {label}:")
        if dry_run:
            summary_lines.append(f"    Would download:  {counts.get('would_download', 0)}")
        else:
            summary_lines.append(f"    Downloaded:      {counts.get('downloaded', 0)}")
        summary_lines.append(f"    Already present: {counts.get('already_present', 0)}")
        summary_lines.append(f"    Missing:         {counts.get('missing', 0)}")
        summary_lines.append(f"    Errors:          {counts.get('error', 0)}")
    summary_lines.append("")

    lines: list[str] = list(summary_lines)

    lines.append("MISSING FILES")
    missing_lines: list[str] = []
    for r in sorted(results, key=lambda x: x["order_number"]):
        for dt in doc_types:
            if (r.get(dt) or {}).get("status") == "missing":
                missing_lines.append(f"  {r['order_number']}  [{dt}]")
    lines += missing_lines if missing_lines else ["  (none)"]
    lines.append("")

    lines.append("FULL REPORT")
    col_w = max((len(r["order_number"]) for r in results), default=20)
    col_w = max(col_w, len("ORDER"))
    header_row = "  " + "ORDER".ljust(col_w + 2)
    for dt in doc_types:
        header_row += dt.upper().replace("-", " ").ljust(22)
    lines.append(header_row)
    lines.append("  " + "-" * (col_w + 2 + len(doc_types) * 22))

    for r in sorted(results, key=lambda x: x["order_number"]):
        row = "  " + r["order_number"].ljust(col_w + 2)
        for dt in doc_types:
            status = (r.get(dt) or {}).get("status")
            row += STATUS_DISPLAY.get(status or "", status or "—").ljust(22)
        lines.append(row)

    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return log_path, "\n".join(summary_lines)


# ---------------------------------------------------------------------------
# Amazon SP-API invoice fallback
# ---------------------------------------------------------------------------

def _try_amazon_invoice_fallback(
    orders: list[dict],
    results: dict[str, dict],
    output_dir: Path,
    filename_pattern: str,
    today: str,
    dry_run: bool,
) -> None:
    try:
        from amazon_sp_client import AmazonSpClient
    except ImportError as exc:
        print(f"\n[amazon] Cannot import AmazonSpClient: {exc}")
        return

    missing_orders = [
        o for o in orders
        if results[_safe_order_number(o)].get("invoice", {}).get("status") == "missing"
    ]
    if not missing_orders:
        print("\n[amazon] No missing invoices to attempt from Amazon SP-API.")
        return

    print(f"\n[amazon] Attempting SP-API invoice download for {len(missing_orders)} missing order(s) …")

    try:
        client = AmazonSpClient()
    except (EnvironmentError, ValueError) as exc:
        print(f"[amazon] Cannot initialise client: {exc}")
        return

    for order in missing_orders:
        key = _safe_order_number(order)
        order_number = order.get("OrderNumber") or order.get("Id") or str(order.get("BillBeeOrderId"))

        if dry_run:
            print(f"  Order {key} — DRY RUN: would attempt Amazon SP-API invoice download")
            results[key]["invoice"]["status"] = "would_download"
            continue

        pdf_bytes = client.get_invoice_pdf(order_number)
        if pdf_bytes:
            filename = results[key]["invoice"]["filename"]
            dest = output_dir / filename
            write_pdf(pdf_bytes, dest)
            print(f"  Order {key} — saved from Amazon  {filename}  ({len(pdf_bytes):,} bytes)")
            results[key]["invoice"]["status"] = "downloaded (Amazon)"


# ---------------------------------------------------------------------------
# Google Drive mode
# ---------------------------------------------------------------------------

def run_google_drive(cfg: dict, dry_run: bool, min_date: str,
                     fetch_invoice: bool, fetch_delivery_note: bool) -> tuple[int, list[dict]]:
    from google_drive_downloader import GoogleDriveDownloader

    creds_cfg = cfg.get("google_drive_credentials", "").strip()
    credentials_file = Path(creds_cfg).expanduser() if creds_cfg else None

    print("[drive] Authenticating with Google Drive …")
    drive = GoogleDriveDownloader(credentials_file)

    billbee_client = BillbeeClient()
    raw_states = cfg["order_state_id"]
    state_ids = [int(s) for s in (raw_states if isinstance(raw_states, list) else [raw_states])]

    orders_by_id: dict = {}
    for state_id in state_ids:
        print(f"Fetching orders with state {state_id} since {min_date} …")
        for order in billbee_client.get_orders(order_state_id=state_id, min_date=min_date):
            orders_by_id[order.get("Id")] = order
    orders = list(orders_by_id.values())
    print(f"Found {len(orders)} order(s) across {len(state_ids)} state(s).")

    if not orders:
        print("Nothing to do.")
        return 0, []

    filename_pattern = cfg.get("filename_pattern", "{date}_{order_number}_{type}.pdf")
    today = datetime.now().strftime("%Y-%m-%d")

    tasks: list[tuple[str, str]] = []
    if fetch_invoice:
        tasks.append(("invoice", cfg.get("google_drive_folder_id_invoice", "")))
    if fetch_delivery_note:
        tasks.append(("delivery-note", cfg.get("google_drive_folder_id_delivery_note", "")))

    results: dict[str, dict] = {}
    for order in orders:
        key = _safe_order_number(order)
        entry: dict = {"order_number": key, "billbee_id": order.get("BillBeeOrderId")}
        for doc_type, _ in tasks:
            entry[doc_type] = {"status": None, "filename": None}
        results[key] = entry

    # Pre-check local files
    print()
    any_present = False
    for doc_type, _ in tasks:
        output_dir = resolve_output_dir(cfg, doc_type)
        for order in orders:
            key = _safe_order_number(order)
            filename = build_filename(filename_pattern, order, doc_type, today)
            results[key][doc_type]["filename"] = filename
            if (output_dir / filename).exists():
                results[key][doc_type]["status"] = "already_present"
                print(f"  [{doc_type}] {key} — already present locally, skipping")
                any_present = True
    if not any_present:
        print("  (no local files found — will check Drive for all orders)")

    stats = {"ok": 0, "skipped": 0, "errors": 0}

    for doc_type, folder_id in tasks:
        pending = [o for o in orders if results[_safe_order_number(o)][doc_type]["status"] is None]
        n_present = len(orders) - len(pending)
        stats["skipped"] += n_present

        if not pending:
            print(f"\n[{doc_type}] All {len(orders)} file(s) already present locally — skipping Drive scan")
            continue

        if not folder_id:
            cfg_key = f"google_drive_folder_id_{doc_type.replace('-', '_')}"
            print(f"\n[{doc_type}] ERROR: {cfg_key} is not set in config — skipping")
            for order in pending:
                results[_safe_order_number(order)][doc_type]["status"] = "error"
            stats["errors"] += len(pending)
            continue

        output_dir = resolve_output_dir(cfg, doc_type)
        print(f"\n[{doc_type}] Scanning Drive folder {folder_id} (recursively) …")
        drive_files = drive.list_files_recursive(folder_id)
        print(f"[{doc_type}] Found {len(drive_files)} PDF(s) across all subfolders")
        if n_present:
            print(f"[{doc_type}] {n_present} already present locally; checking {len(pending)} in Drive …")

        for order in pending:
            key = _safe_order_number(order)
            match = next(
                (f for f in drive_files
                 if key in f["name"] and doc_type in f["name"].lower()),
                None,
            )

            if match is None:
                print(f"  Order {key} — not found in Drive yet (skip)")
                results[key][doc_type]["status"] = "missing"
                stats["skipped"] += 1
                continue

            filename = results[key][doc_type]["filename"]
            dest = output_dir / filename

            if dry_run:
                print(f"  Order {key} — DRY RUN: {match['name']} → {filename}")
                results[key][doc_type]["status"] = "would_download"
                stats["ok"] += 1
                continue

            try:
                pdf_bytes = drive.download_file(match["id"])
                write_pdf(pdf_bytes, dest)
                print(f"  Order {key} — saved {filename} ({len(pdf_bytes):,} bytes)")
                results[key][doc_type]["status"] = "downloaded"
                stats["ok"] += 1
            except Exception as exc:
                print(f"  Order {key} — ERROR: {exc}")
                traceback.print_exc()
                results[key][doc_type]["status"] = "error"
                stats["errors"] += 1

    # Amazon SP-API fallback
    if fetch_invoice and cfg.get("try_amazon_invoice_fallback", False):
        invoice_output_dir = resolve_output_dir(cfg, "invoice")
        _try_amazon_invoice_fallback(
            orders, results, invoice_output_dir, filename_pattern, today, dry_run
        )
        amazon_downloaded = sum(
            1 for r in results.values()
            if r.get("invoice", {}).get("status") == "downloaded (Amazon)"
        )
        if amazon_downloaded:
            stats["ok"] += amazon_downloaded

    print(f"\nDone. saved={stats['ok']}  skipped={stats['skipped']}  errors={stats['errors']}")

    missing_order_numbers = [
        r["order_number"] for r in results.values()
        if any((r.get(dt) or {}).get("status") == "missing" for dt in ("invoice", "delivery-note"))
    ]
    print_missing_amazon_links(missing_order_numbers)
    return stats["errors"], list(results.values())


# ---------------------------------------------------------------------------
# Billbee mode
# ---------------------------------------------------------------------------

def run_billbee(cfg: dict, dry_run: bool, min_date: str,
                fetch_invoice: bool, fetch_delivery_note: bool) -> int:
    client = BillbeeClient()

    raw_states = cfg["order_state_id"]
    state_ids = [int(s) for s in (raw_states if isinstance(raw_states, list) else [raw_states])]
    auto_generate_invoice = bool(cfg.get("auto_generate_invoice", True))
    filename_pattern = cfg.get("filename_pattern", "{date}_{order_number}_{type}.pdf")
    today = datetime.now().strftime("%Y-%m-%d")

    print(f"[config] order_state_id={state_ids}")
    print(f"[config] auto_generate_invoice={auto_generate_invoice}")
    if fetch_invoice:
        print(f"[output] invoices     → {resolve_output_dir(cfg, 'invoice')}")
    if fetch_delivery_note:
        print(f"[output] delivery-notes → {resolve_output_dir(cfg, 'delivery-note')}")
    print()

    orders_by_id = {}
    for state_id in state_ids:
        print(f"Fetching orders with state {state_id} since {min_date} …")
        for order in client.get_orders(order_state_id=state_id, min_date=min_date):
            orders_by_id[order.get("Id")] = order
    orders = list(orders_by_id.values())
    print(f"Found {len(orders)} order(s) across {len(state_ids)} state(s).")

    if not orders:
        print("Nothing to do.")
        return 0

    invoice_pdf_urls: dict = {}
    if fetch_invoice and auto_generate_invoice and not dry_run:
        output_dir_inv = resolve_output_dir(cfg, "invoice")
        print("--- Checking invoices in Billbee ---")
        gen_ok = gen_skip = gen_fail = 0
        for order in orders:
            billbee_id = order.get("BillBeeOrderId")
            order_number = order.get("OrderNumber") or order.get("Id") or str(billbee_id)
            filename = build_filename(filename_pattern, order, "invoice", today)
            dest = output_dir_inv / filename
            if dest.exists():
                gen_skip += 1
                continue
            result = client.create_invoice(billbee_id)
            if result is None:
                print(f"  Order {order_number} → no invoice in Billbee yet")
                gen_fail += 1
            else:
                invoice_num = result.get("InvoiceNumber") or "found"
                pdf_url = result.get("PdfDownloadUrl")
                if pdf_url:
                    invoice_pdf_urls[billbee_id] = pdf_url
                print(f"  Order {order_number} → invoice {invoice_num}")
                gen_ok += 1
        print(f"  Found: {gen_ok}  skipped (file exists): {gen_skip}  missing: {gen_fail}")
        print()

    stats = {"ok": 0, "skipped": 0, "errors": 0}
    missing_order_numbers: list[str] = []

    doc_tasks = []
    if fetch_invoice:
        doc_tasks.append(("invoice", client.get_invoice_pdf))
    if fetch_delivery_note:
        doc_tasks.append(("delivery-note", client.get_delivery_note_pdf))

    for order in orders:
        billbee_id = order.get("BillBeeOrderId")
        order_number = order.get("OrderNumber") or order.get("Id") or str(billbee_id)
        print(f"\n  Order {order_number} (BillBeeOrderId={billbee_id})")
        order_has_missing = False

        for doc_type, fetch_fn in doc_tasks:
            output_dir = resolve_output_dir(cfg, doc_type)
            filename = build_filename(filename_pattern, order, doc_type, today)
            dest = output_dir / filename

            if dest.exists():
                print(f"    [{doc_type}] already exists — skip  ({dest.name})")
                stats["skipped"] += 1
                continue

            if dry_run:
                print(f"    [{doc_type}] DRY RUN — would save to {dest}")
                stats["ok"] += 1
                continue

            try:
                if doc_type == "invoice" and billbee_id in invoice_pdf_urls:
                    pdf_bytes = client.download_pdf_from_url(invoice_pdf_urls[billbee_id])
                else:
                    pdf_bytes = fetch_fn(billbee_id)
                if pdf_bytes is None:
                    print(f"    [{doc_type}] no document yet in Billbee — skip")
                    stats["skipped"] += 1
                    order_has_missing = True
                    continue
                write_pdf(pdf_bytes, dest)
                print(f"    [{doc_type}] saved  {dest.name}  ({len(pdf_bytes):,} bytes)")
                stats["ok"] += 1
            except Exception as exc:
                print(f"    [{doc_type}] ERROR: {exc}")
                traceback.print_exc()
                stats["errors"] += 1

        if order_has_missing:
            missing_order_numbers.append(order_number)

    print(f"\nDone. saved={stats['ok']}  skipped={stats['skipped']}  errors={stats['errors']}")
    print_missing_amazon_links(missing_order_numbers)
    return stats["errors"]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run(cfg: dict, dry_run: bool = False, since_override: str | None = None) -> int:
    """Dispatch to the configured document source. Returns number of errors."""
    document_source = cfg.get("document_source", "billbee")
    fetch_invoice = bool(cfg.get("fetch_invoice", True))
    fetch_delivery_note = bool(cfg.get("fetch_delivery_note", True))

    # Build lookback window
    lookback_hours = float(cfg.get("lookback_hours", 26))
    lookback_from = cfg.get("lookback_from")

    if since_override:
        min_date = since_override
    elif lookback_from:
        min_date = str(lookback_from)
    else:
        from datetime import timedelta
        cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
        min_date = cutoff.strftime("%Y-%m-%dT%H:%M:%S")

    print(f"[config] document_source={document_source}")
    print(f"[config] fetch_invoice={fetch_invoice}  fetch_delivery_note={fetch_delivery_note}")
    print(f"[window] looking back from {min_date}")

    if document_source == "google_drive":
        run_ts = datetime.now(timezone.utc)
        errors, results = run_google_drive(cfg, dry_run, min_date, fetch_invoice, fetch_delivery_note)

        if results:
            log_dir_raw = cfg.get("log_dir")
            log_dir = resolve_cfg_path(log_dir_raw) if log_dir_raw else DEFAULT_LOG_DIR
            report_path, summary_text = write_report_log(
                results, fetch_invoice, fetch_delivery_note, dry_run, log_dir, run_ts
            )
            print(f"\n{summary_text}")
            print(f"[log]   report written to {report_path}")

        return errors

    elif document_source == "billbee":
        return run_billbee(cfg, dry_run, min_date, fetch_invoice, fetch_delivery_note)
    else:
        print(f"ERROR: Unknown document_source '{document_source}'. Use 'billbee' or 'google_drive'.")
        return 1


def main():
    parser = argparse.ArgumentParser(description="Fetch Billbee invoices/delivery notes")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG),
                        help="Path to config YAML (default: config/morning_fetch.yaml)")
    parser.add_argument("--dry-run", action="store_true",
                        help="List what would be downloaded without actually downloading")
    parser.add_argument("--since",
                        help="Override lookback: fetch since this ISO datetime "
                             "(e.g. '2026-02-01T06:00:00'). Takes priority over config.")
    args = parser.parse_args()

    # Use platform-aware config loader when using the default path;
    # fall back to direct file load if a custom --config was provided.
    if args.config == str(DEFAULT_CONFIG):
        from execution.config_loader import load_config as _platform_load
        cfg = _platform_load()
    else:
        cfg = load_config(Path(args.config))
    errors = run(cfg, dry_run=args.dry_run, since_override=args.since)

    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
