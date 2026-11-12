"""PayPal sync (FR-7; P3b B1–B4, X1, X6, F4, F8; TRD §6b–6b-5): webhook events and the sweep.

- `store_event` — one row per PayPal event id (redelivery → nothing new).
- `process_event` — by type:
  · INVOICING.INVOICE.PAID → `reconcile_invoice` (J6 backfill + P2 D23 reconcile).
  · INVOICING.INVOICE.CANCELLED / REFUNDED, PAYMENT.CAPTURE.REFUNDED → shown, not automated
    (TRD §6c, plan G5): raw status + `mismatch` + audit; no ledger change (invariant 3).
  · PAYMENT.CAPTURE.COMPLETED → finish a capture we never heard back about (TRD §6b-2).
  · CHECKOUT.ORDER.APPROVED → server-side capture, only ≥ 10 min after the payment was created
    (X1: the buyer's own `/capture` normally lands first); earlier → no-op, the sweep retries.
  · anything we don't know (dev and prod share one sandbox app) → `ignored: unknown` (G16).
Every money write takes the buyer lock and re-checks inside it (invariant 9), so the webhook,
the sweep and the buyer's own requests can run at the same time without double counting (G10).
"""

import asyncio
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from backend.agent.tools import usd
from backend.core.config import Settings
from backend.core.money import to_cents
from backend.db.models import Allocation, Invoice, Payment, Proposal, WebhookEvent
from backend.paypal import invoices as pinv
from backend.paypal.client import PayPal, PayPalError
from backend.services import audit, payments

log = logging.getLogger("sync")

SERVER_CAPTURE_AFTER = timedelta(minutes=10)  # X1
IGNORED_UNKNOWN = "ignored: unknown"
WAITING_RECORDING = "waiting: an allocation on this invoice is RECORDING"


def store_event(session_factory: Callable[[], Session], event: dict[str, Any]) -> int | None:
    """Insert the verified event (raw payload). Returns its row id, or None if PayPal already
    delivered it (unique `paypal_event_id`, invariant 4)."""
    with session_factory() as s, s.begin():
        row_id = s.execute(
            pg_insert(WebhookEvent)
            .values(paypal_event_id=event["id"], event_type=event["event_type"], payload=event)
            .on_conflict_do_nothing(index_elements=[WebhookEvent.paypal_event_id])
            .returning(WebhookEvent.id)
        ).scalar_one_or_none()
    # event type and id only — payloads carry payer emails (CLAUDE §4: never log them at INFO)
    log.info(
        "webhook.received id=%s type=%s new=%s verified=true",
        event["id"],
        event["event_type"],
        row_id is not None,
    )
    return row_id


# ---------------------------------------------------------------- J6 backfill + D23


@dataclass
class Reconciled:
    state: str  # "recording" | "done" | "unchanged" | "unknown"
    backfilled: list[str] = field(default_factory=list)  # PayPal transaction ids taken in
    requeue: list[int] = field(default_factory=list)  # payments with re-PENDING allocations
    invoice_no: str | None = None


def _txn_cents(t: dict[str, Any]) -> int:
    money = t.get("amount") or {}
    return (
        to_cents(str(money.get("value", "0"))) if money.get("currency_code", "USD") == "USD" else 0
    )


def _shrink_overpays(
    s: Session, inv: Invoice, allocs: list[Allocation], overlap: int
) -> tuple[list[tuple[Payment, int]], list[int]]:
    """P2 D23 (R4): our allocations on this invoice that PayPal refused as an overpay were paid
    a second time on PayPal's page. Take the overlap back out of them — newest first — into
    their payment's credit (`unapplied_cents`), lowering `paid_cents` by the same amount. A
    fully overlapped allocation is deleted (`amount_cents > 0`); a partly overlapped one keeps
    the rest, gets a fresh uid (new Request-Id + `[MTC:]` token — the old one never reached
    PayPal) and goes back to PENDING so it's recorded at its new amount."""
    credited: list[tuple[Payment, int]] = []
    requeue: list[int] = []
    overpays = sorted(
        (
            a
            for a in allocs
            if a.record_state == "FAILED"
            and (a.paypal_record_error or "").startswith(payments.OVERPAY_ISSUE)
        ),
        key=lambda a: a.id,
        reverse=True,
    )
    for a in overpays:
        if overlap <= 0:
            break
        take = min(a.amount_cents, overlap)
        p = s.get(Payment, a.payment_id)
        assert p is not None
        p.unapplied_cents += take
        inv.paid_cents -= take
        overlap -= take
        credited.append((p, take))
        if take == a.amount_cents:
            allocs.remove(a)
            s.delete(a)
        else:
            a.amount_cents -= take
            a.uid = uuid.uuid4()
            a.record_state = "PENDING"
            a.paypal_record_error = None
            requeue.append(p.id)
    return credited, requeue


def _write_reconcile(
    session_factory: Callable[[], Session], invoice_id: int, pp_inv: dict[str, Any], via: str
) -> Reconciled:
    now = datetime.now(UTC)
    with session_factory() as s, s.begin():
        inv = s.get(Invoice, invoice_id)
        assert inv is not None
        payments.lock_buyer(s, inv.buyer_id)
        s.refresh(inv)
        out = Reconciled("unchanged", invoice_no=inv.erp_invoice_no)
        allocs = list(s.scalars(select(Allocation).where(Allocation.invoice_id == inv.id)))
        if any(a.record_state == "RECORDING" for a in allocs):
            return Reconciled("recording", invoice_no=inv.erp_invoice_no)
        ours = {a.paypal_transaction_id for a in allocs if a.paypal_transaction_id}
        ours |= set(
            s.scalars(
                select(Payment.reference).where(
                    Payment.buyer_id == inv.buyer_id, Payment.source == "PAYPAL_INVOICE_PAGE"
                )
            )
        )
        tokens = [f"[MTC:{a.uid}]" for a in allocs]
        txns = ((pp_inv.get("payments") or {}).get("transactions")) or []
        for t in txns:
            tid, note = t.get("payment_id"), t.get("note") or ""
            cents = _txn_cents(t)
            if not tid or tid in ours or any(tok in note for tok in tokens) or cents <= 0:
                continue  # ours (TRD §6b rule 2) or nothing to take in
            due = inv.total_cents - inv.paid_cents
            credited, requeue = _shrink_overpays(s, inv, allocs, max(0, cents - due))
            out.requeue += requeue
            fits = min(cents, inv.total_cents - inv.paid_cents)
            p = Payment(
                buyer_id=inv.buyer_id,
                source="PAYPAL_INVOICE_PAGE",
                status="CAPTURED",
                amount_cents=cents,
                method=t.get("method") or "PAYPAL",
                reference=tid,
                received_at=now,
                unapplied_cents=cents - fits,
            )
            s.add(p)
            s.flush()
            if fits > 0:
                s.add(
                    Allocation(
                        payment_id=p.id,
                        invoice_id=inv.id,
                        amount_cents=fits,
                        record_state="RECORDED",
                        paypal_transaction_id=tid,
                        paypal_recorded_at=now,
                    )
                )
                inv.paid_cents += fits
            inv.ledger_status = payments._ledger_status(inv)
            ours.add(tid)
            out.backfilled.append(tid)
            audit.log(
                s,
                "paypal",
                "sync.backfill",
                ref=str(p.uid),
                detail={
                    "via": via,
                    "payment_id": p.id,
                    "invoice_id": inv.id,
                    "amount_cents": cents,
                    "credited": [{"payment_id": cp.id, "cents": c} for cp, c in credited],
                },
            )
            for cp, c in credited:  # X6: the supplier decides what to do with the extra
                s.add(
                    Proposal(
                        buyer_id=inv.buyer_id,
                        kind="REPLY",
                        payload={
                            "reason": f"{usd(c)} paid twice on {inv.erp_invoice_no} — "
                            "held as credit; refund it from PayPal if they want it back."
                        },
                        evidence={
                            "source": "system",
                            "internal": True,
                            "checks": [f"{inv.erp_invoice_no} paid on PayPal's page too"],
                            "records": [f"invoice:{inv.erp_invoice_no}", f"payment:{cp.id}"],
                        },
                    )
                )
        # PayPal's view, as on every GET (ledger rule: a difference is shown, never overwritten)
        inv.paypal_status = pp_inv.get("status") or inv.paypal_status
        due_value = (pp_inv.get("due_amount") or {}).get("value")
        if due_value is not None:
            inv.paypal_due_cents = to_cents(str(due_value))
            # not on PayPal yet (a D23 remainder being re-recorded): PayPal still shows it as due
            pending = sum(a.amount_cents for a in allocs if a.record_state == "PENDING")
            ours_due = inv.total_cents - inv.paid_cents
            inv.mismatch = (
                None
                if inv.paypal_due_cents in (ours_due, ours_due + pending)
                else f"PayPal due {inv.paypal_due_cents} ≠ ours {ours_due} (cents)"
            )
        inv.paypal_synced_at = now
        if out.backfilled:
            out.state = "done"
        s.flush()
        # ledger rule (invariant 3): paid_cents = Σ allocations, after every write
        total = select(func.coalesce(func.sum(Allocation.amount_cents), 0)).where(
            Allocation.invoice_id == inv.id
        )
        assert inv.paid_cents == s.scalar(total), "paid_cents must equal Σ allocations"
        return out


async def reconcile_invoice(
    session_factory: Callable[[], Session], pp: PayPal, invoice_id: int, *, via: str
) -> Reconciled:
    """TRD §6b: skip while one of our allocations on it is RECORDING (the sweep retries) →
    GET the invoice → take in transactions that are neither our transaction ids nor carry our
    `[MTC:<uid>]` tokens → D23 shrink → backfill payment + allocation, all under the lock."""

    def look() -> tuple[str | None, bool]:
        with session_factory() as s:
            inv = s.get(Invoice, invoice_id)
            busy = s.scalar(
                select(func.count()).where(
                    Allocation.invoice_id == invoice_id, Allocation.record_state == "RECORDING"
                )
            )
            return (inv.paypal_invoice_id if inv else None), bool(busy)

    pp_id, busy = await run_in_threadpool(look)
    if pp_id is None:
        return Reconciled("unknown")
    if busy:
        return Reconciled("recording")
    pp_inv = await pinv.get(pp, pp_id)
    return await _reconcile_with(session_factory, pp, invoice_id, pp_inv, via=via)


async def _reconcile_with(
    session_factory: Callable[[], Session],
    pp: PayPal,
    invoice_id: int,
    pp_inv: dict[str, Any],
    *,
    via: str,
) -> Reconciled:
    out = await run_in_threadpool(_write_reconcile, session_factory, invoice_id, pp_inv, via)
    for payment_id in sorted(set(out.requeue)):
        await payments.record_allocations(session_factory, pp, payment_id)
    if out.backfilled:
        log.info(
            "sync.backfill invoice=%s via=%s txns=%d", out.invoice_no, via, len(out.backfilled)
        )
    return out


# ---------------------------------------------------------------- captures (TRD §6b-2, X1)


def _payment_for(s: Session, resource: dict[str, Any]) -> Payment | None:
    """A statement payment from an order or capture resource: `custom_id` = payment uid, or the
    order id (the order's own id, or the capture's related order id)."""
    units = resource.get("purchase_units") or [{}]
    custom = resource.get("custom_id") or units[0].get("custom_id")
    if custom:
        try:
            p = s.scalars(select(Payment).where(Payment.uid == uuid.UUID(custom))).first()
        except ValueError:
            p = None
        if p is not None:
            return p
    related = (resource.get("supplementary_data") or {}).get("related_ids") or {}
    order_id = related.get("order_id") or (resource.get("id") if units != [{}] else None)
    if order_id:
        return s.scalars(select(Payment).where(Payment.paypal_order_id == order_id)).first()
    return None


def _finish_from_capture(
    session_factory: Callable[[], Session], payment_id: int, capture: dict[str, Any]
) -> str:
    with session_factory() as s, s.begin():
        p = s.get(Payment, payment_id)
        assert p is not None
        payments.lock_buyer(s, p.buyer_id)
        s.refresh(p)
        if p.status not in ("CREATED", "CAPTURE_PENDING"):
            return p.status  # the buyer's /capture (or an earlier event) finished it
        payments.finish_capture(s, p, capture)
        return p.status


async def server_capture(
    session_factory: Callable[[], Session], pp: PayPal, payment_id: int, now: datetime
) -> str:
    """Approved but never captured (TRD §6b-2): capture it for the buyer — only a CREATED
    payment created ≥ 10 min ago (X1), through the same core as `/capture`."""

    def load() -> tuple[str, str | None, str, int, int, datetime]:
        with session_factory() as s:
            p = s.get(Payment, payment_id)
            assert p is not None
            return (
                p.status,
                p.paypal_order_id,
                str(p.uid),
                p.capture_attempts,
                p.buyer_id,
                p.created_at,
            )

    status, order_id, uid, attempts, buyer_id, created = await run_in_threadpool(load)
    if status != "CREATED" or not order_id:
        return "settled"
    if now - created < SERVER_CAPTURE_AFTER:
        return "too_early"
    try:
        r = await payments.capture_core(
            session_factory, pp, payment_id, order_id, uid, attempts, buyer_id
        )
    except payments.PaymentError as e:
        log.info("server capture payment=%s: %s", payment_id, e.code)
        return e.code
    if r.status == "CAPTURED":
        await payments.record_allocations(session_factory, pp, payment_id)
    return r.status


# ---------------------------------------------------------------- shown, not automated (G5)


def _flag_invoice(
    session_factory: Callable[[], Session], invoice_id: int, word: str, status: str | None
) -> None:
    with session_factory() as s, s.begin():
        inv = s.get(Invoice, invoice_id)
        assert inv is not None
        payments.lock_buyer(s, inv.buyer_id)
        inv.paypal_status = status or inv.paypal_status
        inv.mismatch = f"PayPal shows this invoice {word}"
        audit.log(s, "paypal", f"invoice.{word}", ref=str(inv.uid), detail={"invoice_id": inv.id})


def _flag_payment_refund(session_factory: Callable[[], Session], payment_id: int) -> None:
    with session_factory() as s, s.begin():
        p = s.get(Payment, payment_id)
        assert p is not None
        payments.lock_buyer(s, p.buyer_id)
        for inv in s.scalars(select(Invoice).join(Allocation).where(Allocation.payment_id == p.id)):
            inv.mismatch = "PayPal shows a refund on a payment for this invoice"
        audit.log(s, "paypal", "payment.refunded", ref=str(p.uid), detail={"payment_id": p.id})


# ---------------------------------------------------------------- events


def _load_event(session_factory: Callable[[], Session], event_id: int) -> WebhookEvent | None:
    with session_factory() as s:
        ev = s.get(WebhookEvent, event_id)
        if ev is not None:
            s.expunge(ev)
        return ev


def _mark(
    session_factory: Callable[[], Session],
    event_id: int,
    *,
    processed: bool,
    error: str | None = None,
) -> None:
    with session_factory() as s, s.begin():
        ev = s.get(WebhookEvent, event_id)
        assert ev is not None
        ev.error = error
        ev.processed_at = datetime.now(UTC) if processed else None


def _invoice_by_paypal_id(session_factory: Callable[[], Session], pp_id: str | None) -> int | None:
    if not pp_id:
        return None
    with session_factory() as s:
        return s.scalar(select(Invoice.id).where(Invoice.paypal_invoice_id == pp_id))


def _payment_id_for(session_factory: Callable[[], Session], resource: dict[str, Any]) -> int | None:
    with session_factory() as s:
        p = _payment_for(s, resource)
        return p.id if p is not None else None


async def process_event(
    session_factory: Callable[[], Session],
    pp: PayPal,
    settings: Settings,
    event_id: int,
    *,
    now: datetime | None = None,
) -> str:
    """Process one stored event. Returns what happened (for logs and tests). Idempotent: a
    processed event is skipped; every write re-checks under the buyer lock."""
    ev = await run_in_threadpool(_load_event, session_factory, event_id)
    if ev is None or ev.processed_at is not None:
        return "already"
    when = now or datetime.now(UTC)
    t, res = ev.event_type, (ev.payload.get("resource") or {})
    try:
        if t.startswith("INVOICING.INVOICE."):
            inv_res = res.get("invoice") or res
            invoice_id = await run_in_threadpool(
                _invoice_by_paypal_id, session_factory, inv_res.get("id")
            )
            if invoice_id is None:
                result = IGNORED_UNKNOWN
            elif t == "INVOICING.INVOICE.PAID":
                out = await reconcile_invoice(session_factory, pp, invoice_id, via="webhook")
                if out.state == "recording":
                    await run_in_threadpool(
                        _mark, session_factory, event_id, processed=False, error=WAITING_RECORDING
                    )
                    return "recording"
                result = out.state
            elif t in ("INVOICING.INVOICE.CANCELLED", "INVOICING.INVOICE.REFUNDED"):
                word = "cancelled" if t.endswith("CANCELLED") else "refunded"
                await run_in_threadpool(
                    _flag_invoice, session_factory, invoice_id, word, inv_res.get("status")
                )
                result = word
            else:
                result = IGNORED_UNKNOWN
        elif t in (
            "PAYMENT.CAPTURE.COMPLETED",
            "CHECKOUT.ORDER.APPROVED",
            "PAYMENT.CAPTURE.REFUNDED",
        ):
            payment_id = await run_in_threadpool(_payment_id_for, session_factory, res)
            if payment_id is None:
                result = IGNORED_UNKNOWN
            elif t == "PAYMENT.CAPTURE.COMPLETED":
                result = await run_in_threadpool(
                    _finish_from_capture, session_factory, payment_id, res
                )
                if result == "CAPTURED":
                    await payments.record_allocations(session_factory, pp, payment_id)
            elif t == "CHECKOUT.ORDER.APPROVED":
                result = await server_capture(session_factory, pp, payment_id, when)
            else:
                await run_in_threadpool(_flag_payment_refund, session_factory, payment_id)
                result = "refunded"
        else:
            result = IGNORED_UNKNOWN
    except PayPalError as e:  # left for the sweep
        log.warning("event %s %s failed: %s debug_id=%s", ev.paypal_event_id, t, e.name, e.debug_id)
        await run_in_threadpool(
            _mark, session_factory, event_id, processed=False, error=f"{e.name}: {e.message}"[:500]
        )
        return "error"
    except Exception as e:  # never lose the event; the sweep retries
        log.exception("event %s %s failed", ev.paypal_event_id, t)
        await run_in_threadpool(
            _mark, session_factory, event_id, processed=False, error=f"{type(e).__name__}"[:500]
        )
        return "error"
    await run_in_threadpool(
        _mark,
        session_factory,
        event_id,
        processed=True,
        error=IGNORED_UNKNOWN if result == IGNORED_UNKNOWN else None,
    )
    log.info("event processed id=%s type=%s result=%s", ev.paypal_event_id, t, result)
    return result


# ---------------------------------------------------------------- pay-time check (F4)

PAY_CHECK_S = 3.0  # total budget for the GETs before an order; on timeout carry on as before


def _split_invoices(
    session_factory: Callable[[], Session],
    token: str,
    invoice_ids: list[int] | None,
    amount_cents: int | None,
) -> list[tuple[int, str]]:
    """(our invoice id, PayPal invoice id) of the invoices this payment would touch."""
    from backend.core.split import resolve_request, split_payment
    from backend.services.statement import find_buyer, open_invoices

    with session_factory() as s:
        buyer = find_buyer(s, token)
        rows, open_ = open_invoices(s, buyer.id)
        amount, selected = resolve_request(open_, invoice_ids, amount_cents)
        touched = {i for i, _ in split_payment(amount, open_, selected)}
        return [
            (r.id, r.paypal_invoice_id) for r in rows if r.id in touched and r.paypal_invoice_id
        ]


async def pay_check(
    session_factory: Callable[[], Session],
    pp: PayPal,
    token: str,
    invoice_ids: list[int] | None,
    amount_cents: int | None,
) -> list[str]:
    """Before Pay creates an order (F4): ask PayPal about the invoices it would pay. Any paid on
    PayPal's own page meanwhile is backfilled now (via = pay_check) and its number returned —
    the order is then refused with 409 PAID_ELSEWHERE so it isn't charged twice. PayPal slow
    (> 3 s in total) or failing → [] and Pay goes on as before (D23 reconcile still covers it).
    Split errors are left to `create_order`, which reports them as it always has."""
    from backend.core.split import SplitError

    try:
        targets = await run_in_threadpool(
            _split_invoices, session_factory, token, invoice_ids, amount_cents
        )
    except SplitError:
        return []
    if not targets:
        return []

    async def one(invoice_id: int, pp_id: str) -> str | None:
        pp_inv = await pinv.get(pp, pp_id)
        out = await _reconcile_with(session_factory, pp, invoice_id, pp_inv, via="pay_check")
        return out.invoice_no if out.backfilled else None

    try:
        found = await asyncio.wait_for(
            asyncio.gather(*(one(i, p) for i, p in targets)), timeout=PAY_CHECK_S
        )
    except (TimeoutError, PayPalError) as e:
        log.info("pay check skipped: %s", type(e).__name__)
        return []
    return [n for n in found if n]


# ---------------------------------------------------------------- the sweep (B4, G6, F8, G9)

ORDER_STALE = timedelta(minutes=2)
ORDER_EXPIRED = timedelta(hours=3)
RECORDING_STUCK = timedelta(minutes=5)
MAX_RECORD_ATTEMPTS = 5
MAX_MIRROR_ATTEMPTS = 5
EVENT_BATCH = 50


@dataclass
class SweepWork:
    events: list[int] = field(default_factory=list)
    created: list[int] = field(default_factory=list)  # payment ids, CREATED > 2 min
    capture_pending: list[int] = field(default_factory=list)
    recording: list[int] = field(default_factory=list)  # payment ids to resolve_recording
    mirror: list[int] = field(default_factory=list)  # buyer ids with DRAFT invoices

    def total(self) -> int:
        parts = (self.events, self.created, self.capture_pending, self.recording, self.mirror)
        return sum(len(p) for p in parts)


def _find_work(session_factory: Callable[[], Session], now: datetime) -> SweepWork:
    from backend.db.models import Buyer

    w = SweepWork()
    with session_factory() as s:
        w.events = list(
            s.scalars(
                select(WebhookEvent.id)
                .where(WebhookEvent.processed_at.is_(None))
                .order_by(WebhookEvent.id)
                .limit(EVENT_BATCH)
            )
        )
        w.created = list(
            s.scalars(
                select(Payment.id).where(
                    Payment.status == "CREATED",
                    Payment.source == "STATEMENT",
                    Payment.paypal_order_id.is_not(None),
                    Payment.created_at < now - ORDER_STALE,
                )
            )
        )
        w.capture_pending = list(
            s.scalars(
                select(Payment.id).where(
                    Payment.status == "CAPTURE_PENDING", Payment.paypal_order_id.is_not(None)
                )
            )
        )
        stuck = (Allocation.record_state == "RECORDING") & (
            Allocation.record_started_at < now - RECORDING_STUCK
        )
        # B4's "FAILED transient ones" is empty by construction: a transient record error leaves
        # the allocation RECORDING (payments._write_results); FAILED means PayPal said no for
        # good (or D23, handled by reconcile) — retrying would only repeat it.
        w.recording = sorted(
            set(
                s.scalars(
                    select(Allocation.payment_id).where(
                        stuck & (Allocation.attempts < MAX_RECORD_ATTEMPTS)
                    )
                )
            )
        )
        ready = Buyer.is_demo.is_(False) | Buyer.demo_ready.is_(True)  # G9
        w.mirror = sorted(
            set(
                s.scalars(
                    select(Invoice.buyer_id)
                    .join(Buyer, Invoice.buyer_id == Buyer.id)
                    .where(
                        Invoice.ledger_status == "DRAFT",
                        Invoice.mirror_attempts < MAX_MIRROR_ATTEMPTS,
                        ready,
                    )
                )
            )
        )
    return w


async def _finish_and_record(
    session_factory: Callable[[], Session], pp: PayPal, payment_id: int, cap: dict[str, Any]
) -> None:
    status = await run_in_threadpool(_finish_from_capture, session_factory, payment_id, cap)
    if status == "CAPTURED":
        await payments.record_allocations(session_factory, pp, payment_id)


async def _sweep_created(
    session_factory: Callable[[], Session], pp: PayPal, payment_id: int, now: datetime
) -> None:
    """TRD §6b-2: GET the order — COMPLETED → finish; APPROVED → server capture (X1: ≥ 10 min);
    older than 3 h and still not captured, or unknown to PayPal → VOIDED."""
    from backend.paypal import orders

    def load() -> tuple[str | None, datetime]:
        with session_factory() as s:
            p = s.get(Payment, payment_id)
            assert p is not None
            return p.paypal_order_id, p.created_at

    order_id, created = await run_in_threadpool(load)
    assert order_id
    order: dict[str, Any] | None
    try:
        order = await orders.get(pp, order_id)
    except PayPalError as e:
        if e.transient:
            return
        order = None  # not found / expired
    status = (order or {}).get("status")
    if status == "COMPLETED" and order is not None:
        await _finish_and_record(session_factory, pp, payment_id, orders.first_capture(order) or {})
    elif status == "APPROVED":
        await server_capture(session_factory, pp, payment_id, now)
    elif now - created > ORDER_EXPIRED:
        await run_in_threadpool(payments._void, session_factory, payment_id)


async def _sweep_capture_pending(
    session_factory: Callable[[], Session], pp: PayPal, payment_id: int
) -> None:
    """G6: PayPal held the capture (PENDING) and the COMPLETED webhook never came."""
    from backend.paypal import orders

    def load() -> str | None:
        with session_factory() as s:
            p = s.get(Payment, payment_id)
            return p.paypal_order_id if p else None

    order_id = await run_in_threadpool(load)
    if not order_id:
        return
    try:
        order = await orders.get(pp, order_id)
    except PayPalError:
        return
    cap = orders.first_capture(order) or {}
    if cap.get("status") == "COMPLETED":
        await _finish_and_record(session_factory, pp, payment_id, cap)


async def sweep(
    session_factory: Callable[[], Session],
    pp: PayPal,
    settings: Settings,
    *,
    now: datetime | None = None,
) -> int:
    """All pending PayPal work, one function (TRD §6b-4; B4 + G6 + F8): unprocessed webhook
    events · CREATED payments > 2 min · CAPTURE_PENDING payments · RECORDING allocations stuck
    > 5 min · DRAFT invoices of real or ready demo buyers (phase M setup
    path, ≤ 5 attempts). Each item in its own transaction(s); an error is logged and left for
    the next run. Returns how many items it looked at (the worker's "did work" signal)."""
    from backend.services import mirror

    when = now or datetime.now(UTC)
    w = await run_in_threadpool(_find_work, session_factory, when)
    for i in w.events:
        await _safely("event", i, process_event(session_factory, pp, settings, i, now=when))
    for i in w.created:
        await _safely("created", i, _sweep_created(session_factory, pp, i, when))
    for i in w.capture_pending:
        await _safely("capture_pending", i, _sweep_capture_pending(session_factory, pp, i))
    for i in w.recording:
        await _safely("recording", i, payments.resolve_recording(session_factory, pp, i))
    for i in w.mirror:
        await _safely("mirror", i, mirror.ensure_setup(i, pp, settings, session_factory))
    if w.total():
        log.info(
            "sweep: %d events, %d created, %d capture_pending, %d recording, %d mirror",
            len(w.events),
            len(w.created),
            len(w.capture_pending),
            len(w.recording),
            len(w.mirror),
        )
    return w.total()


async def _safely(kind: str, item: int, work: Any) -> None:
    try:
        await work
    except Exception:  # one bad item never stops the rest; it's retried next run
        log.exception("sweep %s %s failed", kind, item)


async def process_then_sweep(
    session_factory: Callable[[], Session], pp: PayPal, settings: Settings, event_id: int
) -> None:
    """The webhook's BackgroundTask (B1, TRD §6b-4(a)): this event, then the sweep — which only
    queries unless something is stale — so a sleeping worker never delays the catch-up."""
    await process_event(session_factory, pp, settings, event_id)
    await sweep(session_factory, pp, settings)
