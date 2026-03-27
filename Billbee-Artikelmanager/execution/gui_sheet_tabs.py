#!/usr/bin/env python3
"""
Return the tab (worksheet) names of a Google Sheet.

Args:
  --sheet  Google Sheet name

Output: JSON {"tabs": ["Tab1", "Tab2", ...], "error": null}
"""
import argparse
import json
import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
_repo = os.path.dirname(os.path.dirname(_here))
sys.path.insert(0, _here)
sys.path.insert(0, os.path.join(_repo, "google-client"))

from google_sheets_client import open_sheet_by_name  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sheet", required=True, help="Google Sheet name")
    args = parser.parse_args()

    try:
        ss = open_sheet_by_name(args.sheet)
        tabs = [ws.title for ws in ss.worksheets()]
        print(json.dumps({"tabs": tabs, "error": None}, ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({"tabs": [], "error": str(exc)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
