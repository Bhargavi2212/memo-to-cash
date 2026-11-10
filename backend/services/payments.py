"""Statement payments: order → capture → finish_capture (FR-5; P2 D4–D6, D8, R1, R2, R7, R8).

Money rules (CLAUDE.md invariants 2, 3, 4, 9):
- every money write runs in one transaction that first takes `pg_advisory_xact_lock(buyer_id)`;
- the split is always `core.split`; allocations count the moment they're inserted
  (`invoices.paid_cents` += amount), PayPal recording only mirrors them (step 7);
- **capture is the one place the lock is held across a PayPal call** (split check → capture
  with a 10 s cap → allocations, one transaction). The OAuth token is fetched before the lock
  and any confirming GET happens after it is released (R7);
- a payment with money at PayPal is never marked VOIDED (R1): before voiding we GET the order;
- captured money that no longer fits becomes `unapplied_cents` credit, never lost (inv. 9).
"""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from backend.core.money import to_cents
from backend.core.split import OpenInvoice, SplitError, resolve_request, split_payment
from backend.db.models import Allocation, Invoice, Memo, Payment
from backend.paypal import orders
from backend.paypal.client import PayPal, PayPalError
from backend.services import audit
from backend.services.statement import StatementNotFoundError, find_buyer, open_invoices, token_ref

log = logging.getLogger("payments")

CAPTURE_CAP_S = 10.0  # total time the capture may hold the buyer lock (R7)
CONFIRM_GET_S = 5.0  # confirming GET after the lock is released (R7)
UNAVAILABLE = "PayPal is slow right now — please try again in a minute. You weren't charged."
UNCONFIRMED = "We couldn't confirm your payment yet — don't pay again. Tap “Check again”."
DECLINED_MSG = "PayPal declined that payment method. Please try another one — you weren't charged."
CHANGED = "Your statement changed — please review."


class PaymentError(Exception):
    """Mapped by routes to {error:{code,message,paypal_debug_id?}} (P2 D13)."""

    def __init__(self, status: int, code: str, message: str, debug_id: str | None = None):
        super().__init__(message)
        self.status, self.code, self.message, self.debug_id = status, code, message, debug_id


@dataclass
class OrderCreated:
    paypal_order_id: str
    payment_id: int


@dataclass
class CaptureResult:
    payment_id: int
    status: str  # CAPTURED | CAPTURE_PENDING


def lock_buyer(session: Session, buyer_id: int) -> None:
    session.execute(text("SELECT pg_advisory_xact_lock(:id)"), {"id": buyer_id})


# ---------------------------------------------------------------- order (D4)


def _order_txn(
    session_factory: Callable[[], Session],
    token: str,
    invoice_ids: list[int] | None,
    amount_cents: int | None,
) -> tuple[int, str, list[orders.OrderLine]]:
    with session_factory() as s, s.begin():
        buyer = find_buyer(s, token)
        lock_buyer(s, buyer.id)
        rows, open_ = open_invoices(s, buyer.id)
        amount, selected = resolve_request(open_, invoice_ids, amount_cents)
        split = split_payment(amount, open_, selected)
        by_id = {i.id: i for i in rows}
        memo_no = dict(
            s.execute(
                select(Memo.id, Memo.memo_no).where(
                    Memo.id.in_({by_id[i].memo_id for i, _ in split})
                )
            ).all()
        )
        p = Payment(
            buyer_id=buyer.id,
            source="STATEMENT",
            status="CREATED",
            amount_cents=amount,
            planned_split=[{"invoice_id": i, "amount_cents": c} for i, c in split],
        )
        s.add(p)
        s.flush()
        lines = [
            orders.OrderLine(by_id[i].erp_invoice_no, memo_no.get(by_id[i].memo_id, ""), c)
            for i, c in split
        ]
        return p.id, str(p.uid), lines


def _set_status(session_factory: Callable[[], Session], payment_id: int, **values: Any) -> None:
    with session_factory() as s, s.begin():
        p = s.get(Payment, payment_id)
        assert p is not None
        lock_buyer(s, p.buyer_id)
        for k, v in values.items():
            setattr(p, k, v)


async def create_order(
    session_factory: Callable[[], Session],
    pp: PayPal,
    token: str,
    invoice_ids: list[int] | None,
    amount_cents: int | None,
    funding: str | None,
) -> OrderCreated:
    """Txn 1 (lock): split + payment CREATED → commit → PayPal order → store its id (D4).
    Each click is a new payment; abandoned CREATED payments are invisible (P3 sweep expires)."""
    payment_id, uid, lines = await run_in_threadpool(
        _order_txn, session_factory, token, invoice_ids, amount_cents
    )
    try:
        order = await orders.create(pp, uid, lines, funding)
    except PayPalError as e:
        await run_in_threadpool(_set_status, session_factory, payment_id, status="FAILED")
        log.warning("order create failed payment=%s %s debug_id=%s", payment_id, e.name, e.debug_id)
        if e.transient:
            raise PaymentError(503, "PAYPAL_UNAVAILABLE", UNAVAILABLE) from e
        raise PaymentError(
            502, "PAYPAL_REJECTED", "PayPal couldn't start this payment.", e.debug_id
        ) from e
    await run_in_threadpool(_set_status, session_factory, payment_id, paypal_order_id=order["id"])
    log.info(
        "order created payment=%s order=%s token=%s", payment_id, order["id"], token_ref(token)
    )
    return OrderCreated(paypal_order_id=order["id"], payment_id=payment_id)


# ---------------------------------------------------------------- finish_capture (D6)


def _ledger_status(inv: Invoice) -> str:
    if inv.paid_cents >= inv.total_cents:
        return "PAID"
    return "PARTIALLY_PAID" if inv.paid_cents > 0 else "OPEN"


def finish_capture(session: Session, payment: Payment, capture: dict[str, Any]) -> None:
    """Idempotent; caller holds the buyer lock in an open transaction (shared with P3).

    COMPLETED → allocations by re-split over the planned invoices, oldest due first; what no
    longer fits becomes `unapplied_cents` credit. PENDING → CAPTURE_PENDING, no allocations.
    """
    if payment.status == "CAPTURED":
        return
    status = (capture or {}).get("status")
    cap_id = (capture or {}).get("id")
    if status == "PENDING":
        payment.status = "CAPTURE_PENDING"
        payment.paypal_capture_id = cap_id or payment.paypal_capture_id
        audit.log(session, "paypal", "payment.capture_pending", ref=str(payment.uid))
        return
    if status != "COMPLETED":
        raise ValueError(f"finish_capture needs a COMPLETED capture, got {status!r}")

    money = capture.get("amount") or {}
    if money.get("currency_code") != "USD":
        log.error("payment %s captured in %r — stored, nothing allocated", payment.id, money)
        captured = 0
    else:
        captured = to_cents(str(money.get("value")))
    if captured != payment.amount_cents:
        log.error(
            "payment %s captured %s cents, expected %s", payment.id, captured, payment.amount_cents
        )

    planned = [int(x["invoice_id"]) for x in payment.planned_split or []]
    invs = {
        i.id: i
        for i in session.scalars(
            select(Invoice).where(Invoice.id.in_(planned), Invoice.buyer_id == payment.buyer_id)
        )
    }
    open_ = [
        OpenInvoice(i.id, i.due_on, i.issued_on, i.total_cents - i.paid_cents)
        for i in invs.values()
    ]
    # never allocate more than we ordered (a different captured amount → credit, D6), nor more
    # than the planned invoices still owe (balances changed → credit, invariant 9)
    available = sum(o.balance_cents for o in open_ if o.balance_cents > 0)
    fits = min(captured, payment.amount_cents, available)
    split = split_payment(fits, open_, planned) if fits > 0 else []
    for invoice_id, cents in split:
        session.add(Allocation(payment_id=payment.id, invoice_id=invoice_id, amount_cents=cents))
        inv = invs[invoice_id]
        inv.paid_cents += cents
        inv.ledger_status = _ledger_status(inv)
    payment.status = "CAPTURED"
    payment.paypal_capture_id = cap_id
    payment.received_at = datetime.now(UTC)
    payment.unapplied_cents = max(0, captured - fits) if money.get("currency_code") == "USD" else 0
    if payment.unapplied_cents:
        log.warning("payment %s: %s cents kept as credit", payment.id, payment.unapplied_cents)
    audit.log(
        session,
        "paypal",
        "payment.captured",
        ref=str(payment.uid),
        detail={
            "payment_id": payment.id,
            "capture_id": cap_id,
            "allocations": [{"invoice_id": i, "amount_cents": c} for i, c in split],
            "unapplied_cents": payment.unapplied_cents,
        },
    )
    session.flush()


# ---------------------------------------------------------------- capture (D5, D10)


def _find_payment(session: Session, token: str, order_id: str) -> Payment:
    buyer = find_buyer(session, token)
    p = session.scalars(
        select(Payment).where(
            Payment.paypal_order_id == order_id,
            Payment.buyer_id == buyer.id,
            Payment.source == "STATEMENT",
        )
    ).first()
    if p is None:
        raise StatementNotFoundError
    return p


def _settled(p: Payment) -> CaptureResult:
    if p.status in ("CAPTURED", "CAPTURE_PENDING"):
        return CaptureResult(p.id, p.status)
    if p.status == "VOIDED":
        raise PaymentError(409, "STATEMENT_CHANGED", CHANGED)
    if p.status == "FAILED":
        raise PaymentError(
            409, "PAYMENT_FAILED", "This payment didn't go through. You weren't charged."
        )
    raise AssertionError(p.status)


def _fits(session: Session, p: Payment) -> bool:
    planned = [int(x["invoice_id"]) for x in p.planned_split or []]
    _, open_ = open_invoices(session, p.buyer_id)
    try:
        split_payment(p.amount_cents, open_, planned)
    except SplitError:
        return False
    return True


async def _confirm(pp: PayPal, order_id: str) -> dict[str, Any] | None:
    """One GET of the order after the lock is released, hard 5 s cap (R7)."""
    try:
        return await asyncio.wait_for(
            orders.get(pp, order_id, timeout=CONFIRM_GET_S), CONFIRM_GET_S
        )
    except (PayPalError, TimeoutError):
        return None


def _finish_from_order(
    session_factory: Callable[[], Session], payment_id: int, order: dict[str, Any]
) -> CaptureResult:
    """New locked transaction: finish a capture PayPal already made (we missed the answer)."""
    with session_factory() as s, s.begin():
        p = s.get(Payment, payment_id)
        assert p is not None
        lock_buyer(s, p.buyer_id)
        s.refresh(p)
        if p.status in ("CAPTURED", "CAPTURE_PENDING"):
            return CaptureResult(p.id, p.status)
        finish_capture(s, p, orders.first_capture(order) or {})
        return CaptureResult(p.id, p.status)


def _void(session_factory: Callable[[], Session], payment_id: int) -> None:
    with session_factory() as s, s.begin():
        p = s.get(Payment, payment_id)
        assert p is not None
        lock_buyer(s, p.buyer_id)
        s.refresh(p)
        if p.status == "CREATED":
            p.status = "VOIDED"
            audit.log(
                s,
                "system",
                "payment.voided",
                ref=str(p.uid),
                detail={"reason": "statement changed"},
            )


def _declined(session_factory: Callable[[], Session], payment_id: int) -> None:
    """R2: the next capture attempt uses a new Request-Id suffix."""
    with session_factory() as s, s.begin():
        p = s.get(Payment, payment_id)
        assert p is not None
        lock_buyer(s, p.buyer_id)
        p.capture_attempts += 1


async def capture_payment(
    session_factory: Callable[[], Session], pp: PayPal, token: str, order_id: str
) -> CaptureResult:
    # 0. who is this (scoped to the token's buyer — another buyer's order id is a 404)
    def lookup() -> tuple[int, str, str, int, int]:
        with session_factory() as s:
            p = _find_payment(s, token, order_id)
            return p.id, p.status, str(p.uid), p.capture_attempts, p.buyer_id

    payment_id, status, uid, attempts, buyer_id = await run_in_threadpool(lookup)
    if status != "CREATED":
        return await run_in_threadpool(_settled_by_id, session_factory, payment_id)

    await pp.ensure_token()  # R7: never fetch a token while holding the lock

    session = session_factory()
    try:
        # (a) lock + re-check + re-split on current balances
        def lock_and_check() -> str:
            session.begin()
            lock_buyer(session, buyer_id)
            p = session.get(Payment, payment_id)
            assert p is not None
            session.refresh(p)
            if p.status != "CREATED":
                return "SETTLED"
            return "FITS" if _fits(session, p) else "DOES_NOT_FIT"

        state = await run_in_threadpool(lock_and_check)
        if state == "SETTLED":
            await run_in_threadpool(session.rollback)
            return await run_in_threadpool(_settled_by_id, session_factory, payment_id)
        if state == "DOES_NOT_FIT":
            # (b) R1: never void money PayPal may already hold — ask PayPal first
            await run_in_threadpool(session.rollback)
            order = await _confirm(pp, order_id)
            if order and order.get("status") == "COMPLETED":
                return await run_in_threadpool(
                    _finish_from_order, session_factory, payment_id, order
                )
            await run_in_threadpool(_void, session_factory, payment_id)
            raise PaymentError(409, "STATEMENT_CHANGED", CHANGED)

        # (c) capture under the lock, hard 10 s cap
        failure: PayPalError | None = None
        body: dict[str, Any] | None = None
        try:
            body = await asyncio.wait_for(
                orders.capture(pp, order_id, uid, attempts), CAPTURE_CAP_S
            )
        except PayPalError as e:
            failure = e
        except TimeoutError:
            failure = PayPalError("TIMEOUT", "capture took too long", None, [], None, True)

        if body is not None:
            cap = orders.first_capture(body) or {}
            if cap.get("status") in ("COMPLETED", "PENDING"):

                def finish() -> CaptureResult:
                    p = session.get(Payment, payment_id)
                    assert p is not None
                    finish_capture(session, p, cap)
                    session.commit()
                    return CaptureResult(p.id, p.status)

                return await run_in_threadpool(finish)
            # R8: a 201 whose capture is DECLINED / FAILED = a decline
            await run_in_threadpool(session.rollback)
            await run_in_threadpool(_declined, session_factory, payment_id)
            raise PaymentError(402, "PAYMENT_DECLINED", DECLINED_MSG)

        # failure paths: release the lock first (R7)
        await run_in_threadpool(session.rollback)
        assert failure is not None
        kind = orders.classify(failure)
        log.warning(
            "capture failed payment=%s kind=%s debug_id=%s", payment_id, kind, failure.debug_id
        )
        if kind == "DECLINED":
            await run_in_threadpool(_declined, session_factory, payment_id)
            raise PaymentError(402, "PAYMENT_DECLINED", DECLINED_MSG, failure.debug_id)
        if kind == "NOT_APPROVED":
            raise PaymentError(
                409, "ORDER_NOT_APPROVED", "Please approve the payment in PayPal first."
            )
        if kind in ("ALREADY_CAPTURED", "DUPLICATE_INVOICE_ID", "TRANSIENT"):
            order = await _confirm(pp, order_id)
            if order and order.get("status") == "COMPLETED":
                return await run_in_threadpool(
                    _finish_from_order, session_factory, payment_id, order
                )
            if kind == "TRANSIENT":
                raise PaymentError(503, "CAPTURE_UNCONFIRMED", UNCONFIRMED, failure.debug_id)
        raise PaymentError(
            502, "PAYPAL_REJECTED", "PayPal couldn't complete this payment.", failure.debug_id
        )
    finally:
        await run_in_threadpool(_close, session)


def _close(session: Session) -> None:
    try:
        session.rollback()
    finally:
        session.close()


def _settled_by_id(session_factory: Callable[[], Session], payment_id: int) -> CaptureResult:
    with session_factory() as s:
        p = s.get(Payment, payment_id)
        assert p is not None
        return _settled(p)
