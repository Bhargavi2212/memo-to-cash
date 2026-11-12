# ruff: noqa: F811 — fixtures shared from test_payments_flow
"""Webhook event processing (FR-7; P3b B2, B3, X1, X6; plan 1b, G5, G10, G16; TRD §6b–6b-2;
invariants 3, 4, 9, 10). Services called directly with the fake PayPal; real Postgres."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, update

from backend.db.models import Allocation, AuditLog, Buyer, Invoice, Payment, Proposal, WebhookEvent
from backend.db.session import SessionLocal
from backend.paypal.client import PayPal
from backend.services import payments as pay
from backend.services import sync
from backend.services.demo import build_demo_csv
from backend.services.importer import import_csv
from backend.services.mirror import mirror_invoices
from tests.integration.test_payments_flow import (  # noqa: F401 — fixtures + helpers
    D,
    allocs,
    buyer,
    capture,
    fake,
    inv,
    no_backoff,
    order,
    paid_5000,
    payment,
    pp,
    settings,
)

pytestmark = pytest.mark.anyio


def mirror_settings(settings):
    return settings.model_copy(
        update={
            "app_base_url": "https://memo.example",
            "paypal_business_email": "supplier@example.com",
        }
    )


async def event(pp, settings, event_type, resource, now=None):
    eid = sync.store_event(
        SessionLocal, {"id": f"WH-{uuid.uuid4()}", "event_type": event_type, "resource": resource}
    )
    return eid, await sync.process_event(SessionLocal, pp, settings, eid, now=now)


def paid_event(i):
    return {"invoice": {"id": i.paypal_invoice_id, "status": "PAID"}}


def ledger_ok(db, invoice_no, buyer_id=None):
    i = inv(db, invoice_no, buyer_id)
    total = db.scalar(
        select(func.coalesce(func.sum(Allocation.amount_cents), 0)).where(
            Allocation.invoice_id == i.id
        )
    )
    return i.paid_cents == total


async def three_buyers(db, fake, settings):
    """Phase M copy imported as real buyers, every invoice on the fake PayPal."""
    res = import_csv(db, build_demo_csv(D, "judge@example.com"))
    assert res.ok
    db.commit()
    s = mirror_settings(settings)
    for bid in res.buyer_ids:
        assert (
            await mirror_invoices(
                bid, PayPal(s, transport=fake.transport()), s, SessionLocal, today=D
            )
        ).ok
    return {b.name: b for b in db.scalars(select(Buyer))}


# ---------- J6 backfill (TRD §6b) ----------


async def test_paid_on_paypals_page_is_taken_in_for_that_buyer_only(db, pp, fake, settings):
    b = await three_buyers(db, fake, settings)
    harbor, north = b["Harbor Gems"], b["Northside Jewelers"]
    i55 = inv(db, "INV-1055", harbor.id)
    fake.pay_elsewhere(i55.paypal_invoice_id, 145000)
    _, result = await event(pp, settings, "INVOICING.INVOICE.PAID", paid_event(i55))
    assert result == "done"
    i55 = inv(db, "INV-1055", harbor.id)
    assert (i55.paid_cents, i55.ledger_status, i55.mismatch) == (145000, "PAID", None)
    p = db.scalars(select(Payment).where(Payment.source == "PAYPAL_INVOICE_PAGE")).one()
    assert (p.buyer_id, p.status, p.amount_cents, p.unapplied_cents) == (
        harbor.id,
        "CAPTURED",
        145000,
        0,
    )
    assert ledger_ok(db, "INV-1055", harbor.id)
    assert (
        sum(i.paid_cents for i in db.scalars(select(Invoice).where(Invoice.buyer_id == north.id)))
        == 0
    )  # Northside untouched
    via = db.scalars(select(AuditLog.detail).where(AuditLog.action == "sync.backfill")).one()
    assert via["via"] == "webhook"  # X7


async def test_the_same_payment_twice_is_taken_in_once(db, pp, fake, settings):
    b = await three_buyers(db, fake, settings)
    i55 = inv(db, "INV-1055", b["Harbor Gems"].id)
    fake.pay_elsewhere(i55.paypal_invoice_id, 145000)
    await event(pp, settings, "INVOICING.INVOICE.PAID", paid_event(i55))
    _, again = await event(pp, settings, "INVOICING.INVOICE.PAID", paid_event(i55))
    assert again == "unchanged"
    out = await sync.reconcile_invoice(SessionLocal, pp, i55.id, via="sweep")  # G10
    assert out.state == "unchanged"
    assert db.scalar(select(func.count()).where(Payment.source == "PAYPAL_INVOICE_PAGE")) == 1


async def test_our_own_recorded_payments_are_not_new_money(db, pp, fake, buyer, settings):
    o = await paid_5000(db, pp, fake, buyer, settings)
    await pay.record_allocations(SessionLocal, pp, o.payment_id)
    i38 = inv(db, "INV-1038")
    _, result = await event(pp, settings, "INVOICING.INVOICE.PAID", paid_event(i38))
    assert result == "unchanged"
    # even without the stored transaction id, the [MTC:<uid>] note token says it's ours
    db.execute(update(Allocation).values(paypal_transaction_id=None))
    db.commit()
    assert (
        await sync.reconcile_invoice(SessionLocal, pp, i38.id, via="sweep")
    ).state == "unchanged"
    assert db.scalar(select(func.count()).where(Payment.source == "PAYPAL_INVOICE_PAGE")) == 0
    assert inv(db, "INV-1038").paid_cents == 350000


async def test_an_invoice_with_a_recording_allocation_waits_for_the_sweep(
    db, pp, fake, buyer, settings
):
    o = await paid_5000(db, pp, fake, buyer, settings)  # allocations PENDING
    db.execute(
        update(Allocation)
        .where(Allocation.payment_id == o.payment_id)
        .values(record_state="RECORDING")
    )
    db.commit()
    i38 = inv(db, "INV-1038")
    eid, result = await event(pp, settings, "INVOICING.INVOICE.PAID", paid_event(i38))
    assert result == "recording"
    ev = db.get(WebhookEvent, eid)
    db.refresh(ev)
    assert ev.processed_at is None and ev.error == sync.WAITING_RECORDING
    assert "get" not in fake.ops()[-1:]  # didn't even ask PayPal


# ---------- P2 D23 reconcile (R4, X6) ----------


async def test_d23_full_overlap_deletes_our_allocation_and_credits_it(
    db, pp, fake, buyer, settings
):
    o = await paid_5000(db, pp, fake, buyer, settings)  # 3,500 → INV-1038, 1,500 → INV-1042
    i38 = inv(db, "INV-1038")
    fake.pay_elsewhere(i38.paypal_invoice_id, 350000)  # paid on PayPal's page first
    await pay.record_allocations(SessionLocal, pp, o.payment_id)  # ours: FAILED overpay
    assert allocs(db, o.payment_id)["INV-1038"].record_state == "FAILED"

    _, result = await event(pp, settings, "INVOICING.INVOICE.PAID", paid_event(i38))
    assert result == "done"
    assert "INV-1038" not in allocs(db, o.payment_id)  # fully overlapped → deleted
    p = payment(db, o.payment_id)
    assert p.unapplied_cents == 350000  # the second payment is held as credit
    i38 = inv(db, "INV-1038")
    assert (i38.paid_cents, i38.mismatch) == (350000, None) and ledger_ok(db, "INV-1038")
    reply = db.scalars(select(Proposal).where(Proposal.kind == "REPLY")).one()
    assert reply.payload["reason"] == (
        "$3,500.00 paid twice on INV-1038 — held as credit; "
        "refund it from PayPal if they want it back."
    )  # X6
    assert reply.evidence["internal"] is True and reply.status == "PENDING"


async def test_d23_partial_overlap_shrinks_and_rerecords_ours(db, pp, fake, buyer, settings):
    o = await paid_5000(db, pp, fake, buyer, settings)  # 1,500 → INV-1042 (total 3,500)
    i42 = inv(db, "INV-1042")
    fake.pay_elsewhere(i42.paypal_invoice_id, 250000)  # $2,500 on PayPal's page → $1,000 left
    await pay.record_allocations(SessionLocal, pp, o.payment_id)
    old = allocs(db, o.payment_id)["INV-1042"]
    assert old.record_state == "FAILED"
    old_uid = old.uid

    await event(pp, settings, "INVOICING.INVOICE.PAID", paid_event(i42))
    a = allocs(db, o.payment_id)["INV-1042"]
    assert a.amount_cents == 100000 and a.uid != old_uid  # fresh uid → new Request-Id/token
    assert a.record_state == "RECORDED"  # re-recorded at its new amount
    assert payment(db, o.payment_id).unapplied_cents == 50000
    i42 = inv(db, "INV-1042")
    assert i42.paid_cents == 350000 and ledger_ok(db, "INV-1042")
    assert fake.invoices[i42.paypal_invoice_id]["due_amount"]["value"] == "0.00"
    assert i42.mismatch is None


# ---------- lost capture / approved but not captured (TRD §6b-2, X1) ----------


async def lost_capture(pp, fake, buyer):
    """The buyer approved and PayPal captured, but our /capture never heard back."""
    o = await order(pp, buyer)
    fake.approve(o.paypal_order_id)
    from backend.paypal import orders

    body = await orders.capture(pp, o.paypal_order_id, "lost", 0)
    return o, orders.first_capture(body)


async def test_capture_completed_finishes_a_lost_capture_once(db, pp, fake, buyer, settings):
    o, cap = await lost_capture(pp, fake, buyer)
    assert payment(db, o.payment_id).status == "CREATED"
    _, result = await event(pp, settings, "PAYMENT.CAPTURE.COMPLETED", cap)
    assert result == "CAPTURED"
    assert payment(db, o.payment_id).status == "CAPTURED"
    _, again = await event(pp, settings, "PAYMENT.CAPTURE.COMPLETED", cap)
    assert again == "CAPTURED"  # already settled → nothing new
    assert db.scalar(select(func.count()).where(Allocation.payment_id == o.payment_id)) == 2
    r = await capture(pp, buyer, o.paypal_order_id)  # the buyer's late /capture → receipt
    assert r.status == "CAPTURED"


async def test_order_approved_waits_ten_minutes_before_a_server_capture(
    db, pp, fake, buyer, settings
):
    o = await order(pp, buyer)
    fake.approve(o.paypal_order_id)
    res = {
        "id": o.paypal_order_id,
        "purchase_units": [{"custom_id": str(payment(db, o.payment_id).uid)}],
    }
    _, early = await event(pp, settings, "CHECKOUT.ORDER.APPROVED", res)
    assert early == "too_early" and "order_capture" not in fake.ops()  # X1
    later = datetime.now(UTC) + timedelta(minutes=11)
    _, result = await event(pp, settings, "CHECKOUT.ORDER.APPROVED", res, now=later)
    assert result == "CAPTURED" and fake.ops().count("order_capture") == 1
    r = await capture(pp, buyer, o.paypal_order_id)  # client second → receipt, no 2nd capture
    assert r.status == "CAPTURED" and fake.ops().count("order_capture") == 1


async def test_client_capture_first_then_the_webhook_is_a_no_op(db, pp, fake, buyer, settings):
    o = await order(pp, buyer)
    fake.approve(o.paypal_order_id)
    assert (await capture(pp, buyer, o.paypal_order_id)).status == "CAPTURED"
    uid = str(payment(db, o.payment_id).uid)
    later = datetime.now(UTC) + timedelta(minutes=11)
    res = {"id": o.paypal_order_id, "purchase_units": [{"custom_id": uid}]}
    _, result = await event(pp, settings, "CHECKOUT.ORDER.APPROVED", res, now=later)
    assert result == "settled" and fake.ops().count("order_capture") == 1
    cap = fake.orders[o.paypal_order_id]["purchase_units"][0]["payments"]["captures"][0]
    _, done = await event(pp, settings, "PAYMENT.CAPTURE.COMPLETED", cap)
    assert done == "CAPTURED"
    assert db.scalar(select(func.count()).where(Allocation.payment_id == o.payment_id)) == 2


async def test_order_approved_never_captures_a_voided_payment(db, pp, fake, buyer, settings):
    o = await order(pp, buyer)
    fake.approve(o.paypal_order_id)
    db.execute(update(Payment).where(Payment.id == o.payment_id).values(status="VOIDED"))
    db.commit()
    res = {
        "id": o.paypal_order_id,
        "purchase_units": [{"custom_id": str(payment(db, o.payment_id).uid)}],
    }
    later = datetime.now(UTC) + timedelta(minutes=11)
    _, result = await event(pp, settings, "CHECKOUT.ORDER.APPROVED", res, now=later)
    assert result == "settled" and "order_capture" not in fake.ops()


# ---------- shown, not automated (G5) · unknown (G16) ----------


async def test_cancelled_and_refunded_are_shown_not_applied(db, pp, fake, buyer, settings):
    o = await paid_5000(db, pp, fake, buyer, settings)
    i42 = inv(db, "INV-1042")
    await event(
        pp,
        settings,
        "INVOICING.INVOICE.CANCELLED",
        {"invoice": {"id": i42.paypal_invoice_id, "status": "CANCELLED"}},
    )
    i42 = inv(db, "INV-1042")
    assert (
        i42.mismatch == "PayPal shows this invoice cancelled" and i42.paypal_status == "CANCELLED"
    )
    assert (i42.paid_cents, i42.ledger_status) == (150000, "PARTIALLY_PAID")  # ledger untouched
    cap = {
        "id": payment(db, o.payment_id).paypal_capture_id,
        "custom_id": str(payment(db, o.payment_id).uid),
    }
    _, r = await event(pp, settings, "PAYMENT.CAPTURE.REFUNDED", cap)
    assert r == "refunded" and payment(db, o.payment_id).status == "CAPTURED"
    assert inv(db, "INV-1038").mismatch.startswith("PayPal shows a refund")


async def test_events_for_things_we_dont_have_are_ignored(db, pp, fake, settings):
    for t, res in [
        ("INVOICING.INVOICE.PAID", {"invoice": {"id": "INV2-DEV-ONLY"}}),
        ("PAYMENT.CAPTURE.COMPLETED", {"id": "CAP-X", "custom_id": str(uuid.uuid4())}),
        ("CHECKOUT.ORDER.APPROVED", {"id": "ORDER-X", "purchase_units": [{}]}),
        ("BILLING.SUBSCRIPTION.CREATED", {}),
    ]:
        eid, result = await event(pp, settings, t, res)
        assert result == sync.IGNORED_UNKNOWN
        ev = db.get(WebhookEvent, eid)
        db.refresh(ev)
        assert ev.processed_at is not None and ev.error == sync.IGNORED_UNKNOWN


async def test_two_buyers_paid_at_once_dont_block_each_other(db, pp, fake, settings):
    b = await three_buyers(db, fake, settings)
    i55 = inv(db, "INV-1055", b["Harbor Gems"].id)
    i40 = inv(db, "INV-1040", b["Lakeside Fine Jewelry"].id)
    fake.pay_elsewhere(i55.paypal_invoice_id, 145000)
    fake.pay_elsewhere(i40.paypal_invoice_id, 350000)
    outs = await asyncio.gather(
        sync.reconcile_invoice(SessionLocal, pp, i55.id, via="webhook"),
        sync.reconcile_invoice(SessionLocal, pp, i40.id, via="webhook"),
    )
    assert [o.state for o in outs] == ["done", "done"]
    assert inv(db, "INV-1040", b["Lakeside Fine Jewelry"].id).paid_cents == 350000


# ---------- 1c: the sweep (B4, G6, F8, G9) ----------


def age(db, model, row_id, **deltas):
    """Move a row's timestamps into the past (the sweep's fake clock)."""
    now = datetime.now(UTC)
    db.execute(
        update(model)
        .where(model.id == row_id)
        .values(**{col: now - delta for col, delta in deltas.items()})
    )
    db.commit()


async def test_sweep_processes_an_event_left_waiting(db, pp, fake, buyer, settings):
    o = await paid_5000(db, pp, fake, buyer, settings)
    db.execute(
        update(Allocation)
        .where(Allocation.payment_id == o.payment_id)
        .values(record_state="RECORDING")
    )
    db.commit()
    i38 = inv(db, "INV-1038")
    fake.pay_elsewhere(i38.paypal_invoice_id, 100000)
    eid, first = await event(pp, settings, "INVOICING.INVOICE.PAID", paid_event(i38))
    assert first == "recording"
    db.execute(
        update(Allocation)
        .where(Allocation.payment_id == o.payment_id)
        .values(record_state="PENDING")
    )
    db.commit()
    await sync.sweep(SessionLocal, pp, settings)
    ev = db.get(WebhookEvent, eid)
    db.refresh(ev)
    assert ev.processed_at is not None


async def test_sweep_finishes_a_lost_capture_after_two_minutes(db, pp, fake, buyer, settings):
    o, _cap = await lost_capture(pp, fake, buyer)
    await sync.sweep(SessionLocal, pp, settings)
    assert payment(db, o.payment_id).status == "CREATED"  # < 2 min: left alone
    age(db, Payment, o.payment_id, created_at=timedelta(minutes=3))
    await sync.sweep(SessionLocal, pp, settings)
    assert payment(db, o.payment_id).status == "CAPTURED"
    assert "order_capture" in fake.ops() and fake.ops().count("order_capture") == 1


async def test_sweep_captures_an_approved_order_only_after_ten_minutes(
    db, pp, fake, buyer, settings
):
    o = await order(pp, buyer)
    fake.approve(o.paypal_order_id)
    age(db, Payment, o.payment_id, created_at=timedelta(minutes=5))
    await sync.sweep(SessionLocal, pp, settings)
    assert payment(db, o.payment_id).status == "CREATED" and "order_capture" not in fake.ops()
    age(db, Payment, o.payment_id, created_at=timedelta(minutes=11))
    await sync.sweep(SessionLocal, pp, settings)
    assert payment(db, o.payment_id).status == "CAPTURED"
    assert fake.ops().count("order_capture") == 1


async def test_sweep_voids_an_order_never_approved_after_three_hours(db, pp, fake, buyer, settings):
    o = await order(pp, buyer)
    age(db, Payment, o.payment_id, created_at=timedelta(hours=1))
    await sync.sweep(SessionLocal, pp, settings)
    assert payment(db, o.payment_id).status == "CREATED"
    age(db, Payment, o.payment_id, created_at=timedelta(hours=4))
    await sync.sweep(SessionLocal, pp, settings)
    assert payment(db, o.payment_id).status == "VOIDED" and "order_capture" not in fake.ops()


async def test_sweep_finishes_a_capture_paypal_was_holding(db, pp, fake, buyer, settings):
    o = await order(pp, buyer)
    fake.approve(o.paypal_order_id)
    fake.capture_status = "PENDING"
    await capture(pp, buyer, o.paypal_order_id)
    assert payment(db, o.payment_id).status == "CAPTURE_PENDING"
    await sync.sweep(SessionLocal, pp, settings)
    assert payment(db, o.payment_id).status == "CAPTURE_PENDING"  # PayPal still holds it
    fake.orders[o.paypal_order_id]["purchase_units"][0]["payments"]["captures"][0]["status"] = (
        "COMPLETED"
    )
    await sync.sweep(SessionLocal, pp, settings)
    assert payment(db, o.payment_id).status == "CAPTURED"  # G6
    assert db.scalar(select(func.count()).where(Allocation.payment_id == o.payment_id)) == 2


async def test_sweep_resolves_a_stuck_recording(db, pp, fake, buyer, settings):
    o = await paid_5000(db, pp, fake, buyer, settings)
    db.execute(
        update(Allocation)
        .where(Allocation.payment_id == o.payment_id)
        .values(record_state="RECORDING", record_started_at=datetime.now(UTC))
    )
    db.commit()
    await sync.sweep(SessionLocal, pp, settings)
    assert {a.record_state for a in allocs(db, o.payment_id).values()} == {"RECORDING"}
    for a in allocs(db, o.payment_id).values():
        age(db, Allocation, a.id, record_started_at=timedelta(minutes=6))
    await sync.sweep(SessionLocal, pp, settings)
    assert {a.record_state for a in allocs(db, o.payment_id).values()} == {"RECORDED"}


async def test_sweep_sets_up_draft_invoices_of_ready_buyers_only(db, pp, fake, settings):
    real = import_csv(db, build_demo_csv(D, "judge@example.com"))  # real buyers, all DRAFT
    abandoned = import_csv(db, build_demo_csv(D, "gone@example.com"), demo_session="old")
    db.commit()
    s = mirror_settings(settings)
    pp2 = PayPal(s, transport=fake.transport())
    await sync.sweep(SessionLocal, pp2, s)
    db.expire_all()
    mine = select(func.count()).where(Invoice.ledger_status == "DRAFT")
    assert db.scalar(mine.where(Invoice.buyer_id.in_(real.buyer_ids))) == 0
    assert db.scalar(mine.where(Invoice.buyer_id.in_(abandoned.buyer_ids))) == 7  # G9


async def test_sweep_gives_up_on_an_invoice_after_five_attempts(db, pp, fake, settings):
    res = import_csv(db, build_demo_csv(D, "judge@example.com"))
    db.execute(
        update(Invoice)
        .where(Invoice.buyer_id.in_(res.buyer_ids))
        .values(mirror_attempts=sync.MAX_MIRROR_ATTEMPTS)
    )
    db.commit()
    s = mirror_settings(settings)
    assert await sync.sweep(SessionLocal, PayPal(s, transport=fake.transport()), s) == 0
    assert "create" not in fake.ops()


async def test_a_second_sweep_finds_nothing(db, pp, fake, buyer, settings):
    o, _cap = await lost_capture(pp, fake, buyer)
    age(db, Payment, o.payment_id, created_at=timedelta(minutes=3))
    assert await sync.sweep(SessionLocal, pp, settings) >= 1
    assert await sync.sweep(SessionLocal, pp, settings) == 0


# ---------- 1c: the pay-time check (F4) ----------


async def test_pay_all_after_a_paypal_page_payment_is_refused_and_backfilled(
    db, pp, fake, buyer, settings
):
    await paid_5000(db, pp, fake, buyer, settings)  # mirrors the invoices; INV-1042 has $2,000
    i42 = inv(db, "INV-1042")
    fake.pay_elsewhere(i42.paypal_invoice_id, 200000)  # buyer pays the rest on PayPal's page
    orders_before = fake.ops().count("order_create")
    paid = await sync.pay_check(SessionLocal, pp, buyer.statement_token, None, None)
    assert paid == ["INV-1042"]
    assert fake.ops().count("order_create") == orders_before  # no order: nothing charged twice
    assert inv(db, "INV-1042").paid_cents == 350000 and ledger_ok(db, "INV-1042")
    via = db.scalars(select(AuditLog.detail).where(AuditLog.action == "sync.backfill")).one()
    assert via["via"] == "pay_check"  # X7
    assert await sync.pay_check(SessionLocal, pp, buyer.statement_token, None, None) == []


async def test_the_pay_check_gives_up_quietly_when_paypal_is_slow(
    db, pp, fake, buyer, settings, monkeypatch
):
    await paid_5000(db, pp, fake, buyer, settings)
    monkeypatch.setattr(sync, "PAY_CHECK_S", 0.2)
    fake.delay["get"] = 1.0
    assert await sync.pay_check(SessionLocal, pp, buyer.statement_token, None, None) == []


async def test_the_order_route_answers_409_paid_elsewhere(db, pp, fake, buyer, settings):
    from fastapi.testclient import TestClient

    from backend.core.config import get_settings
    from backend.main import app
    from backend.paypal.client import get_paypal

    await paid_5000(db, pp, fake, buyer, settings)
    fake.pay_elsewhere(inv(db, "INV-1042").paypal_invoice_id, 200000)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_paypal] = lambda: pp
    try:
        with TestClient(app) as c:
            r = c.post(f"/api/statement/{buyer.statement_token}/order", json={})
    finally:
        app.dependency_overrides.clear()
    assert r.status_code == 409
    assert r.json()["error"] == {
        "code": "PAID_ELSEWHERE",
        "message": "INV-1042 was paid on PayPal — your statement is updated.",
    }


# ---------- step 1 adds (Bhargavi, Wed night): no webhook · single flight ----------


async def test_without_a_webhook_the_sweep_reconciles_a_d23_overpay(db, pp, fake, buyer, settings):
    o = await paid_5000(db, pp, fake, buyer, settings)
    i38 = inv(db, "INV-1038")
    fake.pay_elsewhere(i38.paypal_invoice_id, 350000)
    await pay.record_allocations(SessionLocal, pp, o.payment_id)  # FAILED overpay, no event
    assert db.scalar(select(func.count()).select_from(WebhookEvent)) == 0
    await sync.sweep(SessionLocal, pp, settings)
    assert "INV-1038" not in allocs(db, o.payment_id)
    assert payment(db, o.payment_id).unapplied_cents == 350000 and ledger_ok(db, "INV-1038")
    via = db.scalars(select(AuditLog.detail).where(AuditLog.action == "sync.backfill")).one()
    assert via["via"] == "sweep"
    assert await sync.sweep(SessionLocal, pp, settings) == 0  # done: nothing left to look at


async def test_an_overpay_with_nothing_new_on_paypal_is_looked_at_five_times(
    db, pp, fake, buyer, settings
):
    o = await paid_5000(db, pp, fake, buyer, settings)
    db.execute(
        update(Allocation)
        .where(Allocation.payment_id == o.payment_id)
        .values(record_state="FAILED", paypal_record_error=f"{pay.OVERPAY_ISSUE}: test")
    )
    db.commit()
    gets = []
    for _ in range(7):
        fake.calls.clear()
        await sync.sweep(SessionLocal, pp, settings)
        gets.append(fake.ops().count("get"))
    assert sum(gets) == 10 and gets[-1] == 0  # 2 invoices × 5 looks, then it stops


async def test_a_second_sweep_while_one_runs_is_skipped(db, pp, fake, buyer, settings):
    o, _cap = await lost_capture(pp, fake, buyer)
    age(db, Payment, o.payment_id, created_at=timedelta(minutes=3))
    fake.delay["order_get"] = 0.5
    first, second = await asyncio.gather(
        sync.sweep(SessionLocal, pp, settings), sync.sweep(SessionLocal, pp, settings)
    )
    assert sorted([first, second]) == [0, 1]  # one did the work, the other returned at once
    assert fake.ops().count("order_get") == 1
    assert await sync.sweep(SessionLocal, pp, settings) == 0  # the flag was released
