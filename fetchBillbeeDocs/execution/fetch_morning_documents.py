"""
fetch_morning_documents.py
--------------------------
Fetches invoices and/or delivery notes. Supports two sources:

  document_source: billbee        (default) — downloads PDFs via Billbee API
  document_source: google_drive   — syncs a Drive folder to the local output dir

The lookback window is calculated from the last recorded run time so no files
are missed between runs.

Designed to run every weekday at 06:30 via a macOS LaunchAgent
(see launchd/com.billbee.morning-fetch.plist) or Windows Task Scheduler
(see taskscheduler/billbee-morning-fetch.xml).

Configuration: config/morning_fetch.yaml
State:         .tmp/last_run.json  (written after each real run)
"""

import argparse
import json
import sys
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).parent.parent

# Load project-specific vars (e.g. GOOGLE_CREDENTIALS_FILE) from the project root.
# Billbee API credentials are NOT stored here — they live in the shared
# ~/code/billbee-python-client/.env and are loaded automatically by BillbeeClient.
_env_file = PROJECT_ROOT / ".env"
if _env_file.exists():
    load_dotenv(_env_file)

from billbee_client import BillbeeClient

DEFAULT_CONFIG = PROJECT_ROOT / "config" / "morning_fetch.yaml"
DEFAULT_LOG_DIR = PROJECT_ROOT / ".tmp" / "logs"
STATE_FILE = PROJECT_ROOT / ".tmp" / "last_run.json"

# Doc-type key → config key for output directory
OUTPUT_DIR_KEYS = {
    "invoice": "output_dir_invoice",
    "delivery-note": "output_dir_delivery_note",
}

# Human-readable labels for the report
STATUS_DISPLAY = {
    "downloaded":          "downloaded",
    "downloaded (Amazon)": "downloaded (Amazon)",
    "would_download":      "would download",
    "already_present":     "already present",
    "missing":             "MISSING",
    "error":               "ERROR",
}


# ---------------------------------------------------------------------------
# State (last-run persistence)
# ---------------------------------------------------------------------------

def load_last_run() -> datetime | None:
    """Return the UTC datetime of the last successful run, or None."""
    if not STATE_FILE.exists():
        return None
    data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return datetime.fromisoformat(data["last_run"])


def save_last_run(dt: datetime) -> None:
    """Persist the run timestamp (UTC) to the state file."""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(
        json.dumps({"last_run": dt.isoformat()}, indent=2),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_config(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def resolve_cfg_path(raw: str) -> Path:
    """
    Resolve a path from config.
    - Absolute paths and ~/... paths are used as-is (after expanduser).
    - Relative paths are resolved relative to PROJECT_ROOT so the project
      can be moved without breaking anything.
    """
    p = Path(raw)
    if raw.startswith("~") or p.is_absolute():
        return p.expanduser().resolve()
    return (PROJECT_ROOT / p).resolve()


def resolve_output_dir(cfg: dict, doc_type: str) -> Path:
    key = OUTPUT_DIR_KEYS[doc_type]
    out = resolve_cfg_path(cfg[key])
    out.mkdir(parents=True, exist_ok=True)
    return out


def build_min_date(cfg: dict, since_override: str | None) -> str:
    """
    Determine the earliest date to fetch from.

    Priority (highest first):
      1. --since CLI flag
      2. lookback_from in config
      3. last_run - lookback_hours  (automatic, uses state file)
      4. now - lookback_hours       (first-ever run fallback)

    Returns an ISO-8601 string (no timezone suffix).
    """
    if since_override:
        return since_override

    lookback_from = cfg.get("lookback_from")
    if lookback_from:
        return str(lookback_from)

    lookback_hours = float(cfg.get("lookback_hours", 26))
    last_run = load_last_run()
    anchor = last_run if last_run else datetime.now(timezone.utc)
    cutoff = anchor - timedelta(hours=lookback_hours)
    return cutoff.strftime("%Y-%m-%dT%H:%M:%S")


def build_filename(pattern: str, order: dict, doc_type: str, today: str) -> str:
    # Use external order number (readable, e.g. Amazon order ID) for filenames.
    # Fall back to BillBeeOrderId if no external number exists.
    order_number = order.get("OrderNumber") or order.get("Id") or str(order.get("BillBeeOrderId", "unknown"))
    order_number = order_number.replace("/", "-").replace("\\", "-").replace(":", "-")
    return pattern.format(
        order_id=order.get("BillBeeOrderId", "unknown"),
        order_number=order_number,
        date=today,
        type=doc_type,
    )


def _safe_order_number(order: dict) -> str:
    """Return a filesystem-safe order number string (matches build_filename logic)."""
    on = order.get("OrderNumber") or order.get("Id") or str(order.get("BillBeeOrderId", "unknown"))
    return on.replace("/", "-").replace("\\", "-").replace(":", "-")


def write_pdf(pdf_bytes: bytes, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(pdf_bytes)


def setup_log(log_dir: Path) -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    return log_dir / f"morning_fetch_{ts}.log"


class Tee:
    """Write to both stdout and a log file."""
    def __init__(self, log_path: Path):
        self._log = open(log_path, "w", encoding="utf-8")
        self._stdout = sys.stdout

    def write(self, msg):
        self._stdout.write(msg)
        self._log.write(msg)

    def flush(self):
        self._stdout.flush()
        self._log.flush()

    def close(self):
        self._log.close()


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
    """
    Write a structured fetchDocuments-{timestamp}.log report to log_dir.

    Report sections:
      1. Header + SUMMARY  (orders found, per-type counts)
      2. MISSING FILES     (orders where Drive file was not found)
      3. FULL REPORT       (every order with status per doc type)

    Returns (report_path, summary_text) so the caller can print the summary
    to the terminal as well.
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    ts_str = run_ts.strftime("%Y-%m-%d_%H-%M-%S")
    log_path = log_dir / f"fetchDocuments-{ts_str}.log"

    doc_types: list[str] = []
    if fetch_invoice:
        doc_types.append("invoice")
    if fetch_delivery_note:
        doc_types.append("delivery-note")

    # Build summary lines separately so they can be returned for stdout printing.
    summary_lines: list[str] = []

    # --- Header ---
    ts_display = run_ts.strftime("%Y-%m-%d %H:%M:%S")
    header = f"=== Morning Document Fetch — {ts_display} ==="
    if dry_run:
        header += " [DRY RUN]"
    summary_lines += [header, ""]

    # --- Summary ---
    summary_lines.append("SUMMARY")
    summary_lines.append(f"  Orders found in Billbee: {len(results)}")
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

    # --- Missing files ---
    lines.append("MISSING FILES")
    missing_lines: list[str] = []
    for r in sorted(results, key=lambda x: x["order_number"]):
        for dt in doc_types:
            if (r.get(dt) or {}).get("status") == "missing":
                missing_lines.append(f"  {r['order_number']}  [{dt}]")
    lines += missing_lines if missing_lines else ["  (none)"]
    lines.append("")

    # --- Full report ---
    lines.append("FULL REPORT")
    col_w = max((len(r["order_number"]) for r in results), default=20)
    col_w = max(col_w, len("ORDER"))

    # Header row
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
    """
    For each order whose invoice is still "missing" after the Drive scan,
    attempt to download the invoice from Amazon's Selling Partner API.

    This handles orders where Amazon (not Billbee) generated the invoice,
    so it never appeared in the Billbee-connected Google Drive folder.

    Mutates `results` in-place, updating invoice status to "downloaded (Amazon)"
    on success. Orders that remain unfound stay as "missing".

    Lazy-imports AmazonSpClient so the import error is only raised if this
    function is actually called (i.e. try_amazon_invoice_fallback: true in config).
    """
    try:
        from amazon_sp_client import AmazonSpClient
    except ImportError as exc:
        print(f"\n[amazon] Cannot import AmazonSpClient: {exc}")
        print("[amazon] Install with: pip install -e ~/code/amazon-sp-client")
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
        # If None, client already printed why; status stays "missing"


# ---------------------------------------------------------------------------
# Google Drive mode
# ---------------------------------------------------------------------------

def run_google_drive(cfg: dict, dry_run: bool, min_date: str,
                     fetch_invoice: bool, fetch_delivery_note: bool) -> tuple[int, list[dict]]:
    """
    Download documents from Google Drive, matching Drive files to Billbee orders
    by order number. Saves files using the standard filename_pattern.

    Flow:
      1. Query Billbee for orders in the lookback window (to get order numbers).
      2. Pre-check: mark orders whose local files already exist (skip Drive for those).
      3. For each doc type, recursively list Drive PDFs only if there are pending orders.
      4. Match each pending order to a Drive file (order number + doc-type keyword).
      5. Download matches and save with the standard filename_pattern.

    Returns (error_count, results_list).
    Each result: {"order_number": str, "billbee_id": int,
                  doc_type: {"status": str, "filename": str}}
    Statuses: "downloaded" | "would_download" | "already_present" | "missing" | "error"
    """
    from google_drive_downloader import GoogleDriveDownloader

    # If google_drive_credentials is set in config, use it; otherwise pass None so
    # GoogleDriveDownloader uses its own default (credentials.json next to the module).
    # This avoids hardcoding an OS-specific path here.
    creds_cfg = cfg.get("google_drive_credentials", "").strip()
    credentials_file = Path(creds_cfg).expanduser() if creds_cfg else None

    print("[drive] Authenticating with Google Drive …")
    drive = GoogleDriveDownloader(credentials_file)

    # --- Fetch orders from Billbee to get order numbers ---
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

    # Determine which doc types to process
    tasks: list[tuple[str, str]] = []
    if fetch_invoice:
        tasks.append(("invoice", cfg.get("google_drive_folder_id_invoice", "")))
    if fetch_delivery_note:
        tasks.append(("delivery-note", cfg.get("google_drive_folder_id_delivery_note", "")))

    # --- Build results scaffold ---
    results: dict[str, dict] = {}
    for order in orders:
        key = _safe_order_number(order)
        entry: dict = {"order_number": key, "billbee_id": order.get("BillBeeOrderId")}
        for doc_type, _ in tasks:
            entry[doc_type] = {"status": None, "filename": None}
        results[key] = entry

    # --- Pre-check: mark files that already exist locally (before querying Drive) ---
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

    # --- Drive scan and download ---
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

    # --- Amazon SP-API fallback for still-missing invoices ---
    if fetch_invoice and cfg.get("try_amazon_invoice_fallback", False):
        invoice_output_dir = resolve_output_dir(cfg, "invoice")
        _try_amazon_invoice_fallback(
            orders, results, invoice_output_dir, filename_pattern, today, dry_run
        )
        # Recount ok from results (Amazon downloads aren't in stats["ok"] yet)
        amazon_downloaded = sum(
            1 for r in results.values()
            if r.get("invoice", {}).get("status") == "downloaded (Amazon)"
        )
        if amazon_downloaded:
            stats["ok"] += amazon_downloaded

    print(f"\nDone. saved={stats['ok']}  skipped={stats['skipped']}  errors={stats['errors']}")
    return stats["errors"], list(results.values())


# ---------------------------------------------------------------------------
# Billbee mode
# ---------------------------------------------------------------------------

def run_billbee(cfg: dict, dry_run: bool, min_date: str, last_run: datetime | None,
                fetch_invoice: bool, fetch_delivery_note: bool) -> int:
    """
    Fetch documents via the Billbee API.
    Returns number of errors.
    """
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

    # --- Collect orders ---
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

    # --- Phase 1: Check/generate invoices in Billbee ---
    # NOTE: CreateInvoice is a PDF renderer, not an invoice creator.
    # For invoices to exist, configure a Billbee automation rule:
    #   Settings → Automatisierungen → trigger on state 3 → create invoice
    invoice_pdf_urls: dict = {}  # billbee_id → PdfDownloadUrl (if returned)
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
                print(f"  Order {order_number} → no invoice in Billbee yet "
                      f"(see Billbee message above)")
                gen_fail += 1
            else:
                invoice_num = result.get("InvoiceNumber") or "found"
                pdf_url = result.get("PdfDownloadUrl")
                if pdf_url:
                    invoice_pdf_urls[billbee_id] = pdf_url
                print(f"  Order {order_number} → invoice {invoice_num}")
                gen_ok += 1
        print(f"  Found: {gen_ok}  skipped (file exists): {gen_skip}  "
              f"missing: {gen_fail}")
        if gen_fail:
            print("  TIP: Set up a Billbee automation rule to create invoices")
            print("       when orders enter your target state.")
        print()

    # --- Phase 2: Download documents ---
    stats = {"ok": 0, "skipped": 0, "errors": 0}

    doc_tasks = []
    if fetch_invoice:
        doc_tasks.append(("invoice", client.get_invoice_pdf))
    if fetch_delivery_note:
        doc_tasks.append(("delivery-note", client.get_delivery_note_pdf))

    for order in orders:
        billbee_id = order.get("BillBeeOrderId")
        order_number = order.get("OrderNumber") or order.get("Id") or str(billbee_id)
        print(f"\n  Order {order_number} (BillBeeOrderId={billbee_id})")

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
                # Use PdfDownloadUrl from Phase 1 if available (avoids rate limit)
                if doc_type == "invoice" and billbee_id in invoice_pdf_urls:
                    pdf_bytes = client.download_pdf_from_url(invoice_pdf_urls[billbee_id])
                else:
                    pdf_bytes = fetch_fn(billbee_id)
                if pdf_bytes is None:
                    print(f"    [{doc_type}] no document yet in Billbee — skip")
                    stats["skipped"] += 1
                    continue
                write_pdf(pdf_bytes, dest)
                print(f"    [{doc_type}] saved  {dest.name}  ({len(pdf_bytes):,} bytes)")
                stats["ok"] += 1
            except Exception as exc:
                print(f"    [{doc_type}] ERROR: {exc}")
                traceback.print_exc()
                stats["errors"] += 1

    print(f"\nDone. saved={stats['ok']}  skipped={stats['skipped']}  errors={stats['errors']}")
    return stats["errors"]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run(cfg: dict, dry_run: bool = False, since_override: str | None = None) -> int:
    """Dispatch to the configured document source. Returns number of errors."""
    document_source = cfg.get("document_source", "billbee")
    fetch_invoice = bool(cfg.get("fetch_invoice", True))
    fetch_delivery_note = bool(cfg.get("fetch_delivery_note", True))
    min_date = build_min_date(cfg, since_override)
    last_run = load_last_run()

    print(f"[config] document_source={document_source}")
    print(f"[config] fetch_invoice={fetch_invoice}  fetch_delivery_note={fetch_delivery_note}")
    print(f"[state]  last_run={last_run.isoformat() if last_run else 'never'}")
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
        return run_billbee(cfg, dry_run, min_date, last_run, fetch_invoice, fetch_delivery_note)
    else:
        print(f"ERROR: Unknown document_source '{document_source}'. Use 'billbee' or 'google_drive'.")
        return 1


def main():
    parser = argparse.ArgumentParser(description="Fetch Billbee invoices/delivery notes")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG),
                        help="Path to config YAML (default: config/morning_fetch.yaml)")
    parser.add_argument("--dry-run", action="store_true",
                        help="List what would be downloaded without actually downloading")
    parser.add_argument("--state", type=int,
                        help="Override order_state_id from config (Billbee mode only)")
    parser.add_argument("--since",
                        help="Override lookback: fetch since this ISO datetime "
                             "(e.g. '2026-02-01T06:00:00'). Takes priority over config.")
    parser.add_argument("--no-log", action="store_true",
                        help="Don't write a stdout log file")
    args = parser.parse_args()

    cfg = load_config(Path(args.config))

    if args.state is not None:
        cfg["order_state_id"] = args.state

    # Set up stdout mirror log (separate from the structured report log)
    tee = None
    if not args.no_log:
        log_dir_raw = cfg.get("log_dir")
        log_dir = Path(log_dir_raw).expanduser().resolve() if log_dir_raw else DEFAULT_LOG_DIR
        log_path = setup_log(log_dir)
        tee = Tee(log_path)
        sys.stdout = tee
        print(f"[log] stdout mirror → {log_path}")

    try:
        run_start = datetime.now(timezone.utc)
        errors = run(cfg, dry_run=args.dry_run, since_override=args.since)

        # Persist run timestamp only for real (non-dry) runs
        if not args.dry_run:
            save_last_run(run_start)
            print(f"[state]  last_run saved as {run_start.isoformat()}")
    finally:
        if tee:
            sys.stdout = tee._stdout
            tee.close()

    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
