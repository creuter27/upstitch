# Directive: Fetch Morning Documents from Billbee

## Purpose
Every weekday at 06:30, automatically download invoices and delivery notes.
Supports two sources (configured via `document_source` in the YAML):

- **`billbee`** — fetch PDFs via Billbee API (invoices must already exist in Billbee)
- **`google_drive`** — sync from a Google Drive folder (use when Billbee's automation
  saves documents directly to Drive)

## Inputs
- `config/morning_fetch.yaml` — all settings
- `.env` — Billbee API credentials (copy from `~/code/Billbee-Artikelmanager/.env`)
- `credentials.json` — Google OAuth2 Desktop credentials (only for `google_drive` mode)

## Tools
- `execution/fetch_morning_documents.py` — main script
- `execution/google_drive_downloader.py` — Google Drive auth + file listing/download
- `~/code/billbee-python-client/billbee_client.py` — shared Billbee API client
- `launchd/com.billbee.morning-fetch.plist` — macOS scheduler
- `taskscheduler/billbee-morning-fetch.xml` — Windows Task Scheduler definition
- `run.bat` — Windows manual run helper

## Outputs
- PDF files in the configured `output_dir` (default: `~/Documents/Billbee-Documents/`)
- Log files in `.tmp/logs/morning_fetch_<timestamp>.log`
- macOS LaunchAgent stdout/stderr in `.tmp/logs/launchd_stdout.log` / `launchd_stderr.log`

---

## One-time Setup — macOS

### 1. Create the venv and install dependencies
```bash
cd ~/code/fetchBillbeeDocs
python3.14 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/pip install -e ~/code/billbee-python-client/
```

### 2. Copy your Billbee credentials
```bash
cp ~/code/Billbee-Artikelmanager/.env ~/code/fetchBillbeeDocs/.env
```

### 3. (Google Drive mode only) Set up Google credentials

1. Go to [Google Cloud Console](https://console.cloud.google.com/) → APIs & Services → Enable **Google Drive API**
2. Create credentials: **OAuth 2.0 Client ID** → Application type: **Desktop app**
3. Download the JSON and save as `~/code/fetchBillbeeDocs/credentials.json`
4. Set the Drive folder IDs in `config/morning_fetch.yaml`:
   - Open the invoices folder in Drive — copy the ID from the URL (`/folders/<ID>`)
   - Paste into `google_drive_folder_id_invoice` (and `_delivery_note` similarly)
5. Run once interactively to complete OAuth consent (opens browser):
   ```bash
   .venv/bin/python execution/fetch_morning_documents.py --dry-run
   ```
   Token is cached at `.tmp/google_token.json` — subsequent runs are headless.

### 4. Install the LaunchAgent
```bash
cp launchd/com.billbee.morning-fetch.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.billbee.morning-fetch.plist
launchctl list | grep billbee    # should show the job
```

### 4. Test manually
```bash
.venv/bin/python execution/fetch_morning_documents.py --dry-run
```

### 5. Run for real
```bash
.venv/bin/python execution/fetch_morning_documents.py
```

---

## One-time Setup — Windows

### 1. Create the venv and install dependencies
Double-click `setup.bat` or run it from a command prompt:
```bat
setup.bat
```
This creates `.venv\`, installs `requirements.txt`, and installs the shared
Billbee client from `%USERPROFILE%\code\billbee-python-client`.

### 2. Copy your Billbee credentials
Place the `.env` file in the project root (`fetchBillbeeDocs\.env`).

### 3. Register the scheduled task
Edit `taskscheduler\billbee-morning-fetch.xml` — replace the **two occurrences**
of `YOUR_USERNAME` in the `<Principals>` block with your Windows username.
All other paths use `%USERPROFILE%` and resolve automatically. Then in an
elevated Command Prompt or PowerShell:
```bat
schtasks /create /xml taskscheduler\billbee-morning-fetch.xml /tn "Billbee Morning Fetch"
```

### 4. Test manually
```bat
run.bat --dry-run
```

### 5. Trigger a scheduled run immediately
```bat
schtasks /run /tn "Billbee Morning Fetch"
```

### 6. Delete the task if needed
```bat
schtasks /delete /tn "Billbee Morning Fetch" /f
```

---

## Configuration (`config/morning_fetch.yaml`)

| Key | Default | Description |
|-----|---------|-------------|
| `order_state_id` | `3` | Billbee state(s) to filter — single int or list, e.g. `[3, 13]` |
| `output_dir_invoice` | `~/Documents/Billbee-Documents/invoices` | Where to save invoice PDFs |
| `output_dir_delivery_note` | `~/Documents/Billbee-Documents/delivery-notes` | Where to save delivery note PDFs |
| `lookback_hours` | `26` | Hours before last run to look back (26 h covers weekend gap) |
| `lookback_from` | *(unset)* | Optional hard override: fetch from this exact ISO datetime |
| `fetch_invoice` | `true` | Download invoices |
| `fetch_delivery_note` | `true` | Download delivery notes |
| `auto_generate_invoice` | `true` | Before downloading, call CreateInvoice for each order to ensure invoices exist in Billbee |
| `filename_pattern` | `{type}-upstitch-{order_number}.pdf` | Output filename pattern |

**Filename placeholders:** `{order_id}`, `{order_number}`, `{date}` (YYYY-MM-DD), `{type}` (invoice / delivery-note)

### Lookback window logic (priority order)
1. `--since` CLI flag — absolute datetime, overrides everything
2. `lookback_from` in config — same, but persistent
3. `last_run - lookback_hours` — automatic, uses `.tmp/last_run.json`
4. `now - lookback_hours` — fallback for the very first run

### State file
`.tmp/last_run.json` stores the UTC timestamp of the last real (non-dry) run.
Delete it to force a full re-fetch using `lookback_hours` from now.

### Billbee Order State IDs
| ID | German | English |
|----|--------|---------|
| 1 | Angenommen | Received |
| 2 | Bestätigt | Confirmed |
| 3 | Versandfertig | Ready to ship |
| 4 | Versendet | Shipped |
| 5 | Reklamation | Complaint |
| 6 | Gelöscht | Deleted |
| 7 | Abgeschlossen | Completed |
| 8 | Storniert | Cancelled |
| 9 | Archiviert | Archived |
| 11 | Bezahlt | Paid |
| 12 | Offen | Open |
| 13 | Versandfertig u. bezahlt | Ready & Paid |

---

## Billbee API Endpoints Used

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/api/v1/orders` | List orders; params: `orderStateId`, `minOrderDate`, `page`, `pageSize` |
| POST | `/api/v1/orders/CreateInvoice/{BillBeeOrderId}` | Check if invoice exists in Billbee (no PDF); returns `ErrorCode: 3` if none yet |
| POST | `/api/v1/orders/CreateInvoice/{BillBeeOrderId}?includeInvoicePdf=true` | Download invoice PDF; response `{"Data": {"PDFData": "<base64>"}}` |
| POST | `/api/v1/orders/CreateDeliveryNote/{BillBeeOrderId}?includePdf=true` | Create/download delivery note PDF; same response shape |

**ID field**: always use `BillBeeOrderId` (integer) from the order object — NOT `Id` (external marketplace order number).

**IMPORTANT — `CreateInvoice` does NOT create invoices**: Despite the name, this endpoint is a **PDF renderer only**. It returns `ErrorCode: 3` ("Es wurde noch keine Rechnung für die Bestellung X erzeugt") if no invoice has been assigned in Billbee yet. The invoice record must exist first.

**How to ensure invoices exist**: Configure a Billbee automation rule:
> **Settings → Automatisierungen (Automation) → New rule**
> Trigger: Order enters state "Versandfertig" (3) — or whichever state you target
> Action: Create invoice ("Rechnung erstellen")

Once that rule is active, invoices are assigned automatically as orders reach that state, and this script can download the PDFs.

**Rate limit**: both document endpoints are extra-throttled to 1 request per 5 minutes per order+api-key.

---

## CLI Override Flags

```bash
# Override state, lookback, and output dir without editing config
.venv/bin/python execution/fetch_morning_documents.py \
  --state 4 \
  --days 7 \
  --outdir ~/Desktop/Billbee-Test \
  --dry-run
```

| Flag | Description |
|------|-------------|
| `--config PATH` | Use a different config file |
| `--dry-run` | List what would be fetched, don't download (does not update last_run) |
| `--state N` | Override `order_state_id` |
| `--since DATETIME` | Override lookback start (ISO 8601, e.g. `2026-02-01T06:00:00`) |
| `--no-log` | Don't write a `.tmp/logs/` file |

---

## Self-Annealing Notes

- **429 rate limit:** handled automatically by `BillbeeClient._request()` with exponential wait.
- **Document endpoint 404:** update `get_invoice_pdf()` or `get_delivery_note_pdf()` in
  `~/code/billbee-python-client/billbee_client.py`, then retest.
- **Empty PDFContent:** the order may not yet have a rendered document in Billbee.
  Check the order in the Billbee UI; you may need to trigger document generation first.
- **Phase 1 returns ErrorCode 3 for all orders:** `CreateInvoice` is a PDF renderer, not an invoice creator. The invoice record must be created in Billbee first. Configure a Billbee automation rule (Settings → Automatisierungen) to create invoices when orders enter your target state. Once the rule is active, invoices will exist and Phase 2 will download them successfully.
- **Already-existing files:** the script skips them (idempotent). Re-running is safe.
- **Missed days:** increase `lookback_days` in config, or pass `--days N` on the CLI.

---

## Uninstalling the LaunchAgent

```bash
launchctl unload ~/Library/LaunchAgents/com.billbee.morning-fetch.plist
rm ~/Library/LaunchAgents/com.billbee.morning-fetch.plist
```
