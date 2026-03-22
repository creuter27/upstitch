"""
Install or update a crontab entry that runs extract_attachments.py at the
time configured in config.yaml.

Usage:
  python setup_cron.py          # install/update
  python setup_cron.py --remove # remove the cron entry
"""

import argparse
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent
CONFIG_FILE = ROOT / "config.yaml"
VENV_PYTHON = ROOT / ".venv" / "bin" / "python"
SCRIPT = ROOT / "execution" / "extract_attachments.py"
LOG_FILE = ROOT / ".tmp" / "cron.log"
MARKER = "# gmailAttachmentExtractor"


def load_schedule() -> tuple[str, list[str] | str]:
    with open(CONFIG_FILE) as f:
        cfg = yaml.safe_load(f)
    sched = cfg.get("schedule", {})
    return sched.get("time", "08:00"), sched.get("days", "daily")


def time_to_cron(time_str: str, days) -> str:
    """Convert HH:MM + days config to a cron schedule expression."""
    h, m = time_str.split(":")
    if days == "daily":
        dow = "*"
    else:
        # Convert day names to cron numbers (0=Sun, 1=Mon, ...)
        day_map = {
            "Sunday": 0, "Monday": 1, "Tuesday": 2, "Wednesday": 3,
            "Thursday": 4, "Friday": 5, "Saturday": 6,
        }
        dow = ",".join(str(day_map[d]) for d in days if d in day_map)
    return f"{m} {h} * * {dow}"


def get_crontab() -> str:
    result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    if result.returncode != 0:
        return ""
    return result.stdout


def set_crontab(content: str) -> None:
    proc = subprocess.run(["crontab", "-"], input=content, text=True, capture_output=True)
    if proc.returncode != 0:
        print(f"crontab error: {proc.stderr}")
        sys.exit(1)


def remove_entry(crontab: str) -> str:
    lines = [l for l in crontab.splitlines() if MARKER not in l]
    return "\n".join(lines).strip() + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--remove", action="store_true", help="Remove the cron entry")
    args = parser.parse_args()

    if not VENV_PYTHON.exists():
        print(f"venv not found at {VENV_PYTHON}. Run: python3.14 -m venv .venv && .venv/bin/pip install -r requirements.txt")
        sys.exit(1)

    current = get_crontab()

    if args.remove:
        new_crontab = remove_entry(current)
        set_crontab(new_crontab)
        print("Cron entry removed.")
        return

    time_str, days = load_schedule()
    schedule_expr = time_to_cron(time_str, days)

    LOG_FILE.parent.mkdir(exist_ok=True)
    cron_line = (
        f"{schedule_expr} {VENV_PYTHON} {SCRIPT} >> {LOG_FILE} 2>&1 {MARKER}"
    )

    new_crontab = remove_entry(current)
    new_crontab = new_crontab.rstrip("\n") + "\n" + cron_line + "\n"
    set_crontab(new_crontab)
    print(f"Cron entry installed:\n  {cron_line}")


if __name__ == "__main__":
    main()
