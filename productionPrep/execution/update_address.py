"""
Apply an address fix to a Billbee order.

Merges the fix dict into the existing ShippingAddress and PUTs the order back.

Usage:
    from execution.update_address import apply_fix
    apply_fix(client, order_id=12345, fix={"Street": "Hauptstraße", "HouseNumber": "42"})
"""


def apply_fix(client, order_id: int, fix: dict, dry_run: bool = False) -> bool:
    """
    Update the ShippingAddress of a Billbee order.

    Parameters
    ----------
    client : BillbeeClient
        Authenticated Billbee client.
    order_id : int
        BillBeeOrderId (internal integer ID).
    fix : dict
        {field_name: new_value} pairs to apply.
    dry_run : bool
        If True, print what would be done without actually updating Billbee.

    Returns
    -------
    bool
        True on success, False on error.
    """
    if not fix:
        return False

    if dry_run:
        print(f"  [dry-run] Would update order {order_id} with: {fix}")
        return True

    try:
        client.update_order_address(order_id, fix)
        return True
    except Exception as e:
        print(f"  [error] Failed to update order {order_id}: {e}")
        return False
