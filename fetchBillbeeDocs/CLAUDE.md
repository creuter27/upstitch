# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project does

Weekday 06:30 automation that downloads Billbee invoices and delivery notes as PDFs.

Two document sources (configured in `config/morning_fetch.yaml`):
- `google_drive` — lists PDFs from a Billbee-connected Google Drive folder, matches them to Billbee orders, saves locally
- `billbee` — downloads directly via Billbee API

If invoices are still missing after the Drive scan, an Amazon SP-API fallback attempts to download Amazon-generated invoices.

## Running the script

```bash
# One-off run (macOS)
.venv/bin/python execution/fetch_morning_documents.py

# Dry run — shows what would be downloaded without saving anything
.venv/bin/python execution/fetch_morning_documents.py --dry-run

# Override lookback window (useful when catching up after a gap)
.venv/bin/python execution/fetch_morning_documents.py --since 2026-02-01T06:00:00

# Windows
run.bat --dry-run
```

There are no tests. A `--dry-run` is the standard way to verify changes.

## Python environment

```bash
python3.14 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/pip install -e ../billbee-python-client
.venv/bin/pip install -e ../google-client
.venv/bin/pip install -e ../amazon-sp-client   # optional, for Amazon fallback
```

Always use `.venv/bin/python` (not `python3` or `python`).

## Shared packages (sibling directories)

| Package | Path | What it provides |
|---|---|---|
| `billbee-python-client` | `../billbee-python-client/` | `BillbeeClient` — orders, invoices, delivery notes |
| `google-client` | `../google-client/` | `GoogleDriveDownloader`, `GoogleSheetsClient` |
| `amazon-sp-client` | `../amazon-sp-client/` | `AmazonSpClient` — SP-API invoice download |

Each package loads its own credentials from its own `.env` file (same directory as the module). Do not put shared-package credentials in this project's `.env`.

## Architecture

Single script: `execution/fetch_morning_documents.py`. Call chain:

```
main()
  └── run(cfg)                         # dispatches on document_source
        ├── run_google_drive()         # google_drive mode
        │     ├── BillbeeClient.get_orders()         # get order list
        │     ├── pre-check local files (already_present)
        │     ├── GoogleDriveDownloader.list_files_recursive()
        │     ├── match + download
        │     └── _try_amazon_invoice_fallback()     # if still missing
        └── run_billbee()              # billbee mode
```

After each real run, `run_google_drive()` calls `write_report_log()` which writes a structured `fetchDocuments-{timestamp}.log` to `log_dir`.

State is persisted in `.tmp/last_run.json` (timestamp of last successful run). The lookback window is computed from that timestamp.

## Configuration (`config/morning_fetch.yaml`)

Key settings:

| Key | Purpose |
|---|---|
| `document_source` | `google_drive` or `billbee` |
| `google_drive_folder_id_invoice` / `_delivery_note` | Drive folder IDs |
| `google_drive_credentials` | Path to OAuth2 JSON (from `~/code/google-client/`) |
| `order_state_id` | Billbee state(s) to query (3 = Versandfertig) |
| `lookback_hours` | How far back to look (48 covers weekends) |
| `filename_pattern` | Output filename template — placeholders: `{type}`, `{order_number}`, `{order_id}`, `{date}` |
| `output_dir_invoice` / `_delivery_note` | Where to save PDFs |
| `try_amazon_invoice_fallback` | Enable Amazon SP-API fallback for missing invoices |
| `log_dir` | Where to write log files (both stdout mirror and report) |

## Scheduling

- **macOS:** `launchd/com.billbee.morning-fetch.plist` → install with `launchctl load ~/Library/LaunchAgents/com.billbee.morning-fetch.plist`
- **Windows:** `taskscheduler/billbee-morning-fetch.xml` → import with `schtasks /create /xml ...`

## Google Drive file matching

Drive files are matched to Billbee orders by:
1. The order's external order number must appear as a substring of the Drive filename
2. The doc-type keyword (`invoice` or `delivery-note`) must appear in the filename

Files live in year/month subfolders; `list_files_recursive()` traverses the full tree.

## Amazon SP-API fallback

`_try_amazon_invoice_fallback()` in `fetch_morning_documents.py` runs after the Drive scan for any invoice still `"missing"`. It calls `AmazonSpClient.get_invoice_pdf(order_number)`, which uses `Invoices.get_invoices()` from `python-amazon-sp-api`. Credentials live in `~/code/amazon-sp-client/.env`.

## Report log format

`fetchDocuments-{timestamp}.log` has three sections:
1. **SUMMARY** — counts per status per doc type
2. **MISSING FILES** — orders with no match found
3. **FULL REPORT** — every order with invoice + delivery-note status

Statuses: `downloaded` / `downloaded (Amazon)` / `already present` / `MISSING` / `ERROR`

## Directives

`directives/fetch_morning_documents.md` is the living SOP for this pipeline. Update it when you discover API constraints, fix edge cases, or change the flow. It is the authoritative reference for how this system is supposed to work.
