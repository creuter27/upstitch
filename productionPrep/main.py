"""
Fix Billbee delivery addresses — interactive terminal tool.

Fetches new orders since the last run, checks their delivery addresses for
common errors, suggests fixes using rule-based checks + OpenCage + Claude API,
and lets you accept/reject/edit each fix interactively.

Accepted fixes and package type tags are applied to Billbee per-order (including
the configured state transition), so Billbee automation can start running
immediately for each order as it is processed.

Run fetchDocuments next to fetch invoices/delivery notes while Billbee automation
assigns shipping profiles, then run getLabels to create shipping labels.

Usage:
    .venv/bin/python main.py
    .venv/bin/python main.py --dry-run
    .venv/bin/python main.py --since 2026-01-01
    .venv/bin/python main.py --skip-geocode
"""

import argparse
import re
import sys

try:
    import readline as _readline
    _HAS_READLINE = True
except ImportError:
    _HAS_READLINE = False  # Windows — no prefill, but input still works
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
    fetch_package_types, combo_key, get_for_combo, set_for_combo, KEINE_PKG_TYPE,
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

# Enable ANSI VT100 colour processing in Windows console.
# os.system("") flips ENABLE_VIRTUAL_TERMINAL_PROCESSING for the current session,
# making dim/colour codes render correctly in CMD and PowerShell.
if _IS_WIN:
    import os
    os.system("")

# Always force terminal mode so Rich outputs colour codes regardless of whether
# stdout is redirected or the bat file doesn't advertise a colour terminal.
console = Console(force_terminal=True)


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

# Billbee order state names for error reporting
_ORDER_STATE_NAMES = {
    6: "Geloescht (deleted)",
    8: "Storniert (cancelled)",
}

_CUSTOM_ORDER_SKU_PREFIX = "custom-order"


def _is_custom_order(order: dict) -> bool:
    """Return True if any order item SKU starts with 'custom-order'."""
    for item in (order.get("OrderItems") or []):
        sku = ((item.get("Product") or {}).get("SKU") or "").strip()
        if sku.lower().startswith(_CUSTOM_ORDER_SKU_PREFIX):
            return True
    return False


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
    addr_clean = {k: addr[k] for k in _ADDR_FIELDS_FOR_CASES
                  if k in addr and addr[k] not in (None, "")}

    case: dict = {
        "description": f"Auto: Order #{order_number} ({addr_clean.get('CountryISO2', '?')})",
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "outcome": outcome,
        "addr": addr_clean,
        "expected_issues": [],
        "expected_fix": {},
    }
    if system_suggestion:
        case["system_suggestion"] = system_suggestion
    if user_edit:
        case["user_edit"] = user_edit

    _PENDING_CASES_FILE.parent.mkdir(parents=True, exist_ok=True)

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
                       geo: dict = None, orig_addr: dict = None,
                       auto_fixed: bool = False) -> None:
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
        "auto_fixed": auto_fixed,
    })


def _fix_category(fix: dict) -> str:
    """Determine display category for an auto-fixed address.
    Priority (highest wins): city_zip > house_number > street."""
    keys = set(fix.keys())
    if keys & {"City", "Zip", "AddressAddition"}:
        return "city_zip"
    if "HouseNumber" in keys:
        return "house_number"
    if "Street" in keys:
        return "street"
    return "other"


def _show_fixed_addr_summary(orders: list) -> None:
    """Show a categorized, numbered list of ALL modified addresses with Maps links.
    Each entry can be re-edited: restore original, keep fixed, or manually edit."""
    entries = [ch for ch in _pending_changes if ch["type"] == "address"]
    if not entries:
        return

    CAT_ORDER = {"street": 0, "house_number": 1, "city_zip": 2, "other": 3}
    CAT_LABEL = {
        "street": "Street fixes",
        "house_number": "House number fixes",
        "city_zip": "City / ZIP fixes",
        "other": "Other fixes",
    }
    entries.sort(key=lambda ch: CAT_ORDER.get(_fix_category(ch["fix"]), 3))
    order_by_id = {(o.get("BillBeeOrderId") or o.get("Id")): o for o in orders}

    while True:
        console.print()
        console.print(f"  [bold]Modified addresses ({len(entries)}):[/]")

        last_cat = None
        for i, ch in enumerate(entries, 1):
            cat = _fix_category(ch["fix"])
            if cat != last_cat:
                console.print(f"\n  [dim]── {CAT_LABEL.get(cat, cat)} ──[/]")
                last_cat = cat

            order = order_by_id.get(ch["order_id"])
            name, flag = _order_name_flag(order) if order else (ch["order_number"], "")
            tag = " [cyan][auto][/cyan]" if ch.get("auto_fixed") else ""

            orig = ch.get("orig_addr") or {}
            changes = "  |  ".join(
                f"{k}: [dim]{orig.get(k, '')}[/] → [cyan]{v}[/]"
                for k, v in ch["fix"].items()
            )
            url = _maps_url({**orig, **ch["fix"]})
            console.print(
                f"  [bold]{i}.[/] [dim]{name} {flag}[/]{tag}  {changes}"
                f"  [link={url}][dim]Maps[/dim][/link]"
            )

        console.print()
        raw = Prompt.ask(
            "  [dim]Enter number to edit, or Enter to continue[/]",
            default="",
        ).strip()
        if not raw:
            break

        try:
            n = int(raw)
        except ValueError:
            console.print("  [red]Enter a number or press Enter to continue.[/]")
            continue

        if not (1 <= n <= len(entries)):
            console.print(f"  [red]Enter a number between 1 and {len(entries)}.[/]")
            continue

        ch = entries[n - 1]
        orig = ch.get("orig_addr") or {}
        current_fix = {**orig, **ch["fix"]}
        order = order_by_id.get(ch["order_id"])
        if order:
            _display_order_header(order)
        _display_address_table(orig, current_fix)
        _display_geocode_components(ch.get("geo"))
        console.print()

        choice = Prompt.ask(
            "  [bold green]\\[o][/] Restore original  "
            "[bold cyan]\\[f][/] Keep fixed  "
            "[bold yellow]\\[e][/] Re-edit",
            choices=["o", "f", "e"],
            default="f",
        ).lower()

        if choice == "o":
            # Restore original: remove all fix fields that came from orig
            ch["fix"] = {}
            ch["summary"] = "[original restored]"
            console.print("  [dim]Restored to original address.[/]")
        elif choice == "f":
            console.print("  [dim]Keeping fixed address.[/]")
        else:
            edits = _prompt_edit(current_fix)
            if edits:
                ch["fix"] = {**ch["fix"], **edits}
                ch["summary"] = "  |  ".join(f"{k}: {v}" for k, v in ch["fix"].items())
                console.print("  [dim]Updated.[/]")
            else:
                console.print("  [dim]No changes.[/]")


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

    existing = next(
        (c for c in _pending_changes
         if c["type"] == "address" and c["order_id"] == order_id),
        None,
    )

    base_addr = {**addr, **(existing["fix"] if existing else {})}
    geo = existing["geo"] if existing else None

    _display_order_header(order, idx=order_idx_1based, total=len(orders))
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
        # Preserve the original address (never overwrite with an intermediate state)
        if not existing.get("orig_addr"):
            existing["orig_addr"] = dict(addr)
        console.print("  [dim]Updated.[/]")
    else:
        _queue_address_fix(order_id, order_number, new_fix, orig_addr=dict(addr))
        console.print("  [dim]Queued.[/]")


def _apply_all_pending(
    client,
    dry_run: bool,
    after_fix_state: int = 0,
) -> tuple[int, list[dict]]:
    """
    Write all queued changes to Billbee, grouped by order.

    For each order that has any queued change, after applying all its changes
    the order state is set to after_fix_state (if non-zero and not dry_run).
    This triggers Billbee automation (shipping profile, package type) immediately
    per-order rather than waiting for the whole batch to finish.

    Returns (n_applied, errors).
    """
    applied = 0
    errors: list[dict] = []

    # Group changes by order_id so we can set state once per order after all
    # its changes are written.
    from collections import defaultdict
    by_order: dict = defaultdict(list)
    for ch in _pending_changes:
        by_order[ch["order_id"]].append(ch)

    total = len(_pending_changes)
    item_num = 0

    console.print(f"\n[dim]Applying {total} change(s) to Billbee...[/]")

    for order_id, changes in by_order.items():
        order_num = changes[0]["order_number"]

        for ch in changes:
            item_num += 1
            if ch["type"] == "address":
                console.print(
                    f"  [{item_num}/{total}] {_e('📍', 'ADDR')} Order {order_num}  address ...",
                    end=" ",
                )
                try:
                    ok = apply_fix(client, ch["order_id"], ch["fix"], dry_run=dry_run)
                    console.print(f"[green]{_OK}[/]" if ok else f"[red]{_FAIL}[/]")
                    if ok:
                        applied += 1
                    else:
                        errors.append({
                            "order_number": order_num,
                            "operation": "address fix",
                            "error": "apply_fix returned False",
                        })
                except Exception as e:
                    console.print(f"[red]{_FAIL}[/]")
                    errors.append({"order_number": order_num, "operation": "address fix", "error": str(e)})

            elif ch["type"] == "package_type":
                console.print(
                    f"  [{item_num}/{total}] {_e('📦', 'PKG')} Order {order_num}  {ch['pkg_name']} ...",
                    end=" ",
                )
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

        # ── Set order state after all this order's changes are applied ──────
        if not dry_run and after_fix_state:
            console.print(
                f"  [{item_num}/{total}] {_e('🔄', '->')} Order {order_num}  "
                f"state → {after_fix_state} ...",
                end=" ",
            )
            try:
                client.set_order_state(order_id, after_fix_state)
                console.print(f"[green]{_OK}[/]")
            except Exception as e:
                console.print(f"[red]{_FAIL}[/]")
                errors.append({
                    "order_number": order_num,
                    "operation": f"set state → {after_fix_state}",
                    "error": str(e),
                })

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


def _maps_url(addr: dict) -> str:
    """Build a Google Maps search URL for the given address."""
    from urllib.parse import quote_plus
    street_hn = f"{(addr.get('Street') or '')} {(addr.get('HouseNumber') or '')}".strip()
    parts = [p for p in [
        street_hn,
        addr.get("AddressAddition") or "",
        addr.get("Zip") or "",
        addr.get("City") or "",
        addr.get("CountryISO2") or "",
    ] if p]
    query = ", ".join(parts)
    if not query:
        return ""
    return f"https://www.google.com/maps/search/?api=1&query={quote_plus(query)}"


def _display_order_header(order: dict, idx: int = 0, total: int = 0) -> None:
    name, flag = _order_name_flag(order)
    order_num = order.get("OrderNumber") or order.get("Id") or "?"
    counter = f"[dim][{idx}/{total}][/]  " if idx else ""
    console.print()
    console.rule(
        f"{counter}[bold cyan]Order #{order_num}[/]  [white]{name}[/]  {flag}",
        style="cyan",
    )
    addr = order.get("ShippingAddress", {})
    url = _maps_url(addr)
    if url:
        console.print(f"  [dim]Maps: [link={url}]{url}[/link][/]")


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


def _prompt_choice(addr: dict, default: str = "s") -> str:
    """Prompt user for action. Returns 'a', 'n', 'e', 's', or 'q'."""
    url = _maps_url(addr)
    if url:
        console.print(f"  [dim]Maps: [link={url}]{url}[/link][/]")
    console.print()
    raw = Prompt.ask(
        "  [bold green]\\[a][/] Accept  [bold red]\\[n][/] Reject  "
        "[bold yellow]\\[e][/] Edit  [bold dim]\\[s][/] Skip  [bold dim]\\[q][/] Quit",
        choices=["a", "n", "e", "s", "q"],
        default=default,
    )
    return raw.lower()


def _input_with_prefill(prompt: str, prefill: str) -> str:
    """Show a prompt with the current value pre-filled so the user can edit it in-place.

    On macOS/Linux uses readline so Ctrl-C/Ctrl-V copy-paste work normally.
    On Windows (no readline) falls back to plain input() — prefill is shown in
    the prompt text instead so the user can still see the current value.
    Entering '-' clears the field; Enter with no change keeps the prefilled value.
    """
    if _HAS_READLINE:
        def _hook():
            _readline.insert_text(prefill)
            _readline.redisplay()
        _readline.set_pre_input_hook(_hook)
        try:
            result = input(prompt)
        finally:
            _readline.set_pre_input_hook(None)
    else:
        # Windows fallback: show current value in brackets, empty input = keep
        display = f"{prompt}[{prefill}] " if prefill else prompt
        raw = input(display)
        result = raw if raw else prefill
    # A lone "-" means "clear this field"
    if result == "-":
        return ""
    return result


def _prompt_edit(original: dict) -> dict:
    """Let user manually edit address fields. Returns dict of changed fields."""
    console.print("\n  [yellow]Edit address fields (Enter = keep, '-' = clear):[/]")
    edits = {}
    for field_key, field_label in DISPLAY_FIELDS:
        current = original.get(field_key) or ""
        label_plain = re.sub(r"\[.*?\]", "", field_label).strip()
        new_val = _input_with_prefill(f"  {label_plain}: ", current)
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

    Returns 'fixed' or 'skipped'.
    """
    if not fix:
        console.print("  [dim]No changes made.[/]")
        return "skipped"

    geo_final = geo  # will be replaced by re-verification result if geocode is called
    if not from_geocode and not skip_geocode:
        new_addr = {**addr, **fix}
        console.print("\n  [dim]Re-verifying fixed address with OpenCage...[/]")
        geo_v = geocode(new_addr)
        geo_final = geo_v  # store result for the fixed address, not the original
        _display_address_table(addr, new_addr)
        _display_geocode(geo_v)
        if geo_v and geo_v.get("confidence", 0) < 6:
            console.print(
                f"\n  [yellow]{_WARN} Geocode confidence still low after fix — "
                "address may still be incorrect.[/]"
            )
        url = _maps_url({**addr, **fix})
        if url:
            console.print(f"  [dim]Maps: [link={url}]{url}[/link][/]")
        console.print()
        raw = Prompt.ask(
            "  Confirm?  [bold green]\\[a][/] Queue  "
            "[bold yellow]\\[e][/] Edit  [bold dim]\\[s][/] Skip",
            choices=["a", "e", "s"],
            default="a",
        ).lower()
        if raw == "s":
            return "skipped"
        elif raw == "e":
            extra_edits = _prompt_edit({**addr, **fix})
            if extra_edits:
                fix = {**fix, **extra_edits}

    _queue_address_fix(order_id, order_number, fix, geo=geo_final, orig_addr=addr)
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
    """
    import re as _re

    def _norm(s: str) -> str:
        s = s.lower()
        s = _re.sub(r'str\.\s*', 'straße', s)
        s = _re.sub(r'(?<![a-zäöüß])str(?![a-zäöüß])', 'straße', s)
        s = _re.sub(r'strasse', 'straße', s)
        s = _re.sub(r'[^\w]', '', s)
        return s

    return bool(original) and bool(suggested) and _norm(original) == _norm(suggested)


def _municipality_similarity(a: str, b: str) -> float:
    """Case-insensitive similarity ratio between two strings (0.0–1.0)."""
    from difflib import SequenceMatcher
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _geocode_suggestion(addr: dict, geo: dict | None,
                        geo_company: dict | None = None) -> dict:
    """
    Build a fix dict from a high-confidence geocode result (confidence ≥ 8).
    """
    if not geo or geo.get("confidence", 0) < 8:
        return {}

    components = geo.get("components", {})
    fix = {}

    import re as _re

    def _norm_street(s: str) -> str:
        s = s.lower()
        s = _re.sub(r"str\.\s*", "straße", s)
        s = _re.sub(r"(?<![a-zäöüß])str(?![a-zäöüß])", "straße", s)
        s = _re.sub(r"strasse", "straße", s)
        s = _re.sub(r"[^\w]", "", s)
        return s

    def _road_traceable_to_original(road: str) -> bool:
        """
        Return True if the suggested road name can be found (normalized) in at
        least one of the original address fields (Street, Company, AddressAddition).
        Prevents suggesting streets that are pure OpenCage invention.
        """
        norm_road = _norm_street(road)
        for field in ("Street", "Company", "AddressAddition"):
            orig = (addr.get(field) or "").strip()
            if orig and norm_road in _norm_street(orig):
                return True
        return False

    road = (components.get("road") or components.get("street") or "").strip()
    orig_street = (addr.get("Street") or "").strip()
    if road and road.lower() != orig_street.lower():
        orig_is_verifiable = orig_street and not _re.match(r"^\d+[-–]?\d*[a-zA-Z]?\s*$", orig_street)
        if orig_is_verifiable:
            # Only suggest the road if it can be traced back to the original address.
            # Suppresses streets that OpenCage invented from geocoding floor/apt notation.
            if _road_traceable_to_original(road):
                fix["Street"] = road
        elif geo_company:
            comp_components = geo_company.get("components", {})
            comp_road = (comp_components.get("road") or comp_components.get("street") or "").strip()
            company_val = (addr.get("Company") or "").strip()
            if comp_road and company_val and geo_company.get("confidence", 0) >= 8:
                if _norm_street(comp_road) == _norm_street(company_val):
                    fix["Street"] = comp_road

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
        formatted = geo.get("formatted", "").lower()
        if not orig_city or orig_city.lower() not in formatted:
            fix["City"] = city
            if orig_city and not (addr.get("AddressAddition") or "").strip():
                orig_lower = orig_city.lower()
                # Search all geographic OpenCage components to find which one the
                # customer's city name refers to.  We always put the customer's
                # original value in AddressAddition (their spelling, not OpenCage's).
                # Municipality is checked first so an exact match wins over a
                # partial village/suburb name that happens to appear inside it.
                _geo_keys = ["municipality", "town"] + list(_SUB_KEYS)
                _found = False
                for _k in _geo_keys:
                    _val = (components.get(_k) or "").strip()
                    if len(_val) < 4:
                        continue
                    sim = _municipality_similarity(_val, orig_city)
                    if sim >= 0.85 or (sim >= 0.65 and geo.get("confidence", 0) >= 9):
                        fix["AddressAddition"] = orig_city
                        _found = True
                        break
                    # Also accept if the component text appears inside orig_city
                    # (e.g. "Mitte" inside "Berlin-Mitte")
                    if len(_val) >= 4 and _val.lower() in orig_lower:
                        fix["AddressAddition"] = orig_city
                        _found = True
                        break

    return fix


_BUSINESS_INDICATORS = frozenset([
    "gmbh", "ag", "kg", "e.v.", "ug", "ltd", "inc", "bv", "nv", "sarl", "s.a.", "s.r.l.",
])


def _deterministic_suggestion(addr: dict, issues: list) -> dict:
    """
    Build a fix dict for issues that can be resolved without calling Claude.
    """
    fix = {}
    issue_codes = {i.code for i in issues}

    # When Company is a real street address and Street contains floor/apt notation,
    # Company wins: parse Company → Street+HouseNumber, move Street → AddressAddition.
    # This must run before HOUSE_NUMBER_AT_START_OF_STREET so that rule's guard
    # ("HouseNumber" not in fix) prevents it from mangling the result.
    if "STREET_IN_COMPANY_WITH_STREET_FILLED" in issue_codes and "HouseNumber" not in fix:
        company = (addr.get("Company") or "").strip()
        orig_street = (addr.get("Street") or "").strip()
        if company and not any(w in company.lower() for w in _BUSINESS_INDICATORS):
            parsed = parse_street_housenumber_floor(company)
            if parsed and parsed[0].strip():
                fix["Street"] = parsed[0]
                fix["HouseNumber"] = parsed[1]
                fix["Company"] = ""
                # Move the original Street (and HouseNumber if present) to AddressAddition
                if not (addr.get("AddressAddition") or "").strip():
                    orig_hn = (addr.get("HouseNumber") or "").strip()
                    parts = [p for p in [orig_street, orig_hn] if p]
                    if parts:
                        fix["AddressAddition"] = " ".join(parts)

    if "HOUSE_NUMBER_IN_STREET" in issue_codes:
        street = (addr.get("Street") or "").strip()
        existing_hn = (addr.get("HouseNumber") or "").strip()
        parsed = parse_street_housenumber_floor(street)
        if parsed:
            clean_street, hn, floor = parsed
            fix["Street"] = clean_street
            fix["HouseNumber"] = hn
            if existing_hn.startswith("/"):
                # Existing HouseNumber is a staircase/apt code — move to AddressAddition
                if not (addr.get("AddressAddition") or "").strip():
                    fix["AddressAddition"] = existing_hn
            elif floor and not (addr.get("AddressAddition") or "").strip():
                fix["AddressAddition"] = floor

    if "STREET_IS_HOUSE_NUMBER" in issue_codes and "HouseNumber" not in fix:
        street = (addr.get("Street") or "").strip()
        company = (addr.get("Company") or "").strip()
        if company and not any(w in company.lower() for w in _BUSINESS_INDICATORS):
            parsed = parse_street_housenumber_floor(company)
            if parsed and parsed[0].strip():
                # Company contains "StreetName HouseNumber" — use parsed values
                fix["Street"] = parsed[0]
                fix["HouseNumber"] = parsed[1]
                fix["Company"] = ""
            else:
                # Company is just a street name with no number embedded
                fix["Street"] = company
                fix["HouseNumber"] = street
                fix["Company"] = ""
        elif street:
            fix["HouseNumber"] = street
            fix["Street"] = ""

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

    if "HOUSE_NUMBER_EQUALS_ZIP" in issue_codes and "HouseNumber" not in fix:
        street = (addr.get("Street") or "").strip()
        parsed = parse_street_housenumber_floor(street)
        if parsed and parsed[0].strip():
            fix["Street"] = parsed[0]
            fix["HouseNumber"] = parsed[1]
            if parsed[2] and not (addr.get("AddressAddition") or "").strip():
                fix["AddressAddition"] = parsed[2]

    if "STREET_IS_SINGLE_LETTER_COMPANY_HAS_ADDRESS" in issue_codes and "HouseNumber" not in fix:
        street = (addr.get("Street") or "").strip()
        company = (addr.get("Company") or "").strip()
        existing_hn = (addr.get("HouseNumber") or "").strip()
        parsed = parse_street_housenumber_floor(company)
        if parsed and parsed[0].strip():
            fix["Street"] = parsed[0]
            fix["HouseNumber"] = parsed[1]
            fix["Company"] = ""
            # Combine original Street letter + HouseNumber as AddressAddition
            parts = [p for p in [street, existing_hn] if p]
            combined = " ".join(parts)
            if combined and not (addr.get("AddressAddition") or "").strip():
                fix["AddressAddition"] = combined

    return fix


def _prompt_package_type_custom(
    order_id, order_number: str, order: dict, pkg_types: list[dict]
) -> str:
    """
    Always show the package type selection for a custom order.
    The list includes 'Keine => kein Versand' for orders that need no label.
    The chosen type is queued but NOT saved to the combo mapping
    (custom orders are one-offs, not repeating product combos).
    Returns 'set' or 'skipped'.
    """
    if not pkg_types:
        console.print(
            f"  [yellow]{_e('📦', 'PKG')} Custom order — no package types configured.[/]\n"
            "  Edit data/package_types.yaml and rerun."
        )
        return "skipped"

    console.print(f"\n  [yellow]{_e('📦', 'PKG')} Custom order — select package type:[/]")
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

    _queue_package_type(order_id, order_number, order,
                        pkg_id=chosen.get("id"), pkg_name=chosen["name"])
    console.print(
        f"  [green]{_OK} Package type:[/] [bold]{chosen['name']}[/]"
    )
    return "set"


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
    defer_manual: bool = False,
) -> tuple[str, str]:
    """
    Process one order: check address + assign package type.
    Changes are queued in _pending_changes, not written to Billbee immediately.

    Returns (addr_result, pkg_result) where:
      addr_result: 'fixed' | 'rejected' | 'skipped' | 'no_issue' | 'deferred'
      pkg_result:  'auto'  | 'set'      | 'skipped' | 'no_items' | 'deferred'

    When defer_manual=True, orders that require manual address intervention are
    not processed interactively; instead they return ('deferred', 'deferred') so
    the caller can collect them and present them all at once after the main loop.
    """
    addr = order.get("ShippingAddress", {})
    order_id = order.get("BillBeeOrderId") or order.get("Id")
    order_number = order.get("OrderNumber") or str(order_id)
    is_custom = _is_custom_order(order)

    resolved_items = resolve_items(order, by_id, by_sku, field_defs)
    known_pkg = get_for_combo(resolved_items) if resolved_items else None
    # Custom orders always need a manual package type selection
    needs_pkg_prompt = (bool(resolved_items) and known_pkg is None) or is_custom

    issues = check_address(addr)

    geo = None
    geo_suggestion: dict = {}
    geo_auto_apply = False
    if not skip_geocode:
        from execution.check_address import Issue
        geo = geocode(addr)

        geo_company: dict | None = None
        _company = (addr.get("Company") or "").strip()
        _orig_street = (addr.get("Street") or "").strip()
        import re as _re_proc
        _street_is_useless = not _orig_street or bool(
            _re_proc.match(r"^(\d+[-–]?\d*[a-zA-Z]?|[A-Za-z])\s*$", _orig_street)
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
                geo_suggestion = _geocode_suggestion(addr, geo, geo_company=geo_company)

                # If a pure city replacement is suggested (not a sub-locality
                # correction where AddressAddition is also being updated), verify
                # the original city independently. OpenCage can be confused by
                # AddressAddition containing a personal name (e.g. "c/o Carbone
                # Giovanna") and return a wrong city with high confidence.
                # Geocoding ZIP + orig_city + country alone will confirm whether
                # the original city is valid. If it is, suppress the city change.
                if "City" in geo_suggestion and "AddressAddition" not in geo_suggestion:
                    orig_city = (addr.get("City") or "").strip()
                    if orig_city:
                        city_verify = geocode({
                            "Zip": addr.get("Zip", ""),
                            "City": orig_city,
                            "CountryISO2": addr.get("CountryISO2", "DE"),
                        })
                        if city_verify and city_verify.get("confidence", 0) >= 8:
                            # Suppress the city change only when orig_city is the
                            # direct town name (exact or near-exact match).
                            # If orig_city is a parent municipality the similarity
                            # will be low and we keep the correction.
                            v_comp = city_verify.get("components", {})
                            v_town = (
                                (v_comp.get("city") or v_comp.get("town") or "")
                            ).strip()
                            if v_town and _municipality_similarity(v_town, orig_city) >= 0.85:
                                del geo_suggestion["City"]
                            # else: orig_city is a municipality → allow the correction

                if geo_suggestion:
                    _auto = False
                    if not force_manual:
                        if conf == 10 and not issues:
                            _auto = True
                        elif conf == 10 and issues and all(i.code in _AUTO_FIX_CODES for i in issues):
                            det_fix = _deterministic_suggestion(addr, issues)
                            if det_fix:
                                _auto = True
                                geo_suggestion = {**geo_suggestion, **det_fix}
                                issues = []
                        elif conf >= 9 and not issues:
                            street_only = set(geo_suggestion.keys()) == {"Street"}
                            has_city = "City" in geo_suggestion
                            if street_only and _is_street_normalization(
                                (addr.get("Street") or ""), geo_suggestion["Street"]
                            ):
                                _auto = True
                            elif has_city:
                                _auto = True

                        # Path B: if a deterministic fix exists, apply it and
                        # re-geocode. Accept automatically when confidence ≥ 8.
                        if not _auto and issues:
                            det_fix = _deterministic_suggestion(addr, issues)
                            if det_fix:
                                fixed_addr = {**addr, **det_fix}
                                geo_fixed = geocode(fixed_addr)
                                if geo_fixed and geo_fixed.get("confidence", 0) >= 8:
                                    _auto = True
                                    geo = geo_fixed
                                    geo_sugg_fixed = _geocode_suggestion(fixed_addr, geo_fixed,
                                                                          geo_company=geo_company)
                                    geo_suggestion = {**det_fix, **geo_sugg_fixed}
                                    issues = []

                    if _auto:
                        geo_auto_apply = True
                    else:
                        corrections = ", ".join(f"{k} → '{v}'" for k, v in geo_suggestion.items())
                        issues.append(Issue(
                            code="GEOCODE_CORRECTION_AVAILABLE",
                            description=f"OpenCage suggests corrections: {corrections}.",
                            hint="Accept the OpenCage suggestion or edit manually.",
                        ))

                elif issues and not force_manual:
                    # No geo_suggestion (low original confidence), but a deterministic
                    # fix exists. Re-geocode the fixed address.
                    det_fix = _deterministic_suggestion(addr, issues)
                    if det_fix:
                        fixed_addr = {**addr, **det_fix}
                        geo_fixed = geocode(fixed_addr)
                        if geo_fixed and geo_fixed.get("confidence", 0) >= 8:
                            geo_auto_apply = True
                            geo = geo_fixed
                            geo_sugg_fixed = _geocode_suggestion(fixed_addr, geo_fixed,
                                                                  geo_company=geo_company)
                            geo_suggestion = {**det_fix, **geo_sugg_fixed}
                            issues = []

    has_addr_issues = bool(issues)

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
                               geo=geo, orig_addr=dict(addr), auto_fixed=True)
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

    # Defer orders that need manual attention until after the scan loop:
    #   - orders with address issues that weren't auto-fixed
    #   - custom orders (always need manual package type selection)
    if defer_manual and ((has_addr_issues and not geo_auto_apply) or is_custom):
        name, flag = _order_name_flag(order)
        counter = f"[dim][{order_index}/{order_count}][/]  " if order_index else ""
        reason = "↷ deferred (custom order)" if is_custom and not has_addr_issues else "↷ deferred"
        console.print(f"  {counter}[dim]{name} {flag}[/]  [yellow]{reason}[/]")
        return "deferred", "deferred"

    _display_order_header(order, idx=order_index, total=order_count)
    addr_result = "no_issue"

    if geo_auto_apply:
        _queue_address_fix(order_id, order_number, geo_suggestion,
                           geo=geo, orig_addr=dict(addr), auto_fixed=True)
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

    if has_addr_issues:
        console.print("\n  [bold red]Issues found:[/]")
        _display_issues(issues)

        suggestion_from_geocode = False
        det_suggestion = _deterministic_suggestion(addr, issues)
        geo_sugg = geo_suggestion or _geocode_suggestion(addr, geo, geo_company=geo_company)
        if geo_sugg or det_suggestion:
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
        choice = _prompt_choice(addr, default="e" if not suggestion else "s")

        if choice == "q":
            return "quit", "quit"

        elif choice == "s":
            addr_result = "skipped"

        elif choice == "a":
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
                from_geocode=False,
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

    if is_custom:
        pkg_result = _prompt_package_type_custom(order_id, order_number, order, pkg_types)
    else:
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
        "errors": [],   # list of {order_number, reason}
    }

    # Classify orders before the main loop: detect unusable orders upfront so
    # they are reported clearly rather than silently skipped.
    processable: list[dict] = []
    for order in orders:
        num = order.get("OrderNumber") or str(order.get("BillBeeOrderId") or order.get("Id") or "?")
        state = order.get("OrderStateId") or order.get("State") or 0
        addr = order.get("ShippingAddress") or {}
        if state in _ORDER_STATE_NAMES:
            stats["errors"].append({"order_number": num, "reason": _ORDER_STATE_NAMES[state]})
        elif not any(addr.values()):
            stats["errors"].append({"order_number": num, "reason": "No shipping address"})
        else:
            processable.append(order)

    n = len(processable)
    label = " [yellow bold](manual mode)[/]" if force_manual else ""
    console.print(f"\n[cyan]Checking {n} orders...{label}[/]")

    common_kwargs = dict(
        skip_geocode=skip_geocode,
        recent_feedback=recent_feedback,
        by_id=by_id, by_sku=by_sku, field_defs=field_defs,
        pkg_types=pkg_types,
        force_manual=force_manual,
    )

    def _tally(addr_result: str, pkg_result: str) -> None:
        if addr_result not in ("deferred", "quit"):
            stats[addr_result] = stats.get(addr_result, 0) + 1
        if pkg_result == "auto":
            stats["pkg_auto"] += 1
        elif pkg_result == "set":
            stats["pkg_set"] += 1
        elif pkg_result == "skipped":
            stats["pkg_skipped"] += 1

    deferred_orders: list[dict] = []
    quit_requested = False

    try:
        for i, order in enumerate(processable, 1):
            addr_result, pkg_result = process_order(
                order=order, order_index=i, order_count=n,
                defer_manual=(not force_manual),
                **common_kwargs,
            )
            if addr_result == "quit":
                console.print("\n[dim]Quit.[/]")
                quit_requested = True
                break
            if addr_result == "deferred":
                deferred_orders.append(order)
            else:
                _tally(addr_result, pkg_result)
    except KeyboardInterrupt:
        console.print("\n[dim]Interrupted.[/]")
        return stats

    # ── Deferred: manual review (address issues + custom orders) ─────────────
    if deferred_orders and not quit_requested:
        nd = len(deferred_orders)
        console.print(
            f"\n[cyan]Manual review — {nd} order(s) need attention:[/]"
        )
        try:
            for i, order in enumerate(deferred_orders, 1):
                addr_result, pkg_result = process_order(
                    order=order, order_index=i, order_count=nd,
                    defer_manual=False,
                    **common_kwargs,
                )
                if addr_result == "quit":
                    console.print("\n[dim]Quit.[/]")
                    break
                _tally(addr_result, pkg_result)
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

    # Load project settings (common config merged with platform-specific override)
    from execution.config_loader import load_config
    _settings: dict = load_config()

    _transitions_cfg = _settings.get("order_state_transitions") or {}
    _after_fix_state = int(_transitions_cfg.get("after_fix") or 0)

    # Load order filter settings from morning_fetch.yaml
    fetch_cfg = load_fetch_config()
    since = compute_since(fetch_cfg["lookback_hours"], override=args.since)
    run_start = datetime.now(timezone.utc)

    # ── Log file setup ────────────────────────────────────────────────────────
    _log_dir = Path(__file__).parent / "logs"
    _log_dir.mkdir(exist_ok=True)
    _log_path = _log_dir / f"run_{run_start.strftime('%Y-%m-%d_%H%M%S')}.log"
    _log_file = open(_log_path, "w", encoding="utf-8", buffering=1)

    _RICH_TAG_RE = re.compile(r"\[/?[^\]]*\]")

    def _log(msg: str, *, also_console: bool = False) -> None:
        plain = _RICH_TAG_RE.sub("", msg)
        _log_file.write(plain + "\n")
        if also_console:
            console.print(msg)

    def _clog(msg: str) -> None:
        console.print(msg)
        _log_file.write(_RICH_TAG_RE.sub("", msg) + "\n")

    _log(f"=== Run started {run_start.strftime('%Y-%m-%d %H:%M:%S')} UTC ===")
    if args.dry_run:
        _log("Mode: DRY RUN")

    console.print(
        f"[dim]Config: states={fetch_cfg['state_ids'] or 'all'}, "
        f"lookback={fetch_cfg['lookback_hours']}h, since={since}[/]"
    )

    # Initialize
    client = BillbeeClient()
    recent_feedback = feedback_store.get_recent_accepted(n=20)

    # Load product cache and package types
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

    # Fetch orders
    orders = fetch_orders_since(
        client, since,
        state_ids=fetch_cfg["state_ids"],
        criterion=fetch_cfg.get("criterion", "orderStatus"),
        tag_name=fetch_cfg.get("tag_name", ""),
    )
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
    order_errors = stats.get("errors") or []
    err_line = f"\n  [bold red]Cannot process:[/]         {len(order_errors)}" if order_errors else ""
    console.print()
    console.print(Panel(
        f"[bold]Summary[/]\n"
        f"  Orders fetched:          {len(orders)}\n"
        f"  [green]Address fixed:[/]          {stats['fixed']}\n"
        f"  [red]Address rejected:[/]       {stats['rejected']}\n"
        f"  [dim]Address skipped:[/]        {stats['skipped']}\n"
        f"  [dim]No address issues:[/]      {stats['no_issue']}\n"
        f"  [cyan]Package type auto:[/]      {stats['pkg_auto']}\n"
        f"  [cyan]Package type set:[/]       {stats['pkg_set']}\n"
        f"  [dim]Package type skipped:[/]   {stats['pkg_skipped']}"
        + err_line,
        border_style="cyan",
    ))

    if order_errors:
        lines = [f"[bold red]✗ {len(order_errors)} order(s) could not be processed:[/]\n"]
        for e in order_errors:
            lines.append(f"  [bold]{e['order_number']}[/]  —  {e['reason']}")
        console.print(Panel("\n".join(lines), border_style="red", title="[bold red]Errors[/]"))

    # ── Modified address review ───────────────────────────────────────────────
    _show_fixed_addr_summary(orders)

    # ── Pending changes: confirm before writing to Billbee ───────────────────
    _show_pending_summary()

    if not _pending_changes:
        if not args.dry_run:
            save_last_run(run_start)
            console.print(f"[dim]Last run timestamp saved: {run_start.strftime('%Y-%m-%dT%H:%M:%SZ')}[/]")
        _log_file.close()
        return

    def _confirm_loop(allow_redo: bool) -> str:
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
        _log_file.close()
        sys.exit(1)

    if action == "redo":
        _pending_changes.clear()
        console.print("\n[yellow bold]Manual mode — every suggestion requires confirmation.[/]")
        stats = _run_order_loop(orders, **loop_kwargs, force_manual=True)
        _show_pending_summary()
        if not _pending_changes:
            console.print("[dim]No changes to apply.[/]")
            _log_file.close()
            return
        action = _confirm_loop(allow_redo=False)
        if action == "quit":
            console.print("[dim]Quit — no changes written to Billbee.[/]")
            _log_file.close()
            sys.exit(1)

    # ── Apply queued changes + set state per-order ───────────────────────────
    # State is set immediately after each order's changes so Billbee automation
    # (shipping profile, Verpackungstyp assignment) triggers per-order, not after
    # the whole batch.
    n_applied, _apply_errors = _apply_all_pending(
        client, args.dry_run, after_fix_state=_after_fix_state
    )
    verb = "would be applied" if args.dry_run else "applied"
    _clog(f"\n[green]{n_applied} change(s) {verb}.[/]")

    _all_errors: list[dict] = list(_apply_errors)

    # ── Set state for orders that had NO pending changes ──────────────────────
    # (orders that were perfectly fine: no address issues, known package type)
    # They still need the state transition to trigger Billbee automation.
    if not args.dry_run and _after_fix_state:
        pending_order_ids = {ch["order_id"] for ch in _pending_changes}
        remaining = [
            o for o in orders
            if (o.get("BillBeeOrderId") or o.get("Id")) not in pending_order_ids
        ]
        if remaining:
            _clog(
                f"\n[dim]Setting state {_after_fix_state} for {len(remaining)} "
                f"order(s) with no pending changes...[/]"
            )
            for _o in remaining:
                _oid = _o.get("BillBeeOrderId") or _o.get("Id")
                _onum = _o.get("OrderNumber") or str(_oid)
                try:
                    client.set_order_state(_oid, _after_fix_state)
                except Exception as _e:
                    _all_errors.append({
                        "order_number": _onum,
                        "operation": f"set state → {_after_fix_state}",
                        "error": str(_e),
                    })

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

    # Save last run timestamp
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
