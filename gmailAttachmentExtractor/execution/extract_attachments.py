"""
Extract PDF attachments from configured Gmail mailboxes and upload to Google Drive.

Usage:
  # Regular run (respects schedule.days from config.yaml):
  python extract_attachments.py

  # Force run ignoring day-of-week check:
  python extract_attachments.py --force

  # Historical scan for a date range:
  python extract_attachments.py --from 2024-01-01 --to 2024-12-31

  # Historical scan, single mailbox:
  python extract_attachments.py --from 2024-01-01 --mailbox "Supplier Invoices"
"""

import argparse
import base64
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import yaml
from googleapiclient.discovery import build
from googleapiclient.http import MediaInMemoryUpload

sys.path.insert(0, str(Path(__file__).parent))
from gmail_auth import get_credentials

ROOT = Path(__file__).parent.parent
CONFIG_FILE = ROOT / "config.yaml"
STATE_FILE = ROOT / ".state.json"


# ---------------------------------------------------------------------------
# State helpers
# ---------------------------------------------------------------------------

def load_config() -> dict:
    with open(CONFIG_FILE) as f:
        return yaml.safe_load(f)


def load_state() -> dict:
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            return json.load(f)
    return {}


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


# ---------------------------------------------------------------------------
# Gmail helpers
# ---------------------------------------------------------------------------

def list_messages(gmail, query: str) -> list[dict]:
    """Return all message stubs matching a Gmail query (handles pagination)."""
    results = gmail.users().messages().list(userId="me", q=query, maxResults=500).execute()
    messages = results.get("messages", [])
    while "nextPageToken" in results:
        results = gmail.users().messages().list(
            userId="me", q=query, maxResults=500, pageToken=results["nextPageToken"]
        ).execute()
        messages.extend(results.get("messages", []))
    return messages


def _walk_parts(payload: dict, collected: list) -> None:
    """Recursively collect PDF attachment parts from a message payload."""
    mime = payload.get("mimeType", "")
    filename = payload.get("filename", "")
    body = payload.get("body", {})
    # A part is a PDF attachment if it has a filename ending in .pdf
    # (mimeType can be application/pdf or application/octet-stream)
    if filename.lower().endswith(".pdf") and (body.get("data") or body.get("attachmentId")):
        collected.append(payload)
    for part in payload.get("parts", []):
        _walk_parts(part, collected)


def get_pdf_attachments(gmail, msg_id: str) -> list[dict]:
    """Return list of {filename, data: bytes} for all PDF attachments in a message."""
    msg = gmail.users().messages().get(userId="me", id=msg_id, format="full").execute()
    parts: list[dict] = []
    _walk_parts(msg["payload"], parts)

    results = []
    for part in parts:
        filename = part.get("filename") or f"attachment_{msg_id}.pdf"
        if not filename.lower().endswith(".pdf"):
            filename += ".pdf"

        body = part.get("body", {})
        data = body.get("data")

        if not data:
            att_id = body.get("attachmentId")
            if att_id:
                att = gmail.users().messages().attachments().get(
                    userId="me", messageId=msg_id, id=att_id
                ).execute()
                data = att.get("data")

        if data:
            pdf_bytes = base64.urlsafe_b64decode(data)
            results.append({"filename": filename, "data": pdf_bytes})

    return results


# ---------------------------------------------------------------------------
# Drive helpers
# ---------------------------------------------------------------------------

def upload_to_drive(drive, folder_id: str, filename: str, data: bytes) -> str:
    """Upload PDF bytes to a Drive folder; return new file ID."""
    media = MediaInMemoryUpload(data, mimetype="application/pdf", resumable=False)
    file_meta = {"name": filename, "parents": [folder_id]}
    f = drive.files().create(body=file_meta, media_body=media, fields="id").execute()
    return f["id"]


# ---------------------------------------------------------------------------
# Core extraction
# ---------------------------------------------------------------------------

def extract_mailbox(
    gmail,
    drive,
    mailbox_cfg: dict,
    after: date,
    before: date | None,
    extracted_ids: set,
) -> list[str]:
    """
    Extract PDF attachments from one mailbox config for the given date range.
    Skips message IDs already in extracted_ids.
    Returns list of newly processed message IDs.
    """
    label = mailbox_cfg["label"]
    folder_id = mailbox_cfg["drive_folder_id"]
    base_query = mailbox_cfg.get("query", "has:attachment filename:pdf")

    date_part = f"after:{after.strftime('%Y/%m/%d')}"
    if before:
        date_part += f" before:{before.strftime('%Y/%m/%d')}"

    full_query = f"({base_query}) {date_part} has:attachment"
    print(f"\n[{label}] Query: {full_query}")

    messages = list_messages(gmail, full_query)
    print(f"[{label}] {len(messages)} messages found, {len(extracted_ids)} already extracted")

    new_ids = []
    for msg in messages:
        msg_id = msg["id"]
        if msg_id in extracted_ids:
            continue

        attachments = get_pdf_attachments(gmail, msg_id)
        if not attachments:
            # Message matched but had no extractable PDF (e.g. inline image named .pdf)
            new_ids.append(msg_id)  # still mark as seen so we don't re-check it
            continue

        for att in attachments:
            drive_id = upload_to_drive(drive, folder_id, att["filename"], att["data"])
            print(f"[{label}] Uploaded '{att['filename']}' → Drive {drive_id}")

        new_ids.append(msg_id)

    return new_ids


# ---------------------------------------------------------------------------
# Schedule check
# ---------------------------------------------------------------------------

def is_scheduled_today(schedule_cfg: dict) -> bool:
    days = schedule_cfg.get("days", "daily")
    if days == "daily":
        return True
    today = datetime.now().strftime("%A")  # e.g. "Monday"
    return today in days


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Extract Gmail PDF attachments to Drive")
    parser.add_argument("--from", dest="from_date", metavar="YYYY-MM-DD",
                        help="Historical start date (enables historical mode)")
    parser.add_argument("--to", dest="to_date", metavar="YYYY-MM-DD",
                        help="Historical end date (default: today)")
    parser.add_argument("--mailbox", metavar="LABEL",
                        help="Only process this mailbox label")
    parser.add_argument("--force", action="store_true",
                        help="Run even if today is not a scheduled day")
    args = parser.parse_args()

    config = load_config()
    state = load_state()
    historical_mode = bool(args.from_date)

    if historical_mode:
        after = date.fromisoformat(args.from_date)
        before = date.fromisoformat(args.to_date) if args.to_date else None
        print(f"Historical mode: {after} → {before or 'today'}")
    else:
        schedule_cfg = config.get("schedule", {})
        if not args.force and not is_scheduled_today(schedule_cfg):
            today = datetime.now().strftime("%A")
            print(f"Not scheduled today ({today}). Use --force to override.")
            sys.exit(0)

        last_run_str = state.get("last_run")
        after = date.fromisoformat(last_run_str) if last_run_str else date.today() - timedelta(days=1)
        before = None
        print(f"Regular run. Scanning from {after}.")

    creds = get_credentials()
    gmail = build("gmail", "v1", credentials=creds)
    drive = build("drive", "v3", credentials=creds)

    mailboxes = config.get("mailboxes", [])
    if args.mailbox:
        mailboxes = [m for m in mailboxes if m["label"] == args.mailbox]
        if not mailboxes:
            print(f"No mailbox with label '{args.mailbox}' found in config.")
            sys.exit(1)

    for mailbox_cfg in mailboxes:
        label = mailbox_cfg["label"]
        extracted_ids = set(state.get("extracted_ids", {}).get(label, []))

        new_ids = extract_mailbox(gmail, drive, mailbox_cfg, after, before, extracted_ids)

        if "extracted_ids" not in state:
            state["extracted_ids"] = {}
        state["extracted_ids"][label] = list(extracted_ids | set(new_ids))
        print(f"[{label}] Done. {len(new_ids)} new messages processed.")

    if not historical_mode:
        state["last_run"] = date.today().isoformat()

    save_state(state)
    print("\nAll done.")


if __name__ == "__main__":
    main()
