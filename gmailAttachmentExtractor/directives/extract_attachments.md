# Directive: Extract Gmail PDF Attachments to Google Drive

## Goal
Periodically scan configured Gmail mailboxes for new emails with PDF attachments
and upload those PDFs to designated Google Drive folders. One Drive folder per mailbox.

## Inputs
- `config.yaml` — mailbox queries, Drive folder IDs, schedule (days + time)
- `.state.json` — auto-managed; tracks last run date and already-processed message IDs per mailbox

## Tools/Scripts
- `execution/extract_attachments.py` — main extraction script
- `execution/gmail_auth.py` — OAuth helper (separate token from shared google-client)
- `execution/setup_cron.py` — installs/updates the crontab entry

## Setup (first time)

```bash
cd path/to/gmailAttachmentExtractor
bash setup.sh
```

`setup.sh` creates `.venv/` and installs all dependencies. Re-run it any time to
rebuild the venv (e.g. after moving the project to a new path).

First run will open a browser for Gmail OAuth consent. This creates `.token.json`
with gmail.readonly + drive scopes. It is separate from the sibling `google-client/token.json`.

**Enable APIs in Google Cloud Console** (same project as credentials.json):
- Gmail API
- Google Drive API

## Configuration

Edit `config.yaml`:

```yaml
schedule:
  days: daily            # or ["Monday", "Wednesday", "Friday"]
  time: "08:00"

mailboxes:
  - label: "Supplier Invoices"
    query: "from:invoices@supplier.com has:attachment filename:pdf"
    drive_folder_id: "FOLDER_ID_FROM_DRIVE_URL"
```

Drive folder ID: open the Drive folder, copy the last segment of the URL:
`https://drive.google.com/drive/folders/<THIS_PART>`

After editing config, re-run `python execution/setup_cron.py` to update the cron schedule.

## Regular Runs

Install the cron job (reads time/days from config.yaml):
```bash
.venv/bin/python execution/setup_cron.py
```

Remove the cron job:
```bash
.venv/bin/python execution/setup_cron.py --remove
```

Run manually (respects day-of-week schedule):
```bash
.venv/bin/python execution/extract_attachments.py
```

Force run regardless of schedule:
```bash
.venv/bin/python execution/extract_attachments.py --force
```

## Historical Scan

Scan all emails in a date range (ignores .state.json tracking, re-uploads nothing
already seen — extracted_ids are still checked):

```bash
# All mailboxes for a date range
.venv/bin/python execution/extract_attachments.py --from 2024-01-01 --to 2024-12-31

# Single mailbox
.venv/bin/python execution/extract_attachments.py --from 2024-01-01 --mailbox "Supplier Invoices"

# From a date to today
.venv/bin/python execution/extract_attachments.py --from 2024-01-01
```

Historical mode does NOT update `last_run` in `.state.json`, so the next regular
run continues from where it left off.

## Outputs
- PDF files uploaded to the configured Drive folders
- `.state.json` updated with `last_run` date and set of processed message IDs per mailbox
- `.tmp/cron.log` — cron job log output

## Edge Cases & Notes
- **Deduplication**: processed Gmail message IDs are stored in `.state.json`.
  A message is never uploaded twice even if re-queried.
- **Pagination**: Gmail API returns max 500 messages per call; the script pages through all results.
- **Attachments per message**: all PDFs in a single message are uploaded individually.
- **Empty responses**: messages that match the query but have no extractable PDF are still
  marked as seen in `.state.json` so they aren't re-checked on subsequent runs.
- **Gmail query syntax**: same as the Gmail search box. Test your query in Gmail first.
  `has:attachment filename:pdf` is automatically appended to every query.
- **Rate limits**: Gmail API has a 10,000 units/day quota. Each `messages.get` call = 5 units.
  For large historical scans with thousands of messages, space them out or batch over multiple runs.
- **Drive duplicates**: the script does not check if a file with the same name already exists
  in the Drive folder — it always uploads. Deduplication is done at the Gmail message ID level.

## State File Schema
```json
{
  "last_run": "2026-03-13",
  "extracted_ids": {
    "Supplier Invoices": ["msg_id_1", "msg_id_2"],
    "Bank Statements": ["msg_id_3"]
  }
}
```
