"""
Action handlers for emailBot rules.

Supported action types (configured in rules.yaml):
  save_attachments   — save email attachments to a local folder
  run_command        — run a shell command (with optional cwd)
  send_payslips      — find Lohn_*.pdf files and send them to employees by email
"""

import base64
import re
import subprocess
import yaml
from datetime import date
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------

def _prev_month_tag() -> str:
    """Return 'yy-mm' for the calendar month before today, e.g. '26-03'."""
    today = date.today()
    month = today.month - 1
    year = today.year
    if month == 0:
        month = 12
        year -= 1
    return f"{year % 100:02d}-{month:02d}"


def _month_label_de(tag: str) -> str:
    """Convert 'yy-mm' tag to German month label, e.g. '26-03' → 'März 2026'."""
    months = [
        "Januar", "Februar", "März", "April", "Mai", "Juni",
        "Juli", "August", "September", "Oktober", "November", "Dezember",
    ]
    yy, mm = tag.split("-")
    return f"{months[int(mm) - 1]} 20{yy}"


def _expand_path(template: str) -> Path:
    """Expand {prev_month} placeholder and ~ in a path template."""
    return Path(template.replace("{prev_month}", _prev_month_tag())).expanduser()


# ---------------------------------------------------------------------------
# save_attachments
# ---------------------------------------------------------------------------

def execute_save_attachments(
    action_cfg: dict,
    attachments: list[dict],
) -> list[Path]:
    """
    Save in-memory attachment bytes to the configured folder.

    Config keys:
      folder            str  path, may contain {prev_month}
      create_if_missing bool (default true)

    attachments: list of {"filename": str, "data": bytes}

    Returns list of saved file paths.
    """
    folder = _expand_path(action_cfg["folder"])
    if action_cfg.get("create_if_missing", True):
        folder.mkdir(parents=True, exist_ok=True)

    saved: list[Path] = []
    for att in attachments:
        out_path = folder / att["filename"]
        out_path.write_bytes(att["data"])
        print(f"    Saved: {out_path}")
        saved.append(out_path)

    return saved


# ---------------------------------------------------------------------------
# run_command
# ---------------------------------------------------------------------------

def execute_run_command(
    action_cfg: dict,
    saved_paths: list[Path],
) -> int:
    """
    Run a shell command.

    Config keys:
      command      str  shell command; {attachments} is replaced with
                        space-joined quoted file paths; {prev_month} is expanded
      working_dir  str  (optional) cwd; {prev_month} expanded; defaults to
                        the parent of the first saved file

    Returns the process return code.
    """
    tag = _prev_month_tag()
    att_str = " ".join(f'"{p}"' for p in saved_paths)
    cmd = (
        action_cfg["command"]
        .replace("{attachments}", att_str)
        .replace("{prev_month}", tag)
    )

    if wd := action_cfg.get("working_dir"):
        cwd = _expand_path(wd)
    elif saved_paths:
        cwd = saved_paths[0].parent.parent  # parent of month subfolder = Lohnabrechnungen/
    else:
        cwd = Path(".")

    print(f"    Command: {cmd}")
    print(f"    CWD:     {cwd}")
    result = subprocess.run(cmd, shell=True, cwd=cwd)
    return result.returncode


# ---------------------------------------------------------------------------
# send_payslips
# ---------------------------------------------------------------------------

def execute_send_payslips(
    action_cfg: dict,
    gmail_service: Any,
) -> int:
    """
    Find Lohn_<Name>_<yy-mm>.pdf files produced by split_payroll.py and
    send each to the matching employee's email address.

    Config keys:
      folder            str  where to look for Lohn_*.pdf (may contain {prev_month})
      employees_config  str  path to Geh-Abr-Splitter/config/employees.yaml
                             (relative paths resolved from this file's parent)
      sender            str  Gmail 'From' address
      subject           str  (optional) email subject; {month_label} expanded
      body              str  (optional) plain-text body; {month_label} and {name} expanded

    Returns count of emails sent.
    """
    tag = _prev_month_tag()
    folder = _expand_path(action_cfg["folder"])

    # Resolve employees_config path
    emp_cfg_raw = action_cfg["employees_config"]
    emp_cfg_path = Path(emp_cfg_raw).expanduser()
    if not emp_cfg_path.is_absolute():
        emp_cfg_path = (Path(__file__).parent.parent.parent / emp_cfg_raw).resolve()

    with open(emp_cfg_path) as f:
        emp_cfg = yaml.safe_load(f)

    # Build normalized_name → email lookup
    # "Martina Riedenauer" → key "martina_riedenauer"
    name_to_email: dict[str, str] = {}
    for emp in emp_cfg.get("employees", []):
        key = "_".join(emp["name"].split()).lower()
        name_to_email[key] = emp["email"]

    sender = action_cfg["sender"]
    ml = _month_label_de(tag)
    subject_tpl = action_cfg.get("subject", "Lohnabrechnung {month_label}")
    body_tpl = action_cfg.get(
        "body",
        "Sehr geehrte/r {name},\n\nanbei finden Sie Ihre Lohnabrechnung für {month_label}.\n\nMit freundlichen Grüßen",
    )

    payslip_files = sorted(folder.glob(f"Lohn_*_{tag}.pdf"))
    if not payslip_files:
        print(f"    No payslip files found in {folder} matching Lohn_*_{tag}.pdf")
        return 0

    sent = 0
    for pdf_path in payslip_files:
        # Filename: Lohn_Martina_Riedenauer_26-03.pdf
        # Extract name: strip "Lohn_" prefix and "_yy-mm" suffix
        stem = pdf_path.stem
        inner = re.sub(r"^Lohn_", "", stem)
        inner = re.sub(r"_\d{2}-\d{2}$", "", inner)
        # inner = "Martina_Riedenauer"

        emp_email = name_to_email.get(inner.lower())
        if not emp_email:
            print(f"    WARNING: No email found for '{inner}' — skipping {pdf_path.name}")
            continue

        emp_name = inner.replace("_", " ")
        subject = subject_tpl.replace("{month_label}", ml)
        body = (
            body_tpl
            .replace("{month_label}", ml)
            .replace("{name}", emp_name)
        )

        _send_gmail(gmail_service, sender, emp_email, subject, body, pdf_path)
        print(f"    Sent {pdf_path.name} → {emp_email}")
        sent += 1

    return sent


def _send_gmail(
    service: Any,
    sender: str,
    to: str,
    subject: str,
    body: str,
    attachment: Path,
) -> None:
    """Send a single email with one PDF attachment via the Gmail API."""
    msg = MIMEMultipart()
    msg["From"] = sender
    msg["To"] = to
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))

    part = MIMEBase("application", "pdf")
    part.set_payload(attachment.read_bytes())
    encoders.encode_base64(part)
    part.add_header(
        "Content-Disposition",
        f'attachment; filename="{attachment.name}"',
    )
    msg.attach(part)

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
    service.users().messages().send(userId="me", body={"raw": raw}).execute()
