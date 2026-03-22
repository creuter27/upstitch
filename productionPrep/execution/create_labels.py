"""
Create shipping labels for orders via the Billbee API.

Uses POST /shipment/shipwithlabel which actually calls the carrier (e.g. DHL)
and returns a PDF label.  Provider/product IDs come from
GET /shipment/shippingproviders (account-level configured providers, NOT the
master-data ShippingProviderId stored on the order).

The label PDF returned in the response (LabelDataPdf, base64) is decoded and
saved to the configured output folder as {order_number}.pdf.

Package-type polling
--------------------
Before creating a label we check that the order has a "Verpackungstyp: ..."
tag (set by main.py during the address-fix phase, or by Billbee automation
after a state transition).  Orders without the tag are skipped and retried
after poll_interval_seconds, up to timeout_minutes.  This handles the case
where Billbee automation is still assigning shipping profiles / package types.

Usage (called from main.py or run_labels.py):
    from execution.create_labels import create_labels_with_polling
    stats = create_labels_with_polling(
        client, orders, output_dir,
        provider_id=..., product_id=...,
        timeout_minutes=15, initial_wait=True,
        console=console, log_fn=log_fn,
    )
"""

import base64
import time
from datetime import datetime, timezone
from pathlib import Path

# Tag prefix used to mark the chosen package type on an order
_PKG_TAG_PREFIX = "Verpackungstyp:"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_package_type_tag(order: dict) -> str | None:
    """Return the 'Verpackungstyp: ...' tag string if set on the order, else None."""
    for t in (order.get("Tags") or []):
        if isinstance(t, str) and t.startswith(_PKG_TAG_PREFIX):
            return t
    return None


def _resolve_from_list(providers: list, provider_id: int, product_id: int,
                       order: dict) -> tuple[int, int]:
    """
    Match provider/product IDs from a pre-fetched providers list.

    If both IDs are already non-zero, returns them unchanged.
    Otherwise tries to match by ShippingProviderName / ShippingProviderProductName
    stored on the order.
    """
    if provider_id and product_id:
        return provider_id, product_id

    order_provider_name = (order.get("ShippingProviderName") or "").lower()
    order_product_name = (order.get("ShippingProviderProductName") or "").lower()

    for p in providers:
        if order_provider_name and order_provider_name not in p.get("name", "").lower():
            continue
        resolved_pid = provider_id or p.get("id") or 0
        for prod in (p.get("products") or []):
            display = (prod.get("displayName") or "").lower()
            if not order_product_name or order_product_name in display:
                return resolved_pid, prod.get("id") or 0
        # Provider matched but no product matched — return provider ID only
        return resolved_pid, product_id

    return provider_id, product_id


def _create_label(client, order: dict, output_dir: Path,
                  provider_id: int, product_id: int) -> dict | None:
    """
    Create a carrier shipment label for one order and save the PDF.

    Returns a dict with keys path / tracking_id / tracking_url,
    or None if Billbee returned no PDF (order already has a label, etc.).
    Raises on API errors so the caller can catch and retry.
    """
    order_id = order.get("BillBeeOrderId") or order.get("Id")
    order_number = order.get("OrderNumber") or str(order_id)
    weight_g = int((order.get("ShipWeightKg") or 0) * 1000)

    data = client.create_shipment_with_label(
        order_id,
        provider_id=provider_id,
        product_id=product_id,
        weight_in_gram=weight_g,
    )

    pdf_b64 = data.get("LabelDataPdf") or ""
    if not pdf_b64:
        return None

    pdf_bytes = base64.b64decode(pdf_b64)
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f"{order_number}.pdf"
    out_path.write_bytes(pdf_bytes)

    return {
        "path": out_path,
        "tracking_id": data.get("ShippingId") or "",
        "tracking_url": data.get("TrackingUrl") or "",
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def create_labels_with_polling(
    client,
    orders: list[dict],
    output_dir: Path,
    *,
    provider_id: int = 0,
    product_id: int = 0,
    after_label_state: int = 0,
    timeout_minutes: int = 15,
    poll_interval_seconds: int = 60,
    initial_wait: bool = True,
    console=None,
    log_fn=None,
) -> dict:
    """
    Create shipping labels for orders, polling until all are done or timeout.

    Each polling round:
      1. Re-fetch the order from Billbee to get the latest tags / state.
      2. If a "Verpackungstyp: ..." tag is present → attempt label creation.
      3. If no tag yet → skip this round and retry after poll_interval_seconds.
      4. On API errors → log and retry (transient outages are retried automatically).
      5. On permanent config errors (no provider match) → fail immediately, don't retry.

    Parameters
    ----------
    initial_wait : bool
        True  → wait poll_interval_seconds before the FIRST check.
                 Use this from main.py after setting the after_fix state so
                 Billbee automation has time to assign shipping profiles.
        False → check immediately on the first round, wait between retries.
                 Use this from run_labels.py (manual invocation).
    timeout_minutes : int
        Stop trying after this many minutes.  Any orders still pending are
        reported in the error summary.
    poll_interval_seconds : int
        Seconds to wait between polling rounds (default 60).

    Returns
    -------
    dict with keys: created (int), skipped (int), failed (int), errors (list)
        errors entries: {order_number: str, operation: str, error: str}
    """

    def _log(plain: str, rich_: str | None = None) -> None:
        if console:
            console.print(rich_ or plain)
        if log_fn:
            log_fn(plain)

    stats: dict = {"created": 0, "skipped": 0, "failed": 0, "errors": []}

    if not orders:
        return stats

    # Build pending dict: order_number -> state
    # last_error is updated each round so at timeout we report the most recent reason.
    pending: dict[str, dict] = {}
    for order in orders:
        order_id = order.get("BillBeeOrderId") or order.get("Id")
        order_number = order.get("OrderNumber") or str(order_id)
        pending[order_number] = {
            "order_id": int(order_id),
            "last_error": None,    # most recent transient error, or None
            "no_pkg_type": False,  # True if last fetch had no Verpackungstyp tag
        }

    # Pre-fetch shipping providers once — they don't change during a run
    providers: list = []
    try:
        providers = client.get_shipping_providers()
        names = ", ".join(f"{p.get('name')} (id={p.get('id')})" for p in providers) or "none"
        _log(f"  Shipping providers: {names}", f"  [dim]Shipping providers: {names}[/]")
    except Exception as e:
        _log(
            f"  Warning: could not pre-fetch shipping providers — will try per-order: {e}",
            f"  [yellow]Warning:[/] could not pre-fetch shipping providers: {e}",
        )

    start_ts = datetime.now(timezone.utc).timestamp()
    deadline_ts = start_ts + timeout_minutes * 60
    round_num = 0

    _log(
        f"\nPolling for shipping labels — timeout {timeout_minutes} min, "
        f"check every {poll_interval_seconds}s",
        f"\n[cyan]Polling for shipping labels[/] "
        f"[dim]— timeout {timeout_minutes} min, check every {poll_interval_seconds}s[/]",
    )

    while pending:
        round_num += 1

        # Determine wait before this round
        if round_num == 1 and not initial_wait:
            wait_secs = 0
        else:
            now_ts = datetime.now(timezone.utc).timestamp()
            remaining = deadline_ts - now_ts
            if remaining <= 0:
                break
            wait_secs = min(poll_interval_seconds, int(remaining))

        if wait_secs > 0:
            _log(
                f"  Waiting {wait_secs}s for Billbee to assign package types...",
                f"  [dim]Waiting {wait_secs}s for Billbee to assign package types...[/]",
            )
            time.sleep(wait_secs)

        now_ts = datetime.now(timezone.utc).timestamp()
        if now_ts >= deadline_ts:
            break

        elapsed = int(now_ts - start_ts)
        elapsed_str = f"{elapsed // 60}:{elapsed % 60:02d}"
        _log(
            f"\n[Round {round_num} — {elapsed_str} elapsed — {len(pending)} order(s) pending]",
            f"\n[bold cyan][Round {round_num}][/] "
            f"[dim]{elapsed_str} elapsed — {len(pending)} order(s) pending[/]",
        )

        resolved_this_round: set[str] = set()
        no_pkg_count = 0
        retry_count = 0

        for order_number, info in list(pending.items()):

            # Re-fetch to get the latest tags / shipping profile from Billbee
            try:
                fresh_order = client.get_order(info["order_id"])
            except Exception as e:
                info["last_error"] = f"Could not re-fetch order: {e}"
                retry_count += 1
                _log(
                    f"  {order_number}  ERROR fetching order — will retry: {e}",
                    f"  [red]{order_number}[/]  ERROR fetching order — will retry: {e}",
                )
                continue

            pkg_tag = _get_package_type_tag(fresh_order)
            if not pkg_tag:
                info["no_pkg_type"] = True
                info["last_error"] = None  # expected — not an error yet
                no_pkg_count += 1
                _log(
                    f"  {order_number}  waiting — no Verpackungstyp tag yet",
                    f"  [yellow]{order_number}[/]  [dim]waiting — no Verpackungstyp tag yet[/]",
                )
                continue

            info["no_pkg_type"] = False
            _log(
                f"  {order_number}  {pkg_tag}  -> creating label...",
                f"  [cyan]{order_number}[/]  [dim]{pkg_tag}[/]  -> creating label...",
            )

            # Resolve provider/product for this order
            try:
                pid, ppid = _resolve_from_list(providers, provider_id, product_id, fresh_order)
            except Exception as e:
                info["last_error"] = f"Provider resolution error: {e}"
                retry_count += 1
                _log(f"    FAILED (provider): {e}", f"    [red]FAILED (provider):[/] {e}")
                continue

            if not pid or not ppid:
                # Config issue — retrying won't help; fail permanently
                err = (
                    f"Could not resolve shipping provider "
                    f"(ShippingProviderName={fresh_order.get('ShippingProviderName')!r}, "
                    f"ShippingProviderProductName={fresh_order.get('ShippingProviderProductName')!r}). "
                    "Set shipping_provider_id / shipping_provider_product_id in config/settings.yaml."
                )
                _log(f"    FAILED (config): {err}", f"    [red]FAILED (config):[/] {err}")
                stats["failed"] += 1
                stats["errors"].append({
                    "order_number": order_number,
                    "operation": "label creation",
                    "error": err,
                })
                resolved_this_round.add(order_number)
                continue

            # Attempt label creation — transient errors keep order in pending
            try:
                result = _create_label(client, fresh_order, output_dir,
                                       provider_id=pid, product_id=ppid)
                if result:
                    tracking = result["tracking_id"] or "—"
                    path = result["path"]
                    _log(
                        f"    OK  {path.name}  tracking={tracking}",
                        f"    [green]OK[/] {path.name}  [dim]tracking={tracking}[/]",
                    )
                    stats["created"] += 1
                    resolved_this_round.add(order_number)

                    if after_label_state:
                        try:
                            client.set_order_state(info["order_id"], after_label_state)
                            _log(
                                f"    State -> {after_label_state}",
                                f"    [dim]State -> {after_label_state}[/]",
                            )
                        except Exception as se:
                            err = (f"Label created but could not set order state "
                                   f"to {after_label_state}: {se}")
                            _log(f"    ERROR: {err}", f"    [red]ERROR:[/] {err}")
                            stats["errors"].append({
                                "order_number": order_number,
                                "operation": f"set state -> {after_label_state} after label",
                                "error": err,
                            })
                else:
                    # No PDF returned — order already has a label; treat as done
                    _log(
                        f"    -- No PDF returned (order already has a label — skipping)",
                        f"    [yellow]--[/] [dim]No PDF returned (already labeled — skipping)[/]",
                    )
                    stats["skipped"] += 1
                    resolved_this_round.add(order_number)

            except Exception as e:
                # Transient error (network, Billbee outage, etc.) — keep in pending
                info["last_error"] = str(e)
                retry_count += 1
                _log(
                    f"    FAILED — will retry: {e}",
                    f"    [red]FAILED[/] — will retry: {e}",
                )

        # Remove resolved orders from pending
        for num in resolved_this_round:
            del pending[num]

        if pending:
            parts = []
            if no_pkg_count:
                parts.append(f"{no_pkg_count} waiting for pkg type")
            if retry_count:
                parts.append(f"{retry_count} retrying after error")
            suffix = f" ({', '.join(parts)})" if parts else ""
            _log(
                f"  {len(pending)} order(s) still pending{suffix}",
                f"  [dim]{len(pending)} order(s) still pending{suffix}[/]",
            )

    # ── Timeout reached — report anything still unresolved ──────────────────
    if pending:
        elapsed = int(datetime.now(timezone.utc).timestamp() - start_ts)
        _log(
            f"\nTimeout after {elapsed // 60}:{elapsed % 60:02d}. "
            f"{len(pending)} order(s) could not be processed:",
            f"\n[yellow bold]Timeout after {elapsed // 60}:{elapsed % 60:02d}. "
            f"{len(pending)} order(s) could not be processed:[/]",
        )
        for order_number, info in pending.items():
            if info["no_pkg_type"] or not info["last_error"]:
                reason = "no Verpackungstyp tag was set within the timeout (package type unknown)"
            else:
                reason = info["last_error"]
            _log(
                f"  {order_number}  {reason}",
                f"  [red]{order_number}[/]  {reason}",
            )
            stats["failed"] += 1
            stats["errors"].append({
                "order_number": order_number,
                "operation": "label creation",
                "error": reason,
            })

    return stats
