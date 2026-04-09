#!/usr/bin/env python3
"""
emailBot monitor — polls Gmail accounts, evaluates rules, and executes actions.

Usage:
  python monitor.py                  # one-shot: scan since last run (or 24 h back)
  python monitor.py --force          # ignore last-run state, scan 24 h back
  python monitor.py --since 2026-04-01  # scan from a specific date
  python monitor.py --daemon 5       # run in a loop every 5 minutes
"""

import argparse
import base64
import json
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import yaml
from googleapiclient.discovery import build

sys.path.insert(0, str(Path(__file__).parent))
from gmail_auth import get_credentials
from rule_engine import matches_conditions
from actions import (
    execute_save_attachments,
    execute_run_command,
    execute_send_payslips,
)

ROOT = Path(__file__).parent.parent
CONFIG_FILE = ROOT / "config" / "rules.yaml"
STATE_FILE = ROOT / ".state.json"


# ---------------------------------------------------------------------------
# Config / state
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

def _header(payload: dict, name: str) -> str:
    for h in payload.get("headers", []):
        if h["name"].lower() == name.lower():
            return h["value"]
    return ""


def _collect_parts(payload: dict, text_parts: list[str], att_parts: list[dict]) -> None:
    """Recursively gather plain-text body parts and attachment parts."""
    mime = payload.get("mimeType", "")
    body = payload.get("body", {})
    filename = payload.get("filename", "")

    if mime == "text/plain" and not filename:
        data = body.get("data", "")
        if data:
            text_parts.append(base64.urlsafe_b64decode(data).decode("utf-8", errors="replace"))
    elif filename and (body.get("data") or body.get("attachmentId")):
        att_parts.append(payload)

    for part in payload.get("parts", []):
        _collect_parts(part, text_parts, att_parts)


def fetch_attachment_data(gmail, msg_id: str, part: dict) -> bytes:
    body = part.get("body", {})
    data = body.get("data")
    if not data:
        att_id = body["attachmentId"]
        att = gmail.users().messages().attachments().get(
            userId="me", messageId=msg_id, id=att_id
        ).execute()
        data = att["data"]
    return base64.urlsafe_b64decode(data)


def get_message_meta(gmail, msg_id: str) -> dict:
    """Fetch a message and return metadata + in-memory attachments."""
    msg = gmail.users().messages().get(userId="me", id=msg_id, format="full").execute()
    payload = msg["payload"]

    from_addr = _header(payload, "from")
    subject = _header(payload, "subject")

    text_parts: list[str] = []
    att_parts: list[dict] = []
    _collect_parts(payload, text_parts, att_parts)

    attachments = []
    for part in att_parts:
        fname = part.get("filename") or f"attachment_{msg_id}"
        data = fetch_attachment_data(gmail, msg_id, part)
        attachments.append({"filename": fname, "data": data})

    return {
        "id": msg_id,
        "from_addr": from_addr,
        "subject": subject,
        "body_text": "\n".join(text_parts),
        "attachments": attachments,
        "attachment_count": len(attachments),
        "has_attachments": len(attachments) > 0,
    }


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


# ---------------------------------------------------------------------------
# Action dispatcher
# ---------------------------------------------------------------------------

def run_actions(actions: list[dict], msg_meta: dict, gmail_service) -> None:
    saved_paths = []

    for action in actions:
        atype = action.get("type")

        if atype == "save_attachments":
            saved_paths = execute_save_attachments(action, msg_meta["attachments"])

        elif atype == "run_command":
            rc = execute_run_command(action, saved_paths)
            if rc != 0:
                print(f"    WARNING: command exited with code {rc}")

        elif atype == "send_payslips":
            count = execute_send_payslips(action, gmail_service)
            print(f"    Sent {count} payslip(s).")

        else:
            print(f"    Unknown action type: '{atype}' — skipping")


# ---------------------------------------------------------------------------
# Core polling
# ---------------------------------------------------------------------------

def process_rule(rule: dict, gmail, state: dict, after: date) -> int:
    """
    Poll Gmail for messages matching the rule since `after`.
    Execute actions for each new match.
    Returns count of newly processed messages.
    """
    name = rule.get("name", "(unnamed)")
    conditions = rule.get("conditions", {})
    account = rule["account"]

    # Build query — always include has:attachment when rule requires attachments
    query_parts = []
    if fc := conditions.get("from_contains"):
        query_parts.append(f"from:{fc}")
    if conditions.get("has_attachments") or conditions.get("min_attachments"):
        query_parts.append("has:attachment")
    query_parts.append(f"after:{after.strftime('%Y/%m/%d')}")
    query = " ".join(query_parts)

    print(f"\n[{name}] Query: {query}")

    messages = list_messages(gmail, query)
    processed_ids: set[str] = set(state.get("processed_ids", {}).get(name, []))
    new_count = 0

    for stub in messages:
        msg_id = stub["id"]
        if msg_id in processed_ids:
            continue

        msg_meta = get_message_meta(gmail, msg_id)

        if not matches_conditions(conditions, msg_meta):
            # Mark as seen so we don't re-evaluate it on next run
            processed_ids.add(msg_id)
            continue

        print(f"  MATCH  from={msg_meta['from_addr']!r}  subject={msg_meta['subject']!r}")
        run_actions(rule.get("actions", []), msg_meta, gmail)

        processed_ids.add(msg_id)
        new_count += 1

    # Persist processed IDs
    if "processed_ids" not in state:
        state["processed_ids"] = {}
    state["processed_ids"][name] = list(processed_ids)

    print(f"[{name}] Done. {new_count} new message(s) triggered.")
    return new_count


def run_once(config: dict, state: dict, after: date) -> None:
    rules = config.get("rules", [])

    # Group rules by account to reuse Gmail service instances
    by_account: dict[str, list[dict]] = {}
    for rule in rules:
        by_account.setdefault(rule["account"], []).append(rule)

    for account, account_rules in by_account.items():
        print(f"\n=== Account: {account} ===")
        creds = get_credentials(account)
        gmail = build("gmail", "v1", credentials=creds)

        for rule in account_rules:
            process_rule(rule, gmail, state, after)

    state["last_run"] = date.today().isoformat()
    save_state(state)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="emailBot — Gmail rule-based automation")
    parser.add_argument(
        "--since", metavar="YYYY-MM-DD",
        help="Scan from this date (historical mode)",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Ignore last-run state; scan the last 24 hours",
    )
    parser.add_argument(
        "--daemon", metavar="MINUTES", type=int,
        help="Run in a loop, polling every N minutes",
    )
    args = parser.parse_args()

    config = load_config()

    if args.daemon:
        interval = args.daemon * 60
        print(f"Daemon mode: polling every {args.daemon} minute(s). Ctrl-C to stop.")
        while True:
            state = load_state()
            after = _resolve_after(state, args)
            run_once(config, state, after)
            print(f"\nSleeping {args.daemon} minute(s)…")
            time.sleep(interval)
    else:
        state = load_state()
        after = _resolve_after(state, args)
        run_once(config, state, after)


def _resolve_after(state: dict, args) -> date:
    if args.since:
        return date.fromisoformat(args.since)
    if args.force or not state.get("last_run"):
        return date.today() - timedelta(days=1)
    return date.fromisoformat(state["last_run"])


if __name__ == "__main__":
    main()
