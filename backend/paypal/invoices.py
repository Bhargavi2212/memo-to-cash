"""PayPal Invoicing v2 calls (FR-2, FR-15; P1 D5, D7, D22, D26).

Not covered by APIMatic ContextMatic (docs/APIMATIC.md "Coverage"): built from the
sandbox-proven facts in CLAUDE.md §4 and PayPal's REST docs; field paths confirmed by the
P1 open-checks sandbox run.

`build_body` is pure and takes an `InvoiceDraft` (plain data, never ORM rows — review R1).
"""

import uuid
from dataclasses import dataclass
from datetime import date
from typing import Any

from backend.core.config import Settings
from backend.core.dates import today_ny
from backend.core.money import fmt_paypal
from backend.paypal.client import PayPal, PayPalError, request_id

BASE = "/v2/invoicing/invoices"
CURRENCY = "USD"
# Open check 11 (sandbox): a duplicate number is HTTP 422 name=UNPROCESSABLE_ENTITY with
# details[].issue == DUPLICATE_INVOICE_NUMBER — match on the issue, never on `name`.
DUPLICATE_INVOICE_NUMBER = "DUPLICATE_INVOICE_NUMBER"
RECORD_METHODS = {"PAYPAL", "CHECK", "WIRE_TRANSFER", "BANK_TRANSFER", "CASH", "OTHER"}


@dataclass(frozen=True)
class DraftLine:
    sku: str
    description: str
    amount_cents: int


@dataclass(frozen=True)
class InvoiceDraft:
    uid: str  # invoices.uid — used in PayPal-Request-Id keys
    paypal_invoice_number: str  # e.g. INV-1038-k3x9q2
    erp_invoice_no: str  # e.g. INV-1038
    memo_no: str  # e.g. M-201
    issued_on: date
    due_on: date
    lines: tuple[DraftLine, ...]
    buyer_name: str
    buyer_email: str
    statement_token: str


class NotConfiguredError(RuntimeError):
    def __init__(self, missing: list[str]):
        super().__init__(f"settings missing: {', '.join(missing)}")
        self.missing = missing


def money(cents: int) -> dict[str, str]:
    return {"currency_code": CURRENCY, "value": fmt_paypal(cents)}


def statement_url(settings: Settings, token: str) -> str:
    return f"{settings.app_base_url}/s/{token}"


def expected_dates(draft: InvoiceDraft, settings: Settings, today: date) -> tuple[date, date]:
    """The (invoice_date, due_date) PayPal gets. D22 fallback: if PayPal rejects past dates,
    send today and max(today, due); our own dates stay the truth on our screens."""
    if settings.paypal_past_dates_ok:
        return draft.issued_on, draft.due_on
    return today, max(today, draft.due_on)


def build_body(
    draft: InvoiceDraft, settings: Settings, today: date | None = None
) -> dict[str, Any]:
    missing = [
        name
        for name, value in (
            ("APP_BASE_URL", settings.app_base_url),
            ("PAYPAL_BUSINESS_EMAIL", settings.paypal_business_email),
        )
        if not value
    ]
    if missing:
        raise NotConfiguredError(missing)
    invoice_date, due_date = expected_dates(draft, settings, today or today_ny())
    return {
        "detail": {
            "currency_code": CURRENCY,
            "invoice_number": draft.paypal_invoice_number,
            "reference": draft.memo_no,
            "invoice_date": invoice_date.isoformat(),
            "payment_term": {
                "term_type": "DUE_ON_DATE_SPECIFIED",
                "due_date": due_date.isoformat(),
            },
            # memo = private to the merchant; note = visible to the buyer (D7, FR-15)
            "memo": f"Kept from memo {draft.memo_no} · ERP {draft.erp_invoice_no}",
            "note": (
                "See everything you owe and pay several invoices at once: "
                + statement_url(settings, draft.statement_token)
            ),
        },
        "invoicer": {
            "email_address": settings.paypal_business_email,
            "business_name": settings.supplier_name,
        },
        "primary_recipients": [
            {
                "billing_info": {
                    "business_name": draft.buyer_name,
                    "email_address": draft.buyer_email,
                }
            }
        ],
        "items": [
            {
                "name": f"{line.sku} {line.description} (memo {draft.memo_no})",
                "description": f"SKU {line.sku} · memo {draft.memo_no}",
                "quantity": "1",
                "unit_amount": money(line.amount_cents),
            }
            for line in draft.lines
        ],
        "configuration": {
            "partial_payment": {"allow_partial_payment": True, "minimum_amount_due": money(100)}
        },
    }


def is_duplicate_number(e: PayPalError) -> bool:
    return e.status == 422 and any(
        isinstance(d, dict) and d.get("issue") == DUPLICATE_INVOICE_NUMBER for d in e.details
    )


def payer_url_from_send(body: dict[str, Any]) -> str | None:
    """Open check R6: send answers with the payer link itself `{rel: payer-view, href}`,
    so no GET is needed after send (2 PayPal calls per invoice, not 3)."""
    if body.get("rel") == "payer-view" and body.get("href"):
        return str(body["href"])
    for link in body.get("links") or []:
        if link.get("rel") == "payer-view":
            return str(link.get("href"))
    return None


def invoice_id_from_href(href: str) -> str:
    """Create returns a link; the id is its last path segment (CLAUDE.md §4)."""
    return href.rstrip("/").rsplit("/", 1)[-1]


async def create(pp: PayPal, draft: InvoiceDraft, *, today: date | None = None) -> dict[str, Any]:
    """POST create. Returns the full invoice (`Prefer: return=representation` — confirmed in
    the sandbox, R6); falls back to the href link body. Open check 10: PayPal does NOT honour
    a repeated Request-Id here — a repeat fails as a duplicate number, so callers resume with
    `search_by_number` (P1 D4)."""
    body = await pp.request(
        "POST",
        BASE,
        json=build_body(draft, pp.settings, today),
        request_id=request_id(pp.settings, "inv", draft.uid),
        headers={"Prefer": "return=representation"},
    )
    if "id" not in body and body.get("href"):
        body = {**body, "id": invoice_id_from_href(body["href"])}
    return body


async def send(pp: PayPal, invoice_id: str, uid: str, attempt: int | None = None) -> dict[str, Any]:
    """Send without PayPal emailing the buyer; our link is the channel (CLAUDE.md §4)."""
    return await pp.request(
        "POST",
        f"{BASE}/{invoice_id}/send",
        json={"send_to_recipient": False, "send_to_invoicer": False},
        request_id=request_id(pp.settings, "send", uid, attempt),
    )


async def get(pp: PayPal, invoice_id: str) -> dict[str, Any]:
    return await pp.request("GET", f"{BASE}/{invoice_id}")


async def search_by_number(pp: PayPal, paypal_invoice_number: str) -> dict[str, Any] | None:
    """Find our invoice by its unique number (resume after a lost create, D4)."""
    body = await pp.request(
        "POST",
        "/v2/invoicing/search-invoices?page=1&page_size=10",
        json={"invoice_number": paypal_invoice_number},
        # A fresh key every time: PayPal would replay a cached (possibly empty) result for
        # up to 6 h if a search reused its Request-Id (TRD §6b-5).
        request_id=request_id(pp.settings, "search", uuid.uuid4()),
    )
    for item in body.get("items") or []:
        if (item.get("detail") or {}).get("invoice_number") == paypal_invoice_number:
            return item
    return None


def record_payment_body(
    *, amount_cents: int, method: str, payment_date: date, note: str
) -> dict[str, Any]:
    """No `payment_id` — a checkout capture id there gives RESOURCE_NOT_FOUND (CLAUDE.md §4)."""
    if method not in RECORD_METHODS:
        raise ValueError(f"unknown record-payment method {method!r}")
    if not isinstance(amount_cents, int) or amount_cents <= 0:
        raise ValueError("amount_cents must be a positive int")
    return {
        "method": method,
        "payment_date": payment_date.isoformat(),
        "amount": money(amount_cents),
        "note": note,
    }


async def record_payment(
    pp: PayPal,
    invoice_id: str,
    *,
    allocation_uid: str,
    amount_cents: int,
    method: str,
    payment_date: date,
    note: str,
) -> dict[str, Any]:
    """Record a payment we already hold. The caller puts `[MTC:<allocation uid>]` in `note`
    (invariant 10) — that token, not the Request-Id, is how retries find it (TRD §6b-5)."""
    if f"[MTC:{allocation_uid}]" not in note:
        raise ValueError("record-payment note must carry the [MTC:<allocation uid>] token")
    return await pp.request(
        "POST",
        f"{BASE}/{invoice_id}/payments",
        json=record_payment_body(
            amount_cents=amount_cents, method=method, payment_date=payment_date, note=note
        ),
        request_id=request_id(pp.settings, "alloc", allocation_uid),
        # NO automatic retries: PayPal ignored a repeated Request-Id on invoice create (open
        # check 10), so a blind retry after a lost answer could record the money twice.
        # Retries go through payments.resolve_recording, which first looks for our
        # [MTC:<uid>] note on the invoice (TRD §6b-3).
        retries=0,
    )


# ---------- approved adjustments (P4 X2 / step 5b; FR-10) ----------

# CLAUDE.md §4 (proven P1 #1): GET → drop PayPal's computed fields → change items → PUT.
# Sending stale totals gives calculation_error. PUT works on PARTIALLY_PAID (P1 open check).
STRIP = ("amount", "due_amount", "payments", "refunds", "links", "status", "gratuity")


def adjustment_tag(proposal_uid: str) -> str:
    """Marks our line on the PayPal invoice, so a retried approve never adds it twice."""
    return f"[MTC-ADJ:{proposal_uid}]"


def adjustment_body(
    current: dict[str, Any], *, proposal_uid: str, sku: str, amount_cents: int, reason: str
) -> dict[str, Any] | None:
    """The PUT body: the invoice as PayPal has it, minus computed fields, plus one negative
    line. None when the line is already there (a retry after a lost answer)."""
    if not isinstance(amount_cents, int) or amount_cents >= 0:
        raise ValueError("an adjustment is a negative int of cents")
    tag = adjustment_tag(proposal_uid)
    items = list(current.get("items") or [])
    if any(tag in (i.get("description") or "") for i in items):
        return None
    body = {k: v for k, v in current.items() if k not in STRIP}
    label = f"Adjustment {sku}" + (f": {reason}" if reason else "")
    items.append(
        {
            "name": label[:200],
            "description": f"Approved by the supplier {tag}",
            "quantity": "1",
            "unit_amount": money(amount_cents),
        }
    )
    body["items"] = items
    return body


async def put_adjustment(
    pp: PayPal, invoice_id: str, *, proposal_uid: str, sku: str, amount_cents: int, reason: str
) -> dict[str, Any]:
    """Add an approved deduction line to the PayPal invoice; returns the invoice afterwards
    (GET — PayPal answers a PUT with a link only). Idempotent via the line's tag."""
    current = await get(pp, invoice_id)
    body = adjustment_body(
        current, proposal_uid=proposal_uid, sku=sku, amount_cents=amount_cents, reason=reason
    )
    if body is not None:
        await pp.request(
            "PUT",
            f"{BASE}/{invoice_id}?send_to_recipient=false&send_to_invoicer=false",
            json=body,
            request_id=request_id(pp.settings, "adj", proposal_uid),
        )
    return await get(pp, invoice_id)


async def remind(pp: PayPal, invoice_id: str, *, note: str, request_key: str) -> None:
    """`POST /v2/invoicing/invoices/{id}/remind` (proved in P3b, CLAUDE §4): the recipient only,
    empty 2xx. PayPal allows 2 a day per invoice — the sandbox doesn't enforce it, our
    `reminders_sent` count does (P3b B6). `request_key` = `<invoice uid>-<YYYYMMDD>-<n>`."""
    await pp.request(
        "POST",
        f"/v2/invoicing/invoices/{invoice_id}/remind",
        json={
            "subject": "Payment reminder",
            "note": note,
            "send_to_invoicer": False,
            "send_to_recipient": True,
        },
        request_id=request_id(pp.settings, "rem", request_key),
    )
