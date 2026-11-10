"""PayPal Orders v2: one checkout for many invoices (FR-5; P2 D9, D10, R2, R3) [apimatic].

Field names and enums from APIMatic ContextMatic (docs/APIMATIC.md rows 4–8): OrderRequest,
PurchaseUnitRequest (items, amount.breakdown.item_total, custom_id, invoice_id,
description ≤ 127), PaypalWalletExperienceContext / VenmoWalletExperienceContext
(brand_name, shipping_preference NO_SHIPPING, user_action PAY_NOW), CaptureOrder
(Prefer: return=representation), capture statuses COMPLETED/PENDING/DECLINED/FAILED.
ContextMatic marks `application_context` DEPRECATED (use experience_context); we keep it only
for the guest-card path, which takes no payment_source (R3).
"""

from dataclasses import dataclass
from typing import Any

from backend.core.config import Settings
from backend.core.money import fmt_paypal
from backend.paypal.client import PayPal, PayPalError, request_id

BASE = "/v2/checkout/orders"
CURRENCY = "USD"
CAPTURE_TIMEOUT_S = 10.0  # runs under the buyer lock (D5, R7): one try, hard cap

# Capture outcomes by details[].issue — never by `name` (P1 lesson, open check 11).
ALREADY_CAPTURED = {"ORDER_ALREADY_CAPTURED"}
DECLINED = {
    "INSTRUMENT_DECLINED",
    "PAYER_ACTION_REQUIRED",
    "TRANSACTION_REFUSED",
    "MAX_NUMBER_OF_PAYMENT_ATTEMPTS_EXCEEDED",
}
NOT_APPROVED = {"ORDER_NOT_APPROVED"}
DUPLICATE_INVOICE_ID = {"DUPLICATE_INVOICE_ID"}

PAYPAL_FUNDING = {"paypal", "paylater", "credit"}  # → payment_source.paypal (R3)
VENMO_FUNDING = {"venmo"}  # → payment_source.venmo


@dataclass(frozen=True)
class OrderLine:
    invoice_no: str  # INV-1042
    memo_no: str  # M-207
    amount_cents: int


def _money(cents: int) -> dict[str, str]:
    return {"currency_code": CURRENCY, "value": fmt_paypal(cents)}


def _experience(settings: Settings) -> dict[str, str]:
    return {
        "brand_name": settings.supplier_name[:127],
        "shipping_preference": "NO_SHIPPING",  # judges are never asked for an address
        "user_action": "PAY_NOW",
    }


def build_order_body(
    payment_uid: str, lines: list[OrderLine], funding: str | None, settings: Settings
) -> dict[str, Any]:
    """One purchase unit; one item per invoice so PayPal's window shows the split (D9)."""
    if not lines:
        raise ValueError("an order needs at least one invoice line")
    if any(not isinstance(ln.amount_cents, int) or ln.amount_cents <= 0 for ln in lines):
        raise ValueError("line amounts must be positive int cents")
    total = sum(ln.amount_cents for ln in lines)
    body: dict[str, Any] = {
        "intent": "CAPTURE",
        "purchase_units": [
            {
                "custom_id": str(payment_uid),
                # unique per merchant → PayPal itself refuses a second charge for this payment
                "invoice_id": f"{settings.app_env}-stmt-{payment_uid}",
                "description": f"Statement payment · {settings.supplier_name}"[:127],
                "amount": {**_money(total), "breakdown": {"item_total": _money(total)}},
                "items": [
                    {
                        "name": f"{ln.invoice_no} (memo {ln.memo_no})"[:127],
                        "quantity": "1",
                        "unit_amount": _money(ln.amount_cents),
                    }
                    for ln in lines
                ],
            }
        ],
    }
    source = (funding or "").lower()
    if source in PAYPAL_FUNDING:
        body["payment_source"] = {"paypal": {"experience_context": _experience(settings)}}
    elif source in VENMO_FUNDING:
        body["payment_source"] = {"venmo": {"experience_context": _experience(settings)}}
    else:  # card or unknown: no payment_source; deprecated-but-supported application_context
        body["application_context"] = _experience(settings)
    return body


async def create(
    pp: PayPal, payment_uid: str, lines: list[OrderLine], funding: str | None
) -> dict[str, Any]:
    return await pp.request(
        "POST",
        BASE,
        json=build_order_body(payment_uid, lines, funding, pp.settings),
        request_id=request_id(pp.settings, "ord", payment_uid),
        headers={"Prefer": "return=representation"},
    )


def capture_request_id(settings: Settings, payment_uid: str, capture_attempts: int) -> str:
    """`<env>-cap-<uid>`; after a decline `<env>-cap-<uid>-<n>` (R2: PayPal may replay a stored
    decline for a reused id; ORDER_ALREADY_CAPTURED still prevents a double capture)."""
    return request_id(settings, "cap", payment_uid, capture_attempts or None)


async def capture(
    pp: PayPal, order_id: str, payment_uid: str, capture_attempts: int = 0
) -> dict[str, Any]:
    """One try, 10 s, no client retries (runs under the buyer lock)."""
    return await pp.request(
        "POST",
        f"{BASE}/{order_id}/capture",
        json={},
        request_id=capture_request_id(pp.settings, payment_uid, capture_attempts),
        headers={"Prefer": "return=representation"},
        timeout=CAPTURE_TIMEOUT_S,
        retries=0,
    )


async def get(pp: PayPal, order_id: str, *, timeout: float | None = None) -> dict[str, Any]:
    return await pp.request("GET", f"{BASE}/{order_id}", timeout=timeout, retries=0)


def approve_link(order: dict[str, Any]) -> str | None:
    """Where the buyer approves: `approve`, else `payer-action` (P2 plan step 5)."""
    links = {ln.get("rel"): ln.get("href") for ln in order.get("links") or []}
    return links.get("approve") or links.get("payer-action")


def first_capture(order: dict[str, Any]) -> dict[str, Any] | None:
    """purchase_units[0].payments.captures[0] (open check 12)."""
    units = order.get("purchase_units") or []
    caps = ((units[0].get("payments") or {}).get("captures") or []) if units else []
    return caps[0] if caps else None


def issues(e: PayPalError) -> set[str]:
    return {d.get("issue") for d in e.details if isinstance(d, dict) and d.get("issue")}


def classify(e: PayPalError) -> str:
    """ALREADY_CAPTURED | DECLINED | NOT_APPROVED | DUPLICATE_INVOICE_ID | TRANSIENT | REJECTED"""
    found = issues(e)
    if found & ALREADY_CAPTURED:
        return "ALREADY_CAPTURED"
    if found & DECLINED:
        return "DECLINED"
    if found & NOT_APPROVED:
        return "NOT_APPROVED"
    if found & DUPLICATE_INVOICE_ID:
        return "DUPLICATE_INVOICE_ID"
    if e.transient:
        return "TRANSIENT"
    return "REJECTED"
