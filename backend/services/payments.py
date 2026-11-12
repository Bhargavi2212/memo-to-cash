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
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from backend.core.dates import NY, ny_date, today_ny
from backend.core.money import to_cents
from backend.core.split import OpenInvoice, SplitError, resolve_request, split_payment
from backend.db.models import Allocation, Invoice, Memo, Payment
from backend.paypal import invoices as pinv
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


NOT_READY_MSG = "These invoices are still being set up on PayPal — try again in a moment."


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
        if any(r.ledger_status == "DRAFT" for r in rows):  # P3 §M M9: never pay before mirrored
            raise PaymentError(409, "PAYPAL_NOT_READY", NOT_READY_MSG)
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
    return await capture_core(session_factory, pp, payment_id, order_id, uid, attempts, buyer_id)


async def capture_core(
    session_factory: Callable[[], Session],
    pp: PayPal,
    payment_id: int,
    order_id: str,
    uid: str,
    attempts: int,
    buyer_id: int,
) -> CaptureResult:
    """Lock → re-check → split check → capture (10 s) → finish, one transaction (CLAUDE §6's
    one exception). Shared by the buyer's `/capture` and the server-side capture (webhook
    ORDER.APPROVED / sweep, P3b B3 + X1), so both meet the same re-check: whichever comes
    second finds the payment settled (or PayPal says ORDER_ALREADY_CAPTURED → finish)."""
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


# ---------------------------------------------------------------- recording (D7, R13, D23)

RECORD_CAP_S = 12.0  # overall cap for one payment's PayPal recording calls (D7)
OVERPAY_ISSUE = "PAYMENT_AMOUNT_GREATER_THAN_AMOUNT_DUE"
PAID_ELSEWHERE = "PayPal shows less due than our ledger — paid elsewhere?"


@dataclass
class _Job:
    allocation_id: int
    uid: str
    paypal_invoice_id: str | None
    amount_cents: int
    method: str  # PAYPAL for statement payments; CHECK / WIRE_TRANSFER / OTHER off PayPal
    payment_date: Any  # date the money arrived (None → today)
    note: str  # after the [MTC:<uid>] token


METHOD_WORD = {"CHECK": "Cheque", "WIRE_TRANSFER": "Wire", "OTHER": "Payment"}
RECORDABLE = ("CAPTURED", "RECORDED")  # statement payments + off-PayPal payments (P3 A9, R3)


def _job_for(p: Payment, a: Allocation, paypal_invoice_id: str | None) -> _Job:
    """The recording job is built from the payment row (P3 R3): a statement payment records
    as PAYPAL today; an off-PayPal one with its own method, date and reference."""
    if p.source == "OFF_PAYPAL":
        method = p.method or "OTHER"
        word = METHOD_WORD.get(method, "Payment")
        note = f"{word} {p.reference}".strip() if p.reference else word
        when = ny_date(p.received_at) if p.received_at else None
    else:
        method, when = "PAYPAL", None
        note = f"Statement payment · PayPal capture {p.paypal_capture_id}"
    return _Job(a.id, str(a.uid), paypal_invoice_id, a.amount_cents, method, when, note)


@dataclass
class _Result:
    transaction_id: str | None = None
    invoice: dict[str, Any] | None = None
    error: PayPalError | None = None
    done: bool = False  # PayPal answered (success or a deterministic error)


def _mark_recording(
    session_factory: Callable[[], Session], payment_id: int, states: tuple[str, ...]
) -> list[_Job]:
    """One transaction: every allocation to (re)record → RECORDING, committed BEFORE any
    PayPal call (invariant 10, R13)."""
    with session_factory() as s, s.begin():
        p = s.get(Payment, payment_id)
        if p is None or p.status not in RECORDABLE:
            return []
        lock_buyer(s, p.buyer_id)
        allocs = s.scalars(
            select(Allocation)
            .where(Allocation.payment_id == payment_id, Allocation.record_state.in_(states))
            .order_by(Allocation.id)
        ).all()
        invoice_of = {
            i.id: i.paypal_invoice_id
            for i in s.scalars(
                select(Invoice).where(Invoice.id.in_({a.invoice_id for a in allocs}))
            )
        }
        now = datetime.now(UTC)
        jobs = []
        for a in allocs:
            a.record_state = "RECORDING"
            a.record_started_at = now
            a.attempts += 1
            jobs.append(_job_for(p, a, invoice_of.get(a.invoice_id)))
        return jobs


async def _record_one(pp: PayPal, job: _Job, today: Any, resolve: bool) -> _Result:
    out = _Result()
    if not job.paypal_invoice_id:
        out.error = PayPalError("NOT_ON_PAYPAL", "invoice has no PayPal id", None, [], None)
        out.done = True
        return out
    token = f"[MTC:{job.uid}]"
    try:
        if resolve:  # TRD §6b-3: before any retry, look for our own note first
            inv = await pinv.get(pp, job.paypal_invoice_id)
            for txn in (inv.get("payments") or {}).get("transactions") or []:
                if token in (txn.get("note") or ""):
                    out.transaction_id, out.invoice, out.done = txn.get("payment_id"), inv, True
                    return out
        rec = await pinv.record_payment(
            pp,
            job.paypal_invoice_id,
            allocation_uid=job.uid,
            amount_cents=job.amount_cents,
            method=job.method,
            payment_date=job.payment_date or today,
            note=f"{token} {job.note}",
        )
        out.transaction_id, out.done = rec.get("payment_id"), True
        try:
            out.invoice = await pinv.get(pp, job.paypal_invoice_id)
        except PayPalError:
            pass  # recorded; the sync of PayPal's due waits for the next GET
    except PayPalError as e:
        out.error = e
        out.done = not e.transient
    return out


def _write_results(
    session_factory: Callable[[], Session],
    payment_id: int,
    jobs: list[_Job],
    results: list[_Result | None],
) -> None:
    with session_factory() as s, s.begin():
        p = s.get(Payment, payment_id)
        assert p is not None
        lock_buyer(s, p.buyer_id)
        now = datetime.now(UTC)
        for job, res in zip(jobs, results, strict=True):
            a = s.get(Allocation, job.allocation_id)
            assert a is not None
            if (
                res is None or not res.done
            ):  # timeout / transient: stays RECORDING (sweep, /refresh)
                if res and res.error:
                    a.paypal_record_error = f"{res.error.name}: {res.error.message}"[:500]
                continue
            inv = s.get(Invoice, a.invoice_id)
            assert inv is not None
            if res.error is None:
                a.record_state = "RECORDED"
                a.paypal_recorded_at = now
                a.paypal_transaction_id = res.transaction_id
                a.paypal_record_error = None
                audit.log(
                    s,
                    "system",
                    "allocation.recorded",
                    ref=str(a.uid),
                    detail={"payment_id": payment_id, "invoice_id": inv.id},
                )
                if res.invoice:
                    inv.paypal_status = res.invoice.get("status") or inv.paypal_status
                    due = (res.invoice.get("due_amount") or {}).get("value")
                    if due is not None:
                        inv.paypal_due_cents = to_cents(due)
                        ours = inv.total_cents - inv.paid_cents
                        inv.mismatch = (
                            None
                            if inv.paypal_due_cents == ours
                            else f"PayPal due {inv.paypal_due_cents} ≠ ours {ours} (cents)"
                        )
                    inv.paypal_synced_at = now
            else:
                e = res.error
                a.record_state = "FAILED"
                a.paypal_record_error = f"{e.name}: {e.message}"[:500] + (
                    f" (debug_id {e.debug_id})" if e.debug_id else ""
                )
                if OVERPAY_ISSUE in orders.issues(e):  # D23: paid on PayPal's page meanwhile
                    a.paypal_record_error = f"{OVERPAY_ISSUE}: {PAID_ELSEWHERE}"
                    inv.mismatch = PAID_ELSEWHERE
                log.warning("record failed allocation=%s %s debug_id=%s", a.id, e.name, e.debug_id)


async def _run(pp: PayPal, jobs: list[_Job], resolve: bool) -> list[_Result | None]:
    from backend.core.dates import today_ny

    day = today_ny()
    tasks = [asyncio.create_task(_record_one(pp, j, day, resolve)) for j in jobs]
    done, pending = await asyncio.wait(tasks, timeout=RECORD_CAP_S)
    for t in pending:
        t.cancel()
    return [t.result() if t in done and not t.cancelled() else None for t in tasks]


async def record_allocations(
    session_factory: Callable[[], Session], pp: PayPal, payment_id: int
) -> None:
    """After the capture commit (BackgroundTask, R14): PENDING allocations → RECORDING (one
    txn) → record on PayPal concurrently (12 s overall) → one txn writes the results.
    Never changes paid_cents: the allocation already counted at capture (D8)."""
    jobs = await run_in_threadpool(_mark_recording, session_factory, payment_id, ("PENDING",))
    if not jobs:
        return
    results = await _run(pp, jobs, resolve=False)
    await run_in_threadpool(_write_results, session_factory, payment_id, jobs, results)


async def resolve_recording(
    session_factory: Callable[[], Session], pp: PayPal, payment_id: int
) -> None:
    """P2 retry path (`/refresh`, R4) and the P3 sweep: for RECORDING / FAILED allocations,
    GET the invoice and look for `[MTC:<uid>]` → RECORDED; not found → record again with the
    SAME Request-Id. At most one call per allocation, 12 s cap. D23 overpays are skipped."""

    def mark() -> list[_Job]:
        with session_factory() as s, s.begin():
            p = s.get(Payment, payment_id)
            if p is None or p.status not in RECORDABLE:
                return []
            lock_buyer(s, p.buyer_id)
            allocs = s.scalars(
                select(Allocation)
                .where(
                    Allocation.payment_id == payment_id,
                    Allocation.record_state.in_(("RECORDING", "FAILED")),
                )
                .order_by(Allocation.id)
            ).all()
            allocs = [
                a for a in allocs if not (a.paypal_record_error or "").startswith(OVERPAY_ISSUE)
            ]
            invoice_of = {
                i.id: i.paypal_invoice_id
                for i in s.scalars(
                    select(Invoice).where(Invoice.id.in_({a.invoice_id for a in allocs}))
                )
            }
            now = datetime.now(UTC)
            out = []
            for a in allocs:
                a.record_state = "RECORDING"
                a.record_started_at = now
                a.attempts += 1
                out.append(_job_for(p, a, invoice_of.get(a.invoice_id)))
            return out

    jobs = await run_in_threadpool(mark)
    if not jobs:
        return
    results = await _run(pp, jobs, resolve=True)
    await run_in_threadpool(_write_results, session_factory, payment_id, jobs, results)


# ---------------------------------------------------------------- off-PayPal (P3 A9, FR-14)

OFF_PAYPAL_METHODS = ("CHECK", "WIRE_TRANSFER", "OTHER")


class RecordError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def record_off_paypal(
    session_factory: Callable[[], Session],
    *,
    buyer_id: int,
    client_uid: uuid.UUID,
    method: str,
    reference: str,
    amount_cents: int | None,
    invoice_ids: list[int] | None,
    received_on: date | None,
) -> tuple[int, bool]:
    """A cheque / wire / Zelle the supplier received: the same split rule as the statement
    (`resolve_request` + `split_payment`) under the buyer lock → payment OFF_PAYPAL / RECORDED +
    PENDING allocations, `paid_cents` raised, one transaction. The caller then runs
    `record_allocations` (method + date + `[MTC:<uid>] Cheque <ref>` on each PayPal invoice).

    Idempotent (R3, G8): `client_uid` becomes `payments.uid`; the same uid again for the same
    buyer returns that payment (`repeat=True`); for another buyer → 409. Returns (id, repeat)."""
    if method not in OFF_PAYPAL_METHODS:
        raise RecordError(422, "BAD_METHOD", "method must be CHECK, WIRE_TRANSFER or OTHER")
    today = today_ny()
    when = received_on or today
    if when > today:
        raise RecordError(422, "DATE_IN_FUTURE", "The payment date can't be in the future")
    with session_factory() as s, s.begin():
        lock_buyer(s, buyer_id)
        existing = s.scalars(select(Payment).where(Payment.uid == client_uid)).first()
        if existing is not None:
            if existing.buyer_id != buyer_id:
                raise RecordError(409, "CLIENT_UID_REUSED", "That request id belongs elsewhere")
            return existing.id, True
        rows, open_ = open_invoices(s, buyer_id)
        if any(r.ledger_status == "DRAFT" for r in rows):  # P3 §M M9
            raise RecordError(409, "PAYPAL_NOT_READY", NOT_READY_MSG)
        amount, selected = resolve_request(open_, invoice_ids, amount_cents)
        split = split_payment(amount, open_, selected)
        p = Payment(
            uid=client_uid,
            buyer_id=buyer_id,
            source="OFF_PAYPAL",
            status="RECORDED",
            method=method,
            reference=(reference or "").strip()[:100] or None,
            amount_cents=amount,
            # noon New York on that day, so ny_date() gives the same day back (G9)
            received_at=datetime.combine(when, time(12), tzinfo=NY),
        )
        s.add(p)
        s.flush()
        by_id = {i.id: i for i in rows}
        for inv_id, cents in split:
            s.add(Allocation(payment_id=p.id, invoice_id=inv_id, amount_cents=cents))
            inv = by_id[inv_id]
            inv.paid_cents += cents
            inv.ledger_status = _ledger_status(inv)
        audit.log(s, "supplier", "payment.recorded", ref=str(p.uid),
                  detail={"payment_id": p.id, "method": method, "amount_cents": amount,
                          "split": [[i, c] for i, c in split]})  # fmt: skip
        return p.id, False
