"""
Fix Billbee delivery addresses — interactive terminal tool.

Fetches new orders since the last run, checks their delivery addresses for
common errors, suggests fixes using rule-based checks + OpenCage + Claude API,
and lets you accept/reject/edit each fix interactively.

Accepted fixes are applied to Billbee via the API.
All decisions are logged to data/feedback.jsonl to improve future suggestions.

Usage:
    .venv/bin/python main.py
    .venv/bin/python main.py --dry-run
    .venv/bin/python main.py --since 2026-01-01
    .venv/bin/python main.py --skip-geocode
"""

import argparse
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box
from rich.text import Text
from rich.prompt import Confirm, Prompt

# Project imports
sys.path.insert(0, str(Path(__file__).parent))
from execution.billbee_client import BillbeeClient
from execution.fetch_new_orders import fetch_orders_since, load_fetch_config, compute_since, save_last_run
from execution.check_address import (
    check as check_address,
    parse_housenumber_at_start,
    parse_street_housenumber_floor,
    strip_zip_prefix,
)
from execution.geocode_address import geocode
from execution.suggest_fix import suggest_fix
from execution.update_address import apply_fix
from execution import feedback_store
from execution import product_cache as _product_cache
from execution.resolve_order_items import resolve_items
from execution.package_type_store import (
    fetch_package_types, combo_key, get_for_combo, set_for_combo,
)

# Load project-specific vars (OPENCAGE_API_KEY, ANTHROPIC_API_KEY) from this project's .env.
# Billbee API credentials are NOT stored here — they live in the sibling
# billbee-python-client/.env and are loaded automatically by BillbeeClient.
load_dotenv(Path(__file__).parent / ".env")

_PROJECT_ROOT = Path(__file__).parent


def _resolve_folder(path_str: str) -> Path:
    """Resolve a folder path from settings. Relative paths are resolved from the project root."""
    p = Path(path_str).expanduser()
    if not p.is_absolute():
        p = _PROJECT_ROOT / p
    return p

_IS_WIN = sys.platform == "win32"
# On Windows, force ANSI colour support (bat files don't advertise a colour terminal
# to Rich by default, which causes highlighting to be stripped).
console = Console(force_terminal=_IS_WIN or None)


def _e(emoji: str, fallback: str = "") -> str:
    """Return emoji on Unix, ASCII fallback on Windows."""
    return fallback if _IS_WIN else emoji


# Pre-built Windows-safe variants of common symbols
_OK   = _e("✓", "OK")
_FAIL = _e("✗", "X")
_WARN = _e("⚠", "!")

# ── Pending changes queue ────────────────────────────────────────────────────
# All Billbee writes are deferred until the user confirms at the end of the run.
_pending_changes: list[dict] = []


_PENDING_CASES_FILE = Path(__file__).parent / "tests" / "pending_cases.yaml"

_ADDR_FIELDS_FOR_CASES = [
    "FirstName", "LastName", "Company", "Street", "HouseNumber",
    "AddressAddition", "Zip", "City", "CountryISO2",
]


def _save_pending_case(
    order_number: str,
    addr: dict,
    outcome: str,
    *,
    system_suggestion: dict | None = None,
    user_edit: dict | None = None,
) -> None:
    """
    Append one address case to tests/pending_cases.yaml for later analysis.

    outcome: 'rejected' | 'manually_edited'
    """
    # Keep only relevant address fields (drop empty values for readability)
    addr_clean = {k: addr[k] for k in _ADDR_FIELDS_FOR_CASES
                  if k in addr and addr[k] not in (None, "")}

    case: dict = {
        "description": f"Auto: Order #{order_number} ({addr_clean.get('CountryISO2', '?')})",
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "outcome": outcome,
        "addr": addr_clean,
        # These will be filled in when working through the list:
        "expected_issues": [],
        "expected_fix": {},
    }
    if system_suggestion:
        case["system_suggestion"] = system_suggestion
    if user_edit:
        case["user_edit"] = user_edit

    _PENDING_CASES_FILE.parent.mkdir(parents=True, exist_ok=True)

    # Load existing cases (if any), append, save
    existing: list = []
    if _PENDING_CASES_FILE.exists():
        try:
            with open(_PENDING_CASES_FILE, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            existing = data.get("cases", []) or []
        except Exception:
            pass

    existing.append(case)

    with open(_PENDING_CASES_FILE, "w", encoding="utf-8") as f:
        yaml.dump(
            {"cases": existing},
            f,
            allow_unicode=True,
            default_flow_style=False,
            sort_keys=False,
        )


def _queue_address_fix(order_id, order_number: str, fix: dict, *,
                       geo: dict = None, orig_addr: dict = None) -> None:
    """Queue an address fix for deferred application."""
    parts = []
    for k, v in fix.items():
        parts.append(f"{k}: {v}")
    _pending_changes.append({
        "type": "address",
        "order_id": order_id,
        "order_number": order_number,
        "fix": fix,
        "summary": "  |  ".join(parts),
        "geo": geo,
        "orig_addr": orig_addr,
    })


def _queue_package_type(order_id, order_number: str, order: dict,
                        pkg_id, pkg_name: str) -> None:
    """Queue a package-type tag for deferred application."""
    _pending_changes.append({
        "type": "package_type",
        "order_id": order_id,
        "order_number": order_number,
        "order": order,
        "pkg_id": pkg_id,
        "pkg_name": pkg_name,
        "summary": f"Package type → {pkg_name}",
    })


def _show_pending_summary() -> None:
    """Print the list of queued changes."""
    if not _pending_changes:
        return
    console.print(f"\n  [bold]Pending changes ({len(_pending_changes)}):[/]")
    for i, ch in enumerate(_pending_changes, 1):
        icon = _e("📍", "ADDR") if ch["type"] == "address" else _e("📦", "PKG")
        console.print(f"  {i}. [{ch['order_number']}]  {icon} {ch['summary']}")


def _edit_pending_by_order_index(order_idx_1based: int, orders: list) -> None:
    """
    Let the user manually edit the address of an order chosen by its loop index.
    Updates or creates the address entry in _pending_changes.
    """
    idx = order_idx_1based - 1
    if idx < 0 or idx >= len(orders):
        console.print(f"  [red]No order #{order_idx_1based} in this run.[/]")
        return

    order = orders[idx]
    order_id = order.get("BillBeeOrderId") or order.get("Id")
    order_number = order.get("OrderNumber") or str(order_id)
    addr = order.get("ShippingAddress", {})

    # Find any existing queued address fix for this order
    existing = next(
        (c for c in _pending_changes
         if c["type"] == "address" and c["order_id"] == order_id),
        None,
    )

    # Start from the already-queued state so the user can refine it
    base_addr = {**addr, **(existing["fix"] if existing else {})}
    geo = existing["geo"] if existing else None

    _display_order_header(order, idx=order_idx_1based, total=len(orders))
    # Show comparison table and OpenCage details before prompting
    _display_address_table(addr, base_addr)
    _display_geocode_components(geo)
    console.print()
    edits = _prompt_edit(base_addr)
    if not edits:
        console.print("  [dim]No changes.[/]")
        return

    new_fix = {**(existing["fix"] if existing else {}), **edits}
    if existing:
        existing["fix"] = new_fix
        existing["summary"] = "  |  ".join(f"{k}: {v}" for k, v in new_fix.items())
        console.print("  [dim]Updated.[/]")
    else:
        _queue_address_fix(order_id, order_number, new_fix)
        console.print("  [dim]Queued.[/]")


def _apply_all_pending(client, dry_run: bool) -> tuple[int, list[dict]]:
    """
    Write all queued changes to Billbee.
    Returns (n_applied, errors) where errors is a list of
    {"order_number": str, "operation": str, "error": str}.
    """
    applied = 0
    errors: list[dict] = []
    n = len(_pending_changes)
    console.print(f"\n[dim]Applying {n} change(s) to Billbee...[/]")
    for i, ch in enumerate(_pending_changes, 1):
        order_num = ch["order_number"]
        if ch["type"] == "address":
            console.print(f"  [{i}/{n}] {_e('📍', 'ADDR')} Order {order_num}  address ...", end=" ")
            try:
                ok = apply_fix(client, ch["order_id"], ch["fix"], dry_run=dry_run)
                console.print(f"[green]{_OK}[/]" if ok else f"[red]{_FAIL}[/]")
                if ok:
                    applied += 1
                else:
                    errors.append({"order_number": order_num, "operation": "address fix", "error": "apply_fix returned False"})
            except Exception as e:
                console.print(f"[red]{_FAIL}[/]")
                errors.append({"order_number": order_num, "operation": "address fix", "error": str(e)})
        elif ch["type"] == "package_type":
            console.print(f"  [{i}/{n}] {_e('📦', 'PKG')} Order {order_num}  {ch['pkg_name']} ...", end=" ")
            try:
                if not dry_run:
                    client.set_order_package_type(
                        ch["order_id"], ch["order"],
                        pkg_id=ch.get("pkg_id"), pkg_name=ch["pkg_name"],
                    )
                applied += 1
                console.print(f"[green]{_OK}[/]")
            except Exception as e:
                console.print(f"[red]{_FAIL}[/]")
                errors.append({"order_number": order_num, "operation": "package type", "error": str(e)})
    return applied, errors


# Issue codes that can be auto-fixed deterministically when geocode confidence == 10
_AUTO_FIX_CODES = {"ZIP_HAS_COUNTRY_PREFIX", "HOUSE_NUMBER_IN_STREET",
                   "HOUSE_NUMBER_AT_START_OF_STREET"}

# Address field display names and order
DISPLAY_FIELDS = [
    ("FirstName",       "First Name"),
    ("LastName",        "Last Name"),
    ("Company",         "Company"),
    ("Street",          "Street"),
    ("HouseNumber",     "House Number"),
    ("AddressAddition", "Addition"),
    ("Zip",             "ZIP"),
    ("City",            "City"),
    ("State",           "State"),
    ("CountryISO2",     "Country"),
]


def _flag(country: str) -> str:
    """Convert ISO2 country code to flag emoji, or bracketed code on Windows."""
    if _IS_WIN:
        return f"[{country.upper()}]" if country else ""
    try:
        return "".join(chr(0x1F1E6 + ord(c) - ord("A")) for c in country.upper())
    except Exception:
        return ""


def _order_name_flag(order: dict) -> tuple[str, str]:
    """Return (display_name, flag_emoji) for an order."""
    addr = order.get("ShippingAddress", {})
    name = " ".join(filter(None, [addr.get("FirstName", ""), addr.get("LastName", "")])).strip()
    country = addr.get("CountryISO2", "")
    flag = _flag(country) if country else ""
    return name, flag


def _display_order_header(order: dict, idx: int = 0, total: int = 0) -> None:
    name, flag = _order_name_flag(order)
    order_num = order.get("OrderNumber") or order.get("Id") or "?"
    counter = f"[dim][{idx}/{total}][/]  " if idx else ""
    console.print()
    console.rule(
        f"{counter}[bold cyan]Order #{order_num}[/]  [white]{name}[/]  {flag}",
        style="cyan",
    )


def _display_address_table(original: dict, suggested: dict) -> None:
    """Render a side-by-side comparison table of original vs. suggested address."""
    table = Table(box=box.SIMPLE_HEAD, show_header=True, header_style="bold white")
    table.add_column("Field", style="dim", width=14)
    table.add_column("Original", width=28)
    table.add_column("Suggested Fix", width=28)

    for field_key, field_label in DISPLAY_FIELDS:
        orig_val = original.get(field_key) or ""
        sugg_val = suggested.get(field_key, orig_val) or ""

        changed = sugg_val != orig_val
        orig_text = Text(orig_val or "(empty)", style="red" if not orig_val and changed else "")
        sugg_text = Text(
            sugg_val or "(empty)",
            style="bright_yellow bold" if changed else "dim",
        )
        table.add_row(field_label, orig_text, sugg_text)

    console.print(table)


def _display_issues(issues: list) -> None:
    for issue in issues:
        console.print(f"  [red]●[/] [bold]{issue.code}[/]: {issue.description}")


def _display_geocode(geo: dict | None) -> None:
    if not geo:
        return
    conf = geo.get("confidence", "?")
    fmt = geo.get("formatted", "N/A")
    color = "green" if conf and int(conf) >= 8 else "yellow" if conf and int(conf) >= 5 else "red"
    console.print(
        f"\n  [dim]OpenCage →[/] [bold]{fmt}[/] "
        f"[{color}](confidence: {conf}/10)[/]"
    )


_SUB_KEYS = (
    "village", "suburb", "quarter", "neighbourhood", "neighborhood",
    "hamlet", "locality", "city_district", "district",
)
_ADDR_KEYS = (
    "house_number", "road", "postcode", "city", "town", "municipality",
    "state", "country", "country_code",
)


def _display_geocode_components(geo: dict | None) -> None:
    """Show full OpenCage result with all components, highlighting sub-localities."""
    if not geo:
        return
    conf = geo.get("confidence", "?")
    fmt = geo.get("formatted", "N/A")
    color = "green" if conf and int(conf) >= 8 else "yellow" if conf and int(conf) >= 5 else "red"
    console.print(
        f"\n  [dim]OpenCage →[/] [bold]{fmt}[/] "
        f"[{color}](confidence: {conf}/10)[/]"
    )
    components = {k: v for k, v in (geo.get("components") or {}).items()
                  if not k.startswith("_") and v}
    if not components:
        return
    table = Table(box=box.SIMPLE, show_header=False, padding=(0, 2))
    table.add_column("key", style="dim", min_width=20)
    table.add_column("value", min_width=24)
    shown: set[str] = set()
    for k in _ADDR_KEYS:
        if k in components:
            table.add_row(k, str(components[k]))
            shown.add(k)
    sub_rows = [(k, str(components[k])) for k in _SUB_KEYS if k in components]
    if sub_rows:
        table.add_row("", "")
        for k, v in sub_rows:
            table.add_row(
                Text(k, style="bold yellow"),
                Text(v, style="bold yellow"),
            )
            shown.add(k)
    remaining = sorted((k, str(v)) for k, v in components.items() if k not in shown)
    if remaining:
        table.add_row("", "")
        for k, v in remaining:
            table.add_row(k, v)
    console.print(table)


def _prompt_choice(default: str = "s") -> str:
    """Prompt user for action. Returns 'y', 'n', 'e', 's', or 'q'."""
    console.print()
    raw = Prompt.ask(
        "  [bold green]\\[y][/] Accept  [bold red]\\[n][/] Reject  "
        "[bold yellow]\\[e][/] Edit  [bold dim]\\[s][/] Skip  [bold dim]\\[q][/] Quit",
        choices=["y", "n", "e", "s", "q"],
        default=default,
    )
    return raw.lower()


def _prompt_edit(original: dict) -> dict:
    """Let user manually edit address fields. Returns dict of changed fields."""
    console.print("\n  [yellow]Edit address fields (press Enter to keep current value):[/]")
    edits = {}
    for field_key, field_label in DISPLAY_FIELDS:
        current = original.get(field_key) or ""
        new_val = Prompt.ask(f"  {field_label}", default=current)
        if new_val != current:
            edits[field_key] = new_val
    return edits


def _prompt_reject_note() -> str:
    console.print()
    return Prompt.ask("  [dim]Describe the issue (optional, press Enter to skip)[/]", default="")


def _display_resolved_items(items: list[dict]) -> None:
    """Show a compact table of resolved physical items."""
    table = Table(box=box.SIMPLE_HEAD, show_header=True, header_style="bold white",
                  show_edge=False, padding=(0, 1))
    table.add_column("Manufacturer", style="cyan", width=14)
    table.add_column("Category", width=14)
    table.add_column("Size", width=12)
    table.add_column("Qty", width=4, justify="right")
    for it in items:
        table.add_row(
            it.get("manufacturer") or "—",
            it.get("category") or "—",
            it.get("size") or "—",
            str(int(it["quantity"]) if it["quantity"] == int(it["quantity"]) else it["quantity"]),
        )
    console.print(table)


def process_package_type(
    order: dict,
    order_number: str,
    resolved_items: list[dict],
    pkg_types: list[dict],
) -> str:
    """
    Handle the package type assignment for one order.

    Returns one of: 'auto', 'set', 'skipped', 'no_items'
    """
    if not resolved_items:
        return "no_items"

    key = combo_key(resolved_items)
    if not key:
        return "no_items"

    order_id = order.get("BillBeeOrderId") or order.get("Id")

    # Show items table
    console.print(f"\n  [bold]{_e('📦', 'PKG')} Items:[/]")
    _display_resolved_items(resolved_items)

    known = get_for_combo(resolved_items)
    if known:
        console.print(
            f"  [dim]{_e('📦', 'PKG')} Package ->[/] [bold cyan]{known['name']}[/]  [dim][auto][/]"
        )
        _queue_package_type(order_id, order_number, order,
                            pkg_id=known.get("id"), pkg_name=known["name"])
        return "auto"

    # Unknown combination — prompt user
    if not pkg_types:
        console.print(
            f"  [yellow]{_e('📦', 'PKG')} Unknown combination and no package types available.[/]\n"
            "  Edit data/package_types.yaml and rerun."
        )
        return "skipped"

    console.print(f"\n  [yellow]{_e('📦', 'PKG')} Unknown combination:[/] [dim]{key}[/]")
    console.print("  Select package type:")
    for i, pt in enumerate(pkg_types, 1):
        console.print(f"    [[bold]{i}[/]] {pt['name']}")
    console.print(f"    [[dim]s[/]] Skip")
    console.print()

    while True:
        raw = Prompt.ask("  Choice", default="s")
        if raw.lower() == "s":
            return "skipped"
        try:
            idx = int(raw) - 1
            if 0 <= idx < len(pkg_types):
                chosen = pkg_types[idx]
                break
        except ValueError:
            pass
        console.print("  [red]Invalid choice — enter a number or 's'[/]")

    set_for_combo(resolved_items, name=chosen["name"], pkg_id=chosen.get("id"))
    console.print(
        f"  [green]{_OK} Saved:[/] {key} -> [bold]{chosen['name']}[/]"
    )

    _queue_package_type(order_id, order_number, order,
                        pkg_id=chosen.get("id"), pkg_name=chosen["name"])
    return "set"


def _apply_with_verification(
    addr: dict,
    fix: dict,
    suggestion: dict,
    from_geocode: bool,
    skip_geocode: bool,
    order_id,
    order_number: str,
    geo: dict = None,
) -> str:
    """
    Optionally re-verify a fix with OpenCage, then queue it for batch apply.

    Skips re-verification when:
      - from_geocode is True (fix came directly from OpenCage — already trusted)
      - skip_geocode is True (geocoding disabled for this session)

    Returns 'fixed' or 'skipped'.
    """
    if not fix:
        console.print("  [dim]No changes made.[/]")
        return "skipped"

    if not from_geocode and not skip_geocode:
        new_addr = {**addr, **fix}
        console.print("\n  [dim]Re-verifying fixed address with OpenCage...[/]")
        geo_v = geocode(new_addr)
        _display_address_table(addr, new_addr)
        _display_geocode(geo_v)
        if geo_v and geo_v.get("confidence", 0) < 6:
            console.print(
                f"\n  [yellow]{_WARN} Geocode confidence still low after fix — "
                "address may still be incorrect.[/]"
            )
        console.print()
        raw = Prompt.ask(
            "  Confirm?  [bold green]\\[y][/] Queue  "
            "[bold yellow]\\[e][/] Edit  [bold dim]\\[s][/] Skip",
            choices=["y", "e", "s"],
            default="y",
        ).lower()
        if raw == "s":
            return "skipped"
        elif raw == "e":
            extra_edits = _prompt_edit({**addr, **fix})
            if extra_edits:
                fix = {**fix, **extra_edits}

    _queue_address_fix(order_id, order_number, fix, geo=geo, orig_addr=addr)
    console.print("  [dim]Queued.[/]")
    feedback_store.append({
        "order_id": order_id, "order_number": order_number,
        "original": dict(addr), "suggested": suggestion,
        "accepted": True,
        "user_edit": fix if fix != suggestion else None,
        "user_note": None,
    })
    return "fixed"


def _is_street_normalization(original: str, suggested: str) -> bool:
    """
    Return True when the only difference between two street names is a
    trivial formatting change: abbreviation expansion (Str. → Straße) or
    word-boundary spacing (Riswickerstraße → Riswicker Straße).

    Normalises by lowercasing, expanding "str." and "strasse" to "straße",
    then stripping all spaces and hyphens before comparing.
    """
    import re as _re

    def _norm(s: str) -> str:
        s = s.lower()
        s = _re.sub(r'str\.\s*', 'straße', s)   # "Hauptstr." and "Str. " — no \b needed
        s = _re.sub(r'(?<![a-zäöüß])str(?![a-zäöüß])', 'straße', s)  # standalone "str"
        s = _re.sub(r'strasse', 'straße', s)
        # Strip all non-word characters: spaces, hyphens, commas, periods, etc.
        s = _re.sub(r'[^\w]', '', s)
        return s

    return bool(original) and bool(suggested) and _norm(original) == _norm(suggested)


def _geocode_suggestion(addr: dict, geo: dict | None,
                        geo_company: dict | None = None) -> dict:
    """
    Build a fix dict from a high-confidence geocode result (confidence ≥ 8).

    Extracts street name, ZIP, and city from geocoder components and compares
    them (case-insensitively) to the original. Only fields that actually differ
    are included. HouseNumber is always kept from the original.
    """
    if not geo or geo.get("confidence", 0) < 8:
        return {}

    components = geo.get("components", {})
    fix = {}

    import re as _re

    road = (components.get("road") or components.get("street") or "").strip()
    orig_street = (addr.get("Street") or "").strip()
    if road and road.lower() != orig_street.lower():
        orig_is_verifiable = orig_street and not _re.match(r"^\d+[-–]?\d*[a-zA-Z]?\s*$", orig_street)
        if orig_is_verifiable:
            fix["Street"] = road
        elif geo_company:
            # Street is empty/pure-number: fall back to the company-as-street geocode.
            # Only suggest the street if OpenCage's road from that query matches the
            # company name (case/punctuation-insensitive) — confirming the company IS
            # the street.  If OpenCage returns a different road we can't verify it.
            comp_components = geo_company.get("components", {})
            comp_road = (comp_components.get("road") or comp_components.get("street") or "").strip()
            company_val = (addr.get("Company") or "").strip()
            if comp_road and company_val and geo_company.get("confidence", 0) >= 8:
                def _norm_street(s: str) -> str:
                    s = s.lower()
                    s = _re.sub(r"str\.\s*", "straße", s)
                    s = _re.sub(r"(?<![a-zäöüß])str(?![a-zäöüß])", "straße", s)
                    s = _re.sub(r"strasse", "straße", s)
                    s = _re.sub(r"[^\w]", "", s)
                    return s
                if _norm_street(comp_road) == _norm_street(company_val):
                    fix["Street"] = comp_road  # OpenCage-normalised casing

    postcode = (components.get("postcode") or "").strip()
    if postcode and postcode != (addr.get("Zip") or "").strip():
        fix["Zip"] = postcode

    city = (
        components.get("city") or
        components.get("town") or
        components.get("municipality") or
        ""
    ).strip()
    orig_city = (addr.get("City") or "").strip()
    if city and city.lower() != orig_city.lower():
        # Only suggest a city change if the original city is NOT already present in
        # OpenCage's formatted address. When the original is there, OpenCage confirmed
        # it's correct — the component mismatch is just a sub-locality detail
        # (e.g. hamlet "Sambach" vs postal city "Otterbach").
        formatted = geo.get("formatted", "").lower()
        if not orig_city or orig_city.lower() not in formatted:
            fix["City"] = city
            # Only move the original city to AddressAddition when OpenCage explicitly
            # identifies it as a sub-locality of the geocoded address (village, suburb,
            # hamlet, etc.).  For plain typo corrections the original should not be kept.
            if orig_city and not (addr.get("AddressAddition") or "").strip():
                orig_lower = orig_city.lower()
                # Find the first matching sub-locality value (original casing from OpenCage)
                # where the value (≥4 chars) appears as a substring in the customer's city.
                matched_sublocality = next(
                    (
                        (components.get(k) or "").strip()
                        for k in _SUB_KEYS + ("town",)
                        if len((components.get(k) or "").strip()) >= 4
                        and (components.get(k) or "").strip().lower() in orig_lower
                    ),
                    None,
                )
                if matched_sublocality:
                    fix["AddressAddition"] = matched_sublocality

    return fix


def _deterministic_suggestion(addr: dict, issues: list) -> dict:
    """
    Build a fix dict for issues that can be resolved without calling Claude.
    Currently handles:
      - HOUSE_NUMBER_IN_STREET: splits street into Street + HouseNumber (+ AddressAddition for floor)
      - HOUSE_NUMBER_IN_ADDITION: moves addition value to HouseNumber
    """
    fix = {}
    issue_codes = {i.code for i in issues}

    if "HOUSE_NUMBER_IN_STREET" in issue_codes:
        street = (addr.get("Street") or "").strip()
        parsed = parse_street_housenumber_floor(street)
        if parsed:
            clean_street, hn, floor = parsed
            fix["Street"] = clean_street
            fix["HouseNumber"] = hn
            # Only populate AddressAddition with floor text if the field is currently empty
            if floor and not (addr.get("AddressAddition") or "").strip():
                fix["AddressAddition"] = floor

    if "STREET_IS_HOUSE_NUMBER" in issue_codes and "HouseNumber" not in fix:
        street = (addr.get("Street") or "").strip()
        if street:
            fix["HouseNumber"] = street
            fix["Street"] = ""   # street name unknown — user must fill it in

    if "HOUSE_NUMBER_AT_START_OF_STREET" in issue_codes and "HouseNumber" not in fix:
        street = (addr.get("Street") or "").strip()
        parsed = parse_housenumber_at_start(street)
        if parsed:
            hn, clean_street = parsed
            fix["HouseNumber"] = hn
            fix["Street"] = clean_street

    if "HOUSE_NUMBER_IN_ADDITION" in issue_codes and "HouseNumber" not in fix:
        addition = (addr.get("AddressAddition") or "").strip()
        if addition:
            fix["HouseNumber"] = addition
            fix["AddressAddition"] = ""

    if "ZIP_HAS_COUNTRY_PREFIX" in issue_codes:
        stripped = strip_zip_prefix(addr.get("Zip") or "")
        if stripped:
            fix["Zip"] = stripped

    return fix


def process_order(
    order: dict,
    skip_geocode: bool,
    recent_feedback: list[dict],
    by_id: dict,
    by_sku: dict,
    field_defs: dict,
    pkg_types: list[dict],
    order_index: int = 0,
    order_count: int = 0,
    force_manual: bool = False,
) -> tuple[str, str]:
    """
    Process one order: check address + assign package type.
    Changes are queued in _pending_changes, not written to Billbee immediately.

    Returns (addr_result, pkg_result) where:
      addr_result: 'fixed' | 'rejected' | 'skipped' | 'no_issue'
      pkg_result:  'auto'  | 'set'      | 'skipped' | 'no_items'
    """
    addr = order.get("ShippingAddress", {})
    order_id = order.get("BillBeeOrderId") or order.get("Id")
    order_number = order.get("OrderNumber") or str(order_id)

    # -- Resolve order items upfront (needed to decide whether to show this order) --
    resolved_items = resolve_items(order, by_id, by_sku, field_defs)
    known_pkg = get_for_combo(resolved_items) if resolved_items else None
    needs_pkg_prompt = bool(resolved_items) and known_pkg is None

    # -- Address check --
    issues = check_address(addr)

    # Always geocode to verify the address exists, even when rules find nothing
    geo = None
    geo_suggestion: dict = {}
    geo_auto_apply = False  # True when confidence==10 fix can be applied without prompt
    if not skip_geocode:
        from execution.check_address import Issue
        geo = geocode(addr)

        # If street is empty or just a house number, the company field often holds
        # the real street name. Retry geocoding with Company as the street so
        # _geocode_suggestion() can verify whether the company IS the street.
        geo_company: dict | None = None
        _company = (addr.get("Company") or "").strip()
        _orig_street = (addr.get("Street") or "").strip()
        import re as _re_proc
        _street_is_useless = not _orig_street or bool(
            _re_proc.match(r"^\d+[-–]?\d*[a-zA-Z]?\s*$", _orig_street)
        )
        if _company and _street_is_useless:
            _alt_addr = {**addr, "Street": _company,
                         "HouseNumber": _orig_street or (addr.get("HouseNumber") or "")}
            geo_company = geocode(_alt_addr)

        if geo:
            conf = geo.get("confidence", 10)
            if conf < 6:
                issues.append(Issue(
                    code="LOW_GEOCODE_CONFIDENCE",
                    description=f"OpenCage confidence is only {conf}/10 — address may not exist.",
                    hint="Compare the formatted result with the original address.",
                ))
            else:
                # High confidence — check whether geocode found corrections
                geo_suggestion = _geocode_suggestion(addr, geo, geo_company=geo_company)
                if geo_suggestion:
                    _auto = False
                    if not force_manual:
                        if conf == 10 and not issues:
                            # Perfect confidence, no rule-based issues → auto-apply
                            _auto = True
                        elif conf == 10 and issues and all(i.code in _AUTO_FIX_CODES for i in issues):
                            # ZIP prefix / house-in-street + perfect geocode → auto-apply
                            det_fix = _deterministic_suggestion(addr, issues)
                            if det_fix:
                                _auto = True
                                geo_suggestion = {**geo_suggestion, **det_fix}
                                issues = []  # all issues covered
                        elif conf >= 9 and not issues:
                            street_only = set(geo_suggestion.keys()) == {"Street"}
                            has_city = "City" in geo_suggestion
                            if street_only and _is_street_normalization(
                                (addr.get("Street") or ""), geo_suggestion["Street"]
                            ):
                                # Street normalisation only (str.→Straße, word spacing,
                                # punctuation cleanup) → auto-apply
                                _auto = True
                            elif has_city:
                                # City correction (sub-locality → postal city), possibly
                                # with original city preserved in AddressAddition → auto-apply
                                _auto = True
                    if _auto:
                        geo_auto_apply = True
                    else:
                        corrections = ", ".join(f"{k} → '{v}'" for k, v in geo_suggestion.items())
                        issues.append(Issue(
                            code="GEOCODE_CORRECTION_AVAILABLE",
                            description=f"OpenCage suggests corrections: {corrections}.",
                            hint="Accept the OpenCage suggestion or edit manually.",
                        ))

    has_addr_issues = bool(issues)

    # If neither address issues nor unknown package type → silently auto-process
    if not has_addr_issues and not needs_pkg_prompt:
        pkg_result = "no_items"
        pkg_name = ""
        if resolved_items and known_pkg:
            _queue_package_type(order_id, order_number, order,
                                pkg_id=known_pkg.get("id"), pkg_name=known_pkg["name"])
            pkg_result = "auto"
            pkg_name = known_pkg["name"]
        name, flag = _order_name_flag(order)
        counter = f"[dim][{order_index}/{order_count}][/]  " if order_index else ""
        pkg_str = f"  [dim]{_e('📦', 'PKG')} {pkg_name}[/]" if pkg_name else ""
        if geo_auto_apply:
            _queue_address_fix(order_id, order_number, geo_suggestion,
                               geo=geo, orig_addr=dict(addr))
            feedback_store.append({
                "order_id": order_id, "order_number": order_number,
                "original": dict(addr), "suggested": geo_suggestion,
                "accepted": True, "user_edit": None, "user_note": None,
            })
            changes = "  |  ".join(
                f"{k}: [dim]{addr.get(k, '')}[/] → [cyan]{v}[/]"
                for k, v in geo_suggestion.items()
            )
            console.print(f"  {counter}[dim]{name} {flag}[/]  [cyan]auto-fixed[/] {changes}{pkg_str}")
            return "fixed", pkg_result
        else:
            street = (addr.get("Street") or "").strip()
            hn = (addr.get("HouseNumber") or "").strip()
            city = (addr.get("City") or "").strip()
            addr_short = ", ".join(filter(None, [f"{street} {hn}".strip(), city]))
            console.print(
                f"  {counter}[dim]{name} {flag}  {addr_short}[/]  [green]{_OK}[/]{pkg_str}"
            )
            return "no_issue", pkg_result

    # Need to show this order (has address issue or unknown package type)
    _display_order_header(order, idx=order_index, total=order_count)
    addr_result = "no_issue"

    # Auto-queue confidence-10 geocode fix (no rule-based issues, but pkg type needs prompt)
    if geo_auto_apply:
        _queue_address_fix(order_id, order_number, geo_suggestion,
                           geo=geo, orig_addr=dict(addr))
        feedback_store.append({
            "order_id": order_id, "order_number": order_number,
            "original": dict(addr), "suggested": geo_suggestion,
            "accepted": True, "user_edit": None, "user_note": None,
        })
        changes = "  |  ".join(
            f"{k}: [dim]{addr.get(k, '')}[/] → [cyan]{v}[/]"
            for k, v in geo_suggestion.items()
        )
        console.print(f"\n  [cyan]Auto-fixed (OpenCage 10/10):[/] {changes}")
        addr_result = "fixed"

    # ── Address section ────────────────────────────────────────────────────
    if has_addr_issues:
        console.print("\n  [bold red]Issues found:[/]")
        _display_issues(issues)

        # Build suggestion: merge deterministic + geocode, geocode takes priority.
        # Geocode verified the address against the real world so its field values
        # (especially ZIP) are more reliable than a simple prefix-strip heuristic.
        suggestion_from_geocode = False
        det_suggestion = _deterministic_suggestion(addr, issues)
        geo_sugg = geo_suggestion or _geocode_suggestion(addr, geo, geo_company=geo_company)
        if geo_sugg or det_suggestion:
            # Geocode overrides deterministic for overlapping fields
            suggestion = {**det_suggestion, **geo_sugg}
            suggestion_from_geocode = bool(geo_sugg)
        else:
            suggestion = suggest_fix(addr, issues, geo, recent_feedback)

        suggested_display = dict(addr)
        suggested_display.update(suggestion)
        _display_address_table(addr, suggested_display)
        _display_geocode_components(geo)

        if not suggestion:
            console.print(
                "\n  [yellow]No automatic fix available — "
                "enter [bold]e[/bold] to correct manually.[/]"
            )
        choice = _prompt_choice(default="e" if not suggestion else "s")

        if choice == "q":
            return "quit", "quit"

        elif choice == "s":
            addr_result = "skipped"

        elif choice == "y":
            fix = suggestion if suggestion else _prompt_edit(addr)
            addr_result = _apply_with_verification(
                addr, fix, suggestion,
                from_geocode=suggestion_from_geocode,
                skip_geocode=skip_geocode,
                order_id=order_id, order_number=order_number,
                geo=geo,
            )

        elif choice == "n":
            feedback_store.append({
                "order_id": order_id, "order_number": order_number,
                "original": dict(addr), "suggested": suggestion,
                "accepted": False, "user_edit": None, "user_note": None,
            })
            _save_pending_case(
                order_number, addr, outcome="rejected",
                system_suggestion=suggestion or None,
            )
            console.print("  [dim]Rejected.[/]")
            addr_result = "rejected"

        elif choice == "e":
            edits = _prompt_edit(addr)
            addr_result = _apply_with_verification(
                addr, edits, suggestion,
                from_geocode=False,  # always re-verify manual edits
                skip_geocode=skip_geocode,
                order_id=order_id, order_number=order_number,
                geo=geo,
            )
            if edits and addr_result in ("fixed", "skipped"):
                _save_pending_case(
                    order_number, addr, outcome="manually_edited",
                    system_suggestion=suggestion or None,
                    user_edit=edits,
                )

    # ── Package type section ───────────────────────────────────────────────
    pkg_result = process_package_type(order, order_number, resolved_items, pkg_types)

    return addr_result, pkg_result


def _run_order_loop(
    orders: list,
    skip_geocode: bool,
    recent_feedback: list[dict],
    by_id: dict,
    by_sku: dict,
    field_defs: dict,
    pkg_types: list[dict],
    force_manual: bool = False,
) -> dict:
    """
    Iterate through orders, collect queued changes, return stats dict.
    All Billbee writes go into _pending_changes — nothing is written here.
    """
    stats = {
        "fixed": 0, "rejected": 0, "skipped": 0, "no_issue": 0,
        "pkg_auto": 0, "pkg_set": 0, "pkg_skipped": 0,
    }
    n = len(orders)
    label = " [yellow bold](manual mode)[/]" if force_manual else ""
    console.print(f"\n[cyan]Checking {n} orders...{label}[/]")
    try:
        for i, order in enumerate(orders, 1):
            addr_result, pkg_result = process_order(
                order=order,
                skip_geocode=skip_geocode,
                recent_feedback=recent_feedback,
                by_id=by_id,
                by_sku=by_sku,
                field_defs=field_defs,
                pkg_types=pkg_types,
                order_index=i,
                order_count=n,
                force_manual=force_manual,
            )
            if addr_result == "quit":
                console.print("\n[dim]Quit.[/]")
                break
            stats[addr_result] = stats.get(addr_result, 0) + 1
            if pkg_result == "auto":
                stats["pkg_auto"] += 1
            elif pkg_result == "set":
                stats["pkg_set"] += 1
            elif pkg_result == "skipped":
                stats["pkg_skipped"] += 1
    except KeyboardInterrupt:
        console.print("\n[dim]Interrupted.[/]")
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Fix Billbee delivery addresses interactively")
    parser.add_argument("--dry-run", action="store_true",
                        help="Check and suggest fixes but do not update Billbee")
    parser.add_argument("--since", metavar="DATE",
                        help="Override last_run date, e.g. 2026-01-01")
    parser.add_argument("--skip-geocode", action="store_true",
                        help="Skip OpenCage geocoding (faster, uses fewer API credits)")
    args = parser.parse_args()

    if args.dry_run:
        console.print("[yellow bold]DRY RUN mode — no changes will be written to Billbee[/]")

    # Load project settings
    _settings_file = Path(__file__).parent / "config" / "settings.yaml"
    _settings: dict = {}
    if _settings_file.exists():
        with open(_settings_file, encoding="utf-8") as _f:
            _settings = yaml.safe_load(_f) or {}

    # Load order filter settings from the shared morning_fetch.yaml
    fetch_cfg = load_fetch_config()
    since = compute_since(fetch_cfg["lookback_hours"], override=args.since)
    run_start = datetime.now(timezone.utc)

    # ── Log file setup ────────────────────────────────────────────────────────
    _log_dir = Path(__file__).parent / "logs"
    _log_dir.mkdir(exist_ok=True)
    _log_path = _log_dir / f"run_{run_start.strftime('%Y-%m-%d_%H%M%S')}.log"
    _log_file = open(_log_path, "w", encoding="utf-8", buffering=1)  # line-buffered

    _RICH_TAG_RE = re.compile(r"\[/?[^\]]*\]")

    def _log(msg: str, *, also_console: bool = False) -> None:
        """Write plain text (Rich markup stripped) to the log file.
        Pass also_console=True to additionally print to the terminal.
        """
        plain = _RICH_TAG_RE.sub("", msg)
        _log_file.write(plain + "\n")
        if also_console:
            console.print(msg)

    def _clog(msg: str) -> None:
        """Print to both terminal (Rich markup) and log file (plain text)."""
        console.print(msg)
        _log_file.write(_RICH_TAG_RE.sub("", msg) + "\n")

    _log(f"=== Run started {run_start.strftime('%Y-%m-%d %H:%M:%S')} UTC ===")
    _log(f"Log file: {_log_path}")
    if args.dry_run:
        _log("Mode: DRY RUN")

    console.print(
        f"[dim]Config: states={fetch_cfg['state_ids'] or 'all'}, "
        f"lookback={fetch_cfg['lookback_hours']}h, since={since}[/]"
    )

    # Initialize
    client = BillbeeClient()
    recent_feedback = feedback_store.get_recent_accepted(n=20)

    # Load product cache and package types (needed for item resolution)
    console.print()
    if _product_cache.cache_exists():
        age = _product_cache.cache_age_str()
        refresh = Confirm.ask(
            f"  Product cache exists (age: {age}). Refresh from Billbee?",
            default=False,
        )
    else:
        console.print("  [dim]No product cache found — fetching from Billbee...[/]")
        refresh = True
    by_id, by_sku, field_defs = _product_cache.load_or_refresh(client, force=refresh)
    pkg_types = fetch_package_types(client)
    if pkg_types:
        console.print(f"[dim]{len(pkg_types)} package types available[/]")

    # Fetch orders matching the configured states and lookback window
    orders = fetch_orders_since(client, since, state_ids=fetch_cfg["state_ids"])
    _log(f"Orders fetched: {len(orders)}  (since {since})")

    if not orders:
        console.print("[green]No new orders to check.[/]")
        _log("No new orders — run complete.")
        _log_file.close()
        if not args.dry_run:
            save_last_run(run_start)
        return

    # ── Review phase: process all orders, queue changes ─────────────────────
    loop_kwargs = dict(
        skip_geocode=args.skip_geocode,
        recent_feedback=recent_feedback,
        by_id=by_id, by_sku=by_sku, field_defs=field_defs,
        pkg_types=pkg_types,
    )
    stats = _run_order_loop(orders, **loop_kwargs)

    # ── Summary panel ────────────────────────────────────────────────────────
    console.print()
    console.print(Panel(
        f"[bold]Summary[/]\n"
        f"  Orders checked:          {len(orders)}\n"
        f"  [green]Address fixed:[/]          {stats['fixed']}\n"
        f"  [red]Address rejected:[/]       {stats['rejected']}\n"
        f"  [dim]Address skipped:[/]        {stats['skipped']}\n"
        f"  [dim]No address issues:[/]      {stats['no_issue']}\n"
        f"  [cyan]Package type auto:[/]      {stats['pkg_auto']}\n"
        f"  [cyan]Package type set:[/]       {stats['pkg_set']}\n"
        f"  [dim]Package type skipped:[/]   {stats['pkg_skipped']}",
        border_style="cyan",
    ))

    # ── Pending changes: confirm before writing to Billbee ───────────────────
    _show_pending_summary()

    if not _pending_changes:
        if not args.dry_run:
            save_last_run(run_start)
            console.print(f"[dim]Last run timestamp saved: {run_start.strftime('%Y-%m-%dT%H:%M:%SZ')}[/]")
        return

    def _confirm_loop(allow_redo: bool) -> str:
        """
        Interactive confirmation loop. Returns 'apply', 'redo', or 'quit'.
        Accepts: a / r (if allow_redo) / q / <number> to edit that order's address.
        """
        redo_hint = "  [bold yellow]\\[r][/] Redo manual  " if allow_redo else ""
        hint = (
            f"  [bold green]\\[a][/] Apply all  {redo_hint}"
            f"[bold dim]\\[q][/] Quit  or order# to edit"
        )
        while True:
            console.print()
            raw = Prompt.ask(hint, default="a").strip()
            if raw.isdigit():
                _edit_pending_by_order_index(int(raw), orders)
                _show_pending_summary()
                continue
            raw = raw.lower()
            if raw == "a":
                return "apply"
            if raw == "r" and allow_redo:
                return "redo"
            if raw == "q":
                return "quit"
            console.print("  [red]Enter a, r, q, or an order number.[/]")

    action = _confirm_loop(allow_redo=True)

    if action == "quit":
        console.print("[dim]Quit — no changes written to Billbee.[/]")
        return

    if action == "redo":
        _pending_changes.clear()
        console.print("\n[yellow bold]Manual mode — every suggestion requires confirmation.[/]")
        stats = _run_order_loop(orders, **loop_kwargs, force_manual=True)
        _show_pending_summary()
        if not _pending_changes:
            console.print("[dim]No changes to apply.[/]")
            return
        action = _confirm_loop(allow_redo=False)
        if action == "quit":
            console.print("[dim]Quit — no changes written to Billbee.[/]")
            return

    # Apply all queued changes
    n_applied, _apply_errors = _apply_all_pending(client, args.dry_run)
    verb = "would be applied" if args.dry_run else "applied"
    _clog(f"\n[green]{n_applied} change(s) {verb}.[/]")

    # Accumulate all errors across all phases for the final summary
    _all_errors: list[dict] = list(_apply_errors)

    # ── Set order state after address fixes ──────────────────────────────────
    _transitions_cfg = _settings.get("order_state_transitions") or {}
    _after_fix_state = int(_transitions_cfg.get("after_fix") or 0)
    _after_label_state = int(_transitions_cfg.get("after_label") or 0)

    if not args.dry_run and _after_fix_state and orders:
        _clog(f"\n[cyan]Setting {len(orders)} order(s) to state {_after_fix_state}...[/]")
        _fix_ok = 0
        for _o in orders:
            _oid = _o.get("BillBeeOrderId") or _o.get("Id")
            _onum = _o.get("OrderNumber") or str(_oid)
            try:
                client.set_order_state(_oid, _after_fix_state)
                _fix_ok += 1
            except Exception as _e:
                _all_errors.append({
                    "order_number": _onum,
                    "operation": f"set state → {_after_fix_state}",
                    "error": str(_e),
                })
        _fix_fail = len(orders) - _fix_ok
        _clog(
            f"  [green]{_OK} {_fix_ok} order(s) set to state {_after_fix_state}[/]"
            + (f"  [red]({_fix_fail} failed)[/]" if _fix_fail else "")
        )

    # ── Shipping label creation ───────────────────────────────────────────────
    _labels_cfg = _settings.get("shipping_labels") or {}
    if not args.dry_run and _labels_cfg.get("enabled"):
        _label_folder = _resolve_folder(
            _labels_cfg.get("label_folder") or "labels"
        )
        _provider_id = int(_labels_cfg.get("shipping_provider_id") or 0)
        _product_id = int(_labels_cfg.get("shipping_provider_product_id") or 0)
        _timeout_min = int(_labels_cfg.get("polling_timeout_minutes") or 15)

        from execution.create_labels import create_labels_with_polling
        _clog(f"\n[cyan]Starting label polling for {len(orders)} order(s) → {_label_folder}[/]")
        try:
            _label_stats = create_labels_with_polling(
                client, orders, _label_folder,
                provider_id=_provider_id,
                product_id=_product_id,
                after_label_state=_after_label_state,
                timeout_minutes=_timeout_min,
                initial_wait=True,  # wait 1 min for Billbee automation after state change
                console=console,
                log_fn=lambda msg: _log_file.write(msg + "\n"),
            )
            _all_errors.extend(_label_stats.get("errors") or [])
            _summary = (
                f"Labels: {_label_stats['created']} created, "
                f"{_label_stats['skipped']} skipped (already labeled), "
                f"{_label_stats['failed']} failed"
            )
            _clog(f"[dim]{_summary}[/]")
        except Exception as e:
            _clog(f"\n[red bold]{_FAIL} Label creation aborted:[/] {e}")
            _all_errors.append({"order_number": "—", "operation": "label creation (batch)", "error": str(e)})

    # ── Final error summary ───────────────────────────────────────────────────
    if _all_errors:
        lines = [f"[bold red]{_FAIL} {len(_all_errors)} problem(s) require attention:[/]\n"]
        for err in _all_errors:
            lines.append(
                f"  [bold]{err['order_number']}[/]  "
                f"[yellow]{err['operation']}[/]  —  {err['error']}"
            )
        console.print(Panel(
            "\n".join(lines),
            border_style="red",
            title="[bold red]Errors[/]",
        ))
        _log_file.write("\n=== ERRORS ===\n")
        for err in _all_errors:
            _log_file.write(f"  {err['order_number']}  [{err['operation']}]  {err['error']}\n")

    # Save last run timestamp (only on real runs, not dry-run)
    if not args.dry_run:
        save_last_run(run_start)
        _clog(f"[dim]Last run timestamp saved: {run_start.strftime('%Y-%m-%dT%H:%M:%SZ')}[/]")

    run_end = datetime.now(timezone.utc)
    _log(
        f"=== Run finished {run_end.strftime('%Y-%m-%d %H:%M:%S')} UTC "
        f"({(run_end - run_start).seconds}s) ==="
    )
    _log_file.close()
    console.print(f"[dim]Log saved: {_log_path}[/]")


if __name__ == "__main__":
    main()
