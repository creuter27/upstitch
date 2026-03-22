"""
Persistent feedback store for address fix decisions.

Appends JSONL records to data/feedback.jsonl.
Used for:
  1. Logging every user decision (accept / reject / edit)
  2. Providing few-shot examples to the Claude fix suggester
  3. Future analysis / ML training data

Schema per line:
{
  "timestamp": "2026-02-25T10:30:00Z",
  "order_id": 12345,
  "order_number": "2024-001",
  "original": { ...ShippingAddress fields... },
  "suggested": { ...fields suggested by rules+LLM... },
  "accepted": true,
  "user_edit": null,         # if user edited manually: the final applied fix dict
  "user_note": null          # free-text note entered by user on rejection
}

Usage:
    from execution.feedback_store import append, get_recent_accepted
    append(entry)
    examples = get_recent_accepted(n=20)
"""

import json
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"
FEEDBACK_FILE = DATA_DIR / "feedback.jsonl"


def append(entry: dict) -> None:
    """Append one feedback record to feedback.jsonl."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if "timestamp" not in entry:
        entry["timestamp"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with open(FEEDBACK_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def get_recent_accepted(n: int = 20) -> list[dict]:
    """Return the N most recent accepted feedback entries."""
    if not FEEDBACK_FILE.exists():
        return []
    entries = []
    with open(FEEDBACK_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                if entry.get("accepted"):
                    entries.append(entry)
            except json.JSONDecodeError:
                pass
    return entries[-n:]


def get_all() -> list[dict]:
    """Return all feedback entries."""
    if not FEEDBACK_FILE.exists():
        return []
    entries = []
    with open(FEEDBACK_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return entries
