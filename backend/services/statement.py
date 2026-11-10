"""Buyer statement read model + split preview (FR-3, FR-4; P2 D2, D3).

Our ledger is the only source: no PayPal call on page load (ledger rule). Every derived
number (due, owed, past due, ages, overdue days, credit) is computed here, never stored.
One query per table — no N+1.
"""

import hashlib
import logging
from datetime import date, timedelta

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.core.config import Settings
from backend.core.dates import age_days, days_overdue
from backend.core.split import OpenInvoice, resolve_request, split_payment
from backend.db.models import (
    Allocation,
    Buyer,
    Invoice,
    InvoiceLine,
    Memo,
    MemoLine,
    Payment,
    Proposal,
)

log = logging.getLogger("statement")

LISTED = {"CAPTURED", "CAPTURE_PENDING", "RECORDED"}  # CREATED / VOIDED / FAILED never listed
NOTICE_DAYS = 7


class StatementNotFoundError(Exception):
    pass


def token_ref(token: str) -> str:
    """What logs may carry instead of a statement token (D14)."""
    return hashlib.sha256(token.encode()).hexdigest()[:8]


# ---------- response shapes (BACKEND_SCHEMA §3 + P2 D2) ----------


class Party(BaseModel):
    name: str


class LineOut(BaseModel):
    sku: str
    description: str
    price_cents: int
    status: str  # OUT | RETURNED | KEPT
    returned_on: date | None
    return_ref: str | None
    due_back_on: date | None  # memo's due-back date, OUT lines only
    invoice_no: str | None  # KEPT lines only


class MemoOut(BaseModel):
    memo_no: str
    sent_on: date
    lines: list[LineOut]


class InvoiceOut(BaseModel):
    id: int
    invoice_no: str
    memo_no: str
    total_cents: int
    paid_cents: int
    due_cents: int
    issued_on: date
    due_on: date
    ledger_status: str
    days_overdue: int
    paypal_url: str | None


class Applied(BaseModel):
    invoice_no: str
    amount_cents: int


class PaymentOut(BaseModel):
    id: int
    source: str
    status: str
    method: str | None
    reference: str | None
    amount_cents: int
    received_at: date | None
    applied: list[Applied]


class Notice(BaseModel):
    kind: str  # PAYMENT_VOIDED | PAYMENT_CREDITED | RECORDING_PENDING
    payment_id: int
    at: date
    amount_cents: int | None = None


class DemoLogin(BaseModel):
    email: str
    password: str


class Statement(BaseModel):
    buyer: Party
    supplier: Party
    owed_cents: int
    oldest_age_days: int
    past_due_cents: int
    memos: list[MemoOut]
    invoices: list[InvoiceOut]
    payments: list[PaymentOut]
    credit_cents: int
    notices: list[Notice]
    pending_proposals: int
    demo_login: DemoLogin | None = None


class PreviewAllocation(BaseModel):
    invoice_id: int
    invoice_no: str
    amount_cents: int


class Remaining(BaseModel):
    invoice_no: str
    due_cents: int


class Preview(BaseModel):
    allocations: list[PreviewAllocation]
    total_cents: int
    remaining_after: list[Remaining]


# ---------- queries ----------


def find_buyer(session: Session, token: str) -> Buyer:
    """The token's buyer; demo copies only once ready (P1 D9). Else StatementNotFoundError."""
    buyer = session.scalars(select(Buyer).where(Buyer.statement_token == token)).first()
    if buyer is None or (buyer.is_demo and not buyer.demo_ready):
        log.info("statement not found token=%s", token_ref(token))
        raise StatementNotFoundError
    return buyer


def open_invoices(session: Session, buyer_id: int) -> tuple[list[Invoice], list[OpenInvoice]]:
    rows = list(
        session.scalars(
            select(Invoice)
            .where(Invoice.buyer_id == buyer_id, Invoice.ledger_status != "CANCELLED")
            .order_by(Invoice.due_on, Invoice.issued_on, Invoice.id)
        )
    )
    return rows, [
        OpenInvoice(i.id, i.due_on, i.issued_on, i.total_cents - i.paid_cents) for i in rows
    ]


def get_statement(session: Session, token: str, today: date, settings: Settings) -> Statement:
    buyer = find_buyer(session, token)
    memos = list(
        session.scalars(
            select(Memo).where(Memo.buyer_id == buyer.id).order_by(Memo.sent_on, Memo.memo_no)
        )
    )
    memo_no = {m.id: m.memo_no for m in memos}
    lines = list(
        session.scalars(
            select(MemoLine)
            .join(Memo, MemoLine.memo_id == Memo.id)
            .where(Memo.buyer_id == buyer.id)
            .order_by(MemoLine.sku)
        )
    )
    invoices, _ = open_invoices(session, buyer.id)
    inv_no = {i.id: i.erp_invoice_no for i in invoices}
    line_invoice = dict(
        session.execute(
            select(InvoiceLine.memo_line_id, InvoiceLine.invoice_id)
            .join(Invoice, InvoiceLine.invoice_id == Invoice.id)
            .where(Invoice.buyer_id == buyer.id, InvoiceLine.memo_line_id.is_not(None))
        ).all()
    )
    payments = list(
        session.scalars(
            select(Payment).where(Payment.buyer_id == buyer.id).order_by(Payment.created_at)
        )
    )
    allocations = list(
        session.scalars(
            select(Allocation)
            .join(Payment, Allocation.payment_id == Payment.id)
            .where(Payment.buyer_id == buyer.id)
            .order_by(Allocation.id)
        )
    )
    pending = session.scalar(
        select(func.count())
        .select_from(Proposal)
        .where(Proposal.buyer_id == buyer.id, Proposal.status == "PENDING")
    )

    by_memo: dict[int, list[LineOut]] = {}
    memo_due_back = {m.id: m.due_back_on for m in memos}
    for ln in lines:
        by_memo.setdefault(ln.memo_id, []).append(
            LineOut(
                sku=ln.sku,
                description=ln.description,
                price_cents=ln.price_cents,
                status=ln.status,
                returned_on=ln.returned_on,
                return_ref=ln.return_ref,
                due_back_on=memo_due_back[ln.memo_id] if ln.status == "OUT" else None,
                invoice_no=inv_no.get(line_invoice.get(ln.id, -1)) if ln.status == "KEPT" else None,
            )
        )

    inv_out = []
    for i in invoices:
        due = i.total_cents - i.paid_cents
        inv_out.append(
            InvoiceOut(
                id=i.id,
                invoice_no=i.erp_invoice_no,
                memo_no=memo_no.get(i.memo_id, ""),
                total_cents=i.total_cents,
                paid_cents=i.paid_cents,
                due_cents=due,
                issued_on=i.issued_on,
                due_on=i.due_on,
                ledger_status=i.ledger_status,
                days_overdue=days_overdue(i.due_on, today) if due > 0 else 0,
                paypal_url=i.payer_view_url,
            )
        )
    open_ = [i for i in inv_out if i.due_cents > 0]

    allocs_of: dict[int, list[Allocation]] = {}
    for a in allocations:
        allocs_of.setdefault(a.payment_id, []).append(a)
    pay_out, notices = [], []
    since = today - timedelta(days=NOTICE_DAYS)
    for p in payments:
        recent = p.updated_at.date() >= since
        at = p.updated_at.date()
        if p.status in LISTED:
            pay_out.append(
                PaymentOut(
                    id=p.id,
                    source=p.source,
                    status=p.status,
                    method=p.method,
                    reference=p.reference,
                    amount_cents=p.amount_cents,
                    received_at=p.received_at.date() if p.received_at else None,
                    applied=[
                        Applied(
                            invoice_no=inv_no.get(a.invoice_id, ""), amount_cents=a.amount_cents
                        )
                        for a in allocs_of.get(p.id, [])
                    ],
                )
            )
        if not recent:
            continue
        if p.status == "VOIDED" and p.source == "STATEMENT":
            notices.append(Notice(kind="PAYMENT_VOIDED", payment_id=p.id, at=at))
        if p.status in ("CAPTURED", "RECORDED") and any(
            a.record_state != "RECORDED" for a in allocs_of.get(p.id, [])
        ):
            notices.append(Notice(kind="RECORDING_PENDING", payment_id=p.id, at=at))
        if p.unapplied_cents > 0:
            notices.append(
                Notice(
                    kind="PAYMENT_CREDITED", payment_id=p.id, at=at, amount_cents=p.unapplied_cents
                )
            )

    demo_login = None
    if settings.demo_mode and buyer.is_demo and settings.sandbox_buyer_email:
        pw = settings.sandbox_buyer_password
        demo_login = DemoLogin(
            email=settings.sandbox_buyer_email, password=pw.get_secret_value() if pw else ""
        )

    return Statement(
        buyer=Party(name=buyer.name),
        supplier=Party(name=settings.supplier_name),
        owed_cents=sum(i.due_cents for i in open_),
        oldest_age_days=max((age_days(i.issued_on, today) for i in open_), default=0),
        past_due_cents=sum(i.due_cents for i in open_ if i.due_on < today),
        memos=[
            MemoOut(memo_no=m.memo_no, sent_on=m.sent_on, lines=by_memo.get(m.id, []))
            for m in memos
        ],
        invoices=inv_out,
        payments=pay_out,
        credit_cents=sum(p.unapplied_cents for p in payments if p.status in LISTED),
        notices=notices,
        pending_proposals=pending or 0,
        demo_login=demo_login,
    )


def preview(
    session: Session, token: str, invoice_ids: list[int] | None, amount_cents: int | None
) -> Preview:
    """Split on current balances; no writes (D3). Raises SplitError (→ 422)."""
    buyer = find_buyer(session, token)
    rows, open_ = open_invoices(session, buyer.id)
    amount, selected = resolve_request(open_, invoice_ids, amount_cents)
    split = split_payment(amount, open_, selected)
    by_id = {i.id: i for i in rows}
    return Preview(
        allocations=[
            PreviewAllocation(invoice_id=i, invoice_no=by_id[i].erp_invoice_no, amount_cents=c)
            for i, c in split
        ],
        total_cents=amount,
        remaining_after=[
            Remaining(
                invoice_no=by_id[i].erp_invoice_no,
                due_cents=by_id[i].total_cents - by_id[i].paid_cents - c,
            )
            for i, c in split
        ],
    )


# ---------- receipt (P2 D11) ----------


class ReceiptAllocation(BaseModel):
    invoice_no: str
    amount_cents: int
    recorded: bool
    record_state: str  # PENDING | RECORDING | RECORDED | FAILED
    due_after_cents: int  # the invoice's due now, per our ledger
    paypal_url: str | None


class Receipt(BaseModel):
    payment_id: int
    status: str  # CAPTURED | CAPTURE_PENDING
    paypal_capture_id: str | None
    amount_cents: int
    received_at: date | None
    allocations: list[ReceiptAllocation]
    new_owed_cents: int
    credit_cents: int  # this payment's unapplied money
    resplit: bool  # allocations differ from the split shown when ordering (R15)


def get_receipt(session: Session, token: str, payment_id: int) -> Receipt:
    """Scoped to the token's buyer; only payments that reached PayPal capture have one."""
    buyer = find_buyer(session, token)
    p = session.get(Payment, payment_id)
    if p is None or p.buyer_id != buyer.id or p.status not in ("CAPTURED", "CAPTURE_PENDING"):
        raise StatementNotFoundError
    rows, _ = open_invoices(session, buyer.id)
    by_id = {i.id: i for i in rows}
    allocs = list(
        session.scalars(
            select(Allocation).where(Allocation.payment_id == p.id).order_by(Allocation.id)
        )
    )
    planned = {(int(x["invoice_id"]), int(x["amount_cents"])) for x in p.planned_split or []}
    actual = {(a.invoice_id, a.amount_cents) for a in allocs}
    return Receipt(
        payment_id=p.id,
        status=p.status,
        paypal_capture_id=p.paypal_capture_id,
        amount_cents=p.amount_cents,
        received_at=p.received_at.date() if p.received_at else None,
        allocations=[
            ReceiptAllocation(
                invoice_no=by_id[a.invoice_id].erp_invoice_no,
                amount_cents=a.amount_cents,
                recorded=a.record_state == "RECORDED",
                record_state=a.record_state,
                due_after_cents=by_id[a.invoice_id].total_cents - by_id[a.invoice_id].paid_cents,
                paypal_url=by_id[a.invoice_id].payer_view_url,
            )
            for a in allocs
        ],
        new_owed_cents=sum(max(0, i.total_cents - i.paid_cents) for i in rows),
        credit_cents=p.unapplied_cents,
        resplit=bool(allocs) and actual != planned,
    )
