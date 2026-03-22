"""
Fill a B2B webshop cart from a Google Sheet tab.

Flow:
  1. Read visible rows from the order tab (Qty + URL required).
  2. Open Chromium with saved session and add each item to the cart.
     Qty is rounded up to the product's minimum order multiple (step attr).
  3. Navigate to the cart page so the user can review / adjust.
  4. On Enter: scrape the actual cart contents and write them back to the sheet
     (Qty and Cost columns), reflecting any changes the user made in the cart.
  5. Wait for the user to place the order in the browser, then press Enter.
  6. Close browser and exit.

Usage:
  python execution/add_to_cart.py --manufacturer TRX --tab "Order 2026-03-07"
  python execution/add_to_cart.py --manufacturer TRX --tab "Order 2026-03-07" --dry-run

Reads from the '{MFR} Orders' Google Sheet (e.g. 'TRX Orders'), visible rows only.
"""

import argparse
import json
import sys
from pathlib import Path

import requests as _requests
from google.auth.transport.requests import Request

sys.path.insert(0, str(Path(__file__).parent.parent))

from google_sheets_client import open_sheet_by_name


def _session_file(mfr: str) -> Path:
    tmp = Path(__file__).parent.parent / ".tmp"
    return tmp / f"{mfr}_session.json"


def _config_file(mfr: str) -> Path:
    return Path(__file__).parent.parent / ".tmp" / f"{mfr}_config.json"


def _load_config(mfr: str) -> dict:
    path = _config_file(mfr)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def _wait(prompt: str) -> None:
    """Pause and wait for Enter. Typing 'q' + Enter quits immediately."""
    # Flush any keypresses buffered while the browser was running,
    # so they don't accidentally skip this prompt.
    try:
        import termios
        termios.tcflush(sys.stdin.fileno(), termios.TCIFLUSH)
    except Exception:
        pass
    ans = input(f"{prompt}\n   (or type q + Enter to quit) ")
    if ans.strip().lower() == "q":
        print("Quitting.")
        sys.exit(0)


def _col_letter(n: int) -> str:
    """Convert 1-based column index to letter(s): 1→A, 26→Z, 27→AA."""
    result = ""
    while n:
        n, r = divmod(n - 1, 26)
        result = chr(65 + r) + result
    return result


def _get_visible_rows(ss, tab_name: str):
    """
    Returns (ws, headers, rows_with_index) where rows_with_index is a list of
    (sheet_row_1based, row_dict) for all visible (non-filtered) rows in the tab.
    """
    ws = ss.worksheet(tab_name)

    creds = ss.client.auth
    if not creds.valid:
        creds.refresh(Request())

    resp = _requests.get(
        f"https://sheets.googleapis.com/v4/spreadsheets/{ss.id}",
        params={"fields": "sheets(properties(sheetId),data(rowMetadata(hiddenByFilter)))"},
        headers={"Authorization": f"Bearer {creds.token}"},
    )
    resp.raise_for_status()

    hidden_rows: set[int] = set()
    for sheet in resp.json().get("sheets", []):
        if sheet["properties"]["sheetId"] == ws.id:
            for block in sheet.get("data", []):
                for i, meta in enumerate(block.get("rowMetadata", [])):
                    if meta.get("hiddenByFilter"):
                        hidden_rows.add(i)  # 0-based; 0 = header row
            break

    all_values = ws.get_all_values()
    if not all_values:
        return ws, [], []

    headers = all_values[0]
    result = []
    for i, row_vals in enumerate(all_values[1:], start=1):
        if i in hidden_rows:
            continue
        row_dict = {h: (row_vals[j] if j < len(row_vals) else "") for j, h in enumerate(headers)}
        sheet_row = i + 1  # 1-based; row 1 = header, so data row i → sheet row i+1
        result.append((sheet_row, row_dict))

    return ws, headers, result


def _scrape_cart(page) -> list[dict]:
    """
    Scrape cart items from the current page.
    Returns list of {ean, qty} dicts for items with qty > 0.
    """
    return page.evaluate("""() => {
        const items = [];
        // Pattern 1: data-product-id attribute (generic shops)
        document.querySelectorAll('article[data-product-id]').forEach(a => {
            const ean = a.getAttribute('data-product-id');
            const inp = a.querySelector('input[type="number"]');
            const qty = inp ? parseInt(inp.value, 10) : 0;
            if (ean && qty > 0) items.push({ean, qty});
        });
        if (items.length) return items;
        document.querySelectorAll('[data-product-id]').forEach(el => {
            const ean = el.getAttribute('data-product-id');
            const inp = el.querySelector('input[type="number"]');
            const qty = inp ? parseInt(inp.value, 10) : 0;
            if (ean && qty > 0) items.push({ean, qty});
        });
        if (items.length) return items;
        // Pattern 2: EAN embedded in product image filename (e.g. 5420047902269_01.jpg)
        document.querySelectorAll('article').forEach(art => {
            const img = art.querySelector('img');
            if (!img) return;
            const m = (img.src || '').match(/\\/([0-9]{13})_/);
            if (!m) return;
            const ean = m[1];
            const inp = art.querySelector('input[type="number"]');
            if (!inp) return;
            const qty = parseInt(inp.value, 10);
            if (qty > 0) items.push({ean, qty});
        });
        if (items.length) return items;
        // Pattern 3: qty inputs with nearest data-product-id / data-id ancestor
        document.querySelectorAll('input[type="number"]').forEach(inp => {
            const qty = parseInt(inp.value, 10);
            if (qty > 0) {
                const holder = inp.closest('[data-product-id]') || inp.closest('[data-id]');
                const ean = holder
                    ? (holder.getAttribute('data-product-id') || holder.getAttribute('data-id'))
                    : null;
                if (ean) items.push({ean, qty});
            }
        });
        return items;
    }""")


def _navigate_to_cart(page) -> bool:
    """
    Find the cart link from the current page's DOM and navigate to it.
    Works generically — no hardcoded paths.
    Returns True if navigation succeeded.
    """
    cart_url = page.evaluate("""() => {
        const links = Array.from(document.querySelectorAll('a[href]'));
        const a = links.find(l => /cart/i.test(l.getAttribute('href') || ''));
        return a ? a.href : null;
    }""")
    if not cart_url:
        return False
    try:
        resp = page.goto(cart_url, wait_until="domcontentloaded", timeout=10000)
        if resp and resp.status < 400:
            try:
                page.wait_for_load_state("networkidle", timeout=4000)
            except Exception:
                pass
            print(f"  Cart: {cart_url}")
            return True
    except Exception:
        pass
    return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Fill B2B cart from a Google Sheet tab.")
    parser.add_argument("--manufacturer", "-m", required=True,
                        help="Manufacturer code (e.g. TRX). Loads '{MFR} Orders' sheet and browser session.")
    parser.add_argument("--tab", required=True,
                        help="Tab name to read from (e.g. 'Order 2026-03-07').")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would be ordered without opening the browser.")
    args = parser.parse_args()

    mfr = args.manufacturer.upper()
    sf  = _session_file(mfr)
    if not sf.exists():
        print(f"[error] No browser session for '{mfr}'. Run 'b2b_cart.py setup' first.")
        sys.exit(1)

    cfg      = _load_config(mfr)
    base_url = (cfg.get("url") or "").rstrip("/")

    sheet_name = f"{mfr} Orders"
    print(f"Opening '{sheet_name}' / '{args.tab}' ...")
    ss = open_sheet_by_name(sheet_name)
    ws, headers, visible_rows = _get_visible_rows(ss, args.tab)

    def col1(name: str):
        """1-based column index for a header name, or None."""
        return (headers.index(name) + 1) if name in headers else None

    qty_col  = col1("Qty")
    cost_col = col1("Cost")
    price_col = col1("Price")

    to_order = [
        (sheet_row, row)
        for sheet_row, row in visible_rows
        if str(row.get("Qty") or "").strip() not in ("", "0")
        and str(row.get("URL") or "").strip()
    ]

    if not to_order:
        print("No visible rows with Qty + URL. Nothing to add to cart.")
        return

    # EAN → (sheet_row, row_dict) for write-back lookups
    ean_to_info: dict[str, tuple[int, dict]] = {
        str(row.get("EAN") or ""): (sheet_row, row)
        for sheet_row, row in visible_rows
        if str(row.get("EAN") or "").strip()
    }

    print(f"\n{len(to_order)} item(s) to add to cart:")
    for _, item in to_order:
        sku = str(item.get("SKU") or item.get("EAN") or "?")
        print(f"  {sku}  qty={item.get('Qty')}")

    if args.dry_run:
        print("\n[DRY-RUN] Browser not opened.")
        return

    print("\nOpening browser ...")
    from playwright.sync_api import sync_playwright, Error as _PWError

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False, slow_mo=50)
        ctx     = browser.new_context(storage_state=str(sf))
        page    = ctx.new_page()

        # ── Step 1: fill cart ─────────────────────────────────────────────────
        for sheet_row, item in to_order:
            ean      = str(item.get("EAN") or "")
            qty_orig = str(item.get("Qty") or "1").strip()
            url      = str(item.get("URL") or "")
            sku      = str(item.get("SKU") or ean)
            print(f"  → {sku}  qty={qty_orig}")
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=15000)
                try:
                    page.wait_for_load_state("networkidle", timeout=5000)
                except Exception:
                    pass

                qty_sel = (
                    page.query_selector(f"article[data-product-id='{ean}'] input[type='number']")
                    or page.query_selector("input[type='number'][name*='qty' i]")
                    or page.query_selector("input[type='number'][id*='qty' i]")
                    or page.query_selector("input[type='number'][class*='quantity' i]")
                    or page.query_selector("input[type='number']")
                )
                if not qty_sel:
                    print(f"    [warn] no qty input found — skipping")
                    continue

                qty_int = max(1, int(float(qty_orig)))

                # Set value, enforce step, dispatch events, and submit — all in
                # one atomic JS call so the framework cannot interfere between steps.
                js_result = page.evaluate(f"""el => {{
                    const step = Math.max(1, parseInt(el.step) || 1);
                    let qty = {qty_int};
                    if (step > 1 && qty % step !== 0) {{
                        qty = Math.ceil(qty / step) * step;
                    }}
                    el.value = String(qty);
                    el.dispatchEvent(new Event('input',  {{bubbles: true}}));
                    el.dispatchEvent(new Event('change', {{bubbles: true}}));

                    const form = el.closest('form');
                    if (!form) return 'no-form:qty=' + qty;
                    let btn = form.querySelector("button[type='submit'], input[type='submit']");
                    if (btn) {{ btn.click(); return 'ok-submit:qty=' + qty; }}
                    btn = form.querySelector("button");
                    if (btn) {{ btn.click(); return 'ok-btn:qty=' + qty; }}
                    try {{ form.submit(); return 'ok-form.submit:qty=' + qty; }} catch(e) {{}}
                    return 'no-btn:qty=' + qty;
                }}""", qty_sel)
                print(f"    submit → {js_result}")

                # Parse actual qty from result string (e.g. "ok-submit:qty=4")
                qty = qty_orig
                if ":qty=" in js_result:
                    qty = js_result.split(":qty=")[-1]
                if qty != qty_orig:
                    print(f"    qty adjusted: {qty_orig} → {qty}")
                if js_result.startswith("ok"):
                    try:
                        page.wait_for_load_state("networkidle", timeout=4000)
                    except Exception:
                        page.wait_for_timeout(1000)
                    print(f"    added (qty={qty})")
                else:
                    print(f"    [warn] could not submit form")

            except _PWError as e:
                print(f"    [error] {e}")

        # ── Step 2: open cart for user review ─────────────────────────────────
        print("\nNavigating to cart ...")
        if not _navigate_to_cart(page):
            print("  [warn] No cart link found on page. Please navigate manually.")

        _wait("\n>> Review cart in browser (adjust qtys / remove items), then press Enter ...")

        # ── Step 3: scrape actual cart contents and write back to sheet ─────────
        try:
            print("Reading cart ...")
            cart_items = _scrape_cart(page)
            print(f"  {len(cart_items)} item(s) in cart.")

            if not cart_items:
                html = page.content()
                dump_path = Path(__file__).parent.parent / ".tmp" / "cart_debug.html"
                dump_path.write_text(html, encoding="utf-8")
                print(f"  [warn] No items scraped from cart — sheet will not be updated.")
                print(f"  [debug] Cart HTML saved to {dump_path}")
            else:
                cart_by_ean = {str(item["ean"]): int(item["qty"]) for item in cart_items}
                updates: list[dict] = []

                for ean, (sheet_row, orig_row) in ean_to_info.items():
                    new_qty = cart_by_ean.get(ean, 0)  # 0 = removed from cart

                    if qty_col:
                        updates.append({
                            "range":  f"{_col_letter(qty_col)}{sheet_row}",
                            "values": [[new_qty if new_qty else ""]],
                        })

                    if cost_col and price_col:
                        try:
                            price = float(str(orig_row.get("Price") or "0").replace(",", "."))
                            cost  = round(price * new_qty, 2) if new_qty else ""
                        except (TypeError, ValueError):
                            cost = ""
                        updates.append({
                            "range":  f"{_col_letter(cost_col)}{sheet_row}",
                            "values": [[cost]],
                        })

                if updates:
                    print(f"  Writing {len(updates)} cell update(s) back to sheet ...")
                    ws.batch_update(updates, value_input_option="USER_ENTERED")
                    print(f"  Sheet updated.")

        except Exception as e:
            print(f"  [error] Cart read/sheet update failed: {e}")
            print("  Sheet was not updated. You can update it manually.")

        # ── Step 4: wait for user to place the order ──────────────────────────
        _wait("\n>> Place your order in the browser, then press Enter to close and finish ...")
        browser.close()

    print("Done!")


if __name__ == "__main__":
    main()
