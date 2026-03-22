"""
Billbee API client for the fixBillbeeAdresses project.

Extends the shared BillbeeClient from the sibling billbee-python-client repo with an
order address update method.

Usage:
    from execution.billbee_client import BillbeeClient
    client = BillbeeClient()
    client.update_order_address(order_id=12345, new_address={...})

Field name mapping (Billbee API ↔ our code):
    Billbee returns "Line2" for address addition; we normalize to "AddressAddition"
    on read and map back to "Line2" on write.
"""

import sys
from pathlib import Path

# Pull in the shared client from the sibling repo (../billbee-python-client relative to this project)
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "billbee-python-client"))
from billbee_client import BillbeeClient as _BaseBillbeeClient  # noqa: E402


class BillbeeClient(_BaseBillbeeClient):
    """BillbeeClient extended with order address update support."""

    # -----------------------------------------------------------------------
    # Field name normalization
    # -----------------------------------------------------------------------
    # Billbee's API returns "Line2" for the address addition field in both
    # ShippingAddress and InvoiceAddress.  Our codebase calls it "AddressAddition"
    # (the field name used by the order-creation endpoint and our UI).
    # _normalize_order_addresses() translates in-place on every order we read
    # so that the rest of the code only ever sees "AddressAddition".

    @staticmethod
    def _normalize_order_addresses(order: dict) -> dict:
        """Translate Line2 → AddressAddition in ShippingAddress / InvoiceAddress."""
        for key in ("ShippingAddress", "InvoiceAddress"):
            addr = order.get(key) or {}
            if "Line2" in addr and "AddressAddition" not in addr:
                addr["AddressAddition"] = addr.pop("Line2")
        return order

    # -----------------------------------------------------------------------
    # Order retrieval (overrides base to apply normalization)
    # -----------------------------------------------------------------------

    def get_order(self, order_id: int) -> dict:
        """
        Fetch a single order by its Billbee internal ID (BillBeeOrderId).
        Returns the order dict (Data field), with address fields normalized.
        """
        data = self._get(f"/orders/{order_id}")
        return self._normalize_order_addresses(data.get("Data", {}))

    def get_orders(self, **kwargs):
        """
        Generator — yields one normalized order dict at a time.
        Wraps the base generator to apply address field normalization.
        """
        for order in super().get_orders(**kwargs):
            yield self._normalize_order_addresses(order)

    # -----------------------------------------------------------------------
    # Package type (via tag)
    # -----------------------------------------------------------------------

    def set_order_package_type(self, order_id: int, order: dict,
                               pkg_id=None, pkg_name: str = "") -> None:
        """
        Record the Verpackungstyp (packaging type) on a Billbee order via an order tag.

        Billbee's Verpackungstypen are not accessible through the public REST API —
        neither the list nor the field can be read or written via API. The only reliable
        write path is adding an order tag so packers can see the decision in the order view.

        Implementation:
          POST /orders/{id}/tags with the new "Verpackungstyp: {pkg_name}" tag.
          Skips if the same tag already exists to avoid duplicates on re-runs.

        Parameters
        ----------
        order_id : int
            The BillBeeOrderId.
        order : dict
            The current full order dict (used to check existing tags).
        pkg_id : int or None
            Not used — kept for API compatibility. Billbee's packaging type IDs
            are not accessible via the REST API.
        pkg_name : str
            Human-readable package type name, e.g. "Kleinpaket-Max".
        """
        if not pkg_name:
            return

        tag_prefix = "Verpackungstyp:"
        existing_tags = order.get("Tags") or []

        # Skip if this exact tag is already set (idempotent on re-runs)
        desired_tag = f"{tag_prefix} {pkg_name}"
        if desired_tag in existing_tags:
            return

        # POST /orders/{id}/tags appends tags — send only the tags we want to add
        # (existing non-Verpackungstyp tags are untouched)
        self._post(f"/orders/{order_id}/tags", {"Tags": [desired_tag]})

    # -----------------------------------------------------------------------
    # Order state
    # -----------------------------------------------------------------------

    def find_order_by_number(self, order_number: str) -> dict | None:
        """
        Look up an order by its external order number (the human-readable ID shown in the UI).
        Uses POST /search with Type=["order"].
        Returns the full order dict, or None if not found.
        """
        results = self._post("/search", {"Term": order_number, "Type": ["order"]})
        orders = results.get("Orders") or []
        if not orders:
            return None
        # Search returns minimal dicts with Id; fetch the full order
        internal_id = orders[0].get("Id")
        if not internal_id:
            return None
        return self.get_order(int(internal_id))

    def set_order_state(self, order_id: int, state_id: int) -> None:
        """
        Change the state of an order.

        Confirmed working endpoint: PUT /orders/{BillBeeOrderId}/orderstate
        with body {"NewStateId": X}. Returns 200 with empty body on success.

        (PATCH /orders/{id} with {"OrderStateId": X} is NOT patchable — returns 400.)
        """
        self._throttle()
        resp = self.session.put(
            f"https://api.billbee.io/api/v1/orders/{order_id}/orderstate",
            json={"NewStateId": state_id},
        )
        resp.raise_for_status()

    # -----------------------------------------------------------------------
    # Shipment / label creation
    # -----------------------------------------------------------------------

    def get_shipping_providers(self) -> list:
        """
        Return the list of configured shipping providers (with their products).

        Endpoint: GET /shipment/shippingproviders
        Returns a list of provider dicts, each with 'id', 'name', and 'products'
        (list of {'id', 'displayName', 'productName'}).

        Use the provider/product 'id' values as ProviderId/ProductId in
        create_shipment_with_label().
        """
        result = self._get("/shipment/shippingproviders")
        # Response may be a list directly or wrapped in Data
        if isinstance(result, list):
            return result
        return result.get("Data") or []

    def create_shipment_with_label(self, order_id: int,
                                   provider_id: int,
                                   product_id: int,
                                   change_state_to_send: bool = False,
                                   weight_in_gram: int = 0) -> dict:
        """
        Create a carrier shipment label for an order via Billbee.

        Correct endpoint: POST /shipment/shipwithlabel
        This actually calls the carrier (e.g. DHL) and returns a PDF label.

        NOTE: POST /orders/{id}/shipment creates only a manual tracking record —
        it does NOT call the carrier and does NOT generate a label.

        Parameters
        ----------
        order_id : int
            The BillBeeOrderId (integer internal ID).
        provider_id : int
            Account-level shipping provider ID from GET /shipment/shippingproviders.
            This is NOT the master-data ID on the order (ShippingProviderId).
        product_id : int
            Product ID from the same providers endpoint.
        change_state_to_send : bool
            If True, marks the order as "Sent" after label creation.
        weight_in_gram : int
            Parcel weight in grams. 0 = use the weight on the order.

        Returns
        -------
        dict
            Response dict which may include:
              - ShippingId       tracking number
              - TrackingUrl      carrier tracking URL
              - LabelDataPdf     base64-encoded PDF of the shipping label
              - Carrier          carrier name string
              - ShippingDate     datetime string
        """
        body = {
            "OrderId": order_id,
            "ProviderId": provider_id,
            "ProductId": product_id,
            "ChangeStateToSend": change_state_to_send,
        }
        if weight_in_gram:
            body["WeightInGram"] = weight_in_gram
        resp = self._post("/shipment/shipwithlabel", body)
        return resp.get("Data") or resp or {}

    # -----------------------------------------------------------------------
    # Address update
    # -----------------------------------------------------------------------

    def update_order_address(self, order_id: int, new_address: dict) -> dict:
        """
        Update the ShippingAddress of an order in Billbee.

        Correct endpoint (confirmed from working n8n workflow):
            PATCH /customers/addresses/{BillbeeShippingAddressId}
        No customer ID is required in the path.

        Field name notes:
          - "Housenumber" (lowercase n) is the PATCH field name; GET returns "HouseNumber"
          - "Line2" is Billbee's PATCH field name for AddressAddition
          - InvoiceAddress is never sent and therefore never modified

        Parameters
        ----------
        order_id : int
            The BillBeeOrderId (integer internal ID, NOT the external order number).
        new_address : dict
            Partial ShippingAddress dict using our normalized field names, e.g.:
                Street, HouseNumber, AddressAddition, Zip, City, CountryISO2, …
            Field names are mapped to Billbee's PATCH names automatically.

        Returns
        -------
        dict
            The updated address dict returned by Billbee (Data field).
        """
        # Map from our normalized field names → Billbee PATCH /customers/addresses field names.
        # Confirmed from working n8n workflow (field names differ from GET response names).
        _FIELD_MAP = {
            "HouseNumber":    "Housenumber",   # GET returns HouseNumber, PATCH wants Housenumber
            "AddressAddition": "Line2",         # GET returns Line2 (normalized), PATCH wants Line2
        }
        # All fields accepted by PATCH /customers/addresses/{id}
        _WRITABLE = {
            "FirstName", "LastName", "Company",
            "Street", "Housenumber", "Line2",
            "Zip", "City", "CountryISO2",
            "Phone", "Email",
        }

        order = self.get_order(order_id)
        if not order:
            raise ValueError(f"Order {order_id} not found in Billbee")

        shipping_addr = order.get("ShippingAddress") or {}
        shipping_addr_id = shipping_addr.get("BillbeeId")

        if not shipping_addr_id:
            raise ValueError(f"Order {order_id}: missing ShippingAddress.BillbeeId")

        # Build the patch body: map field names, then filter to writable + non-empty
        patch = {}
        for k, v in new_address.items():
            patch[_FIELD_MAP.get(k, k)] = v

        body = {k: v for k, v in patch.items() if k in _WRITABLE and v not in (None, "")}

        # PATCH /customers/addresses/{id} — only the shipping address BillbeeId is
        # used; InvoiceAddress is a separate record and is never touched.
        try:
            result = self._request(
                "PATCH",
                f"/customers/addresses/{shipping_addr_id}",
                json=body,
            )
        except Exception as e:
            raise type(e)(f"{e}  |  body sent: {body}") from e
        return result.get("Data", {})
