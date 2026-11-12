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
