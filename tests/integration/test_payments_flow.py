"""Order → capture → finish_capture (FR-5; P2 D4–D6, D10, R1, R2, R7, R8; invariants 2, 4, 9).

Real Postgres (advisory locks, unique keys) + the fake PayPal (tests/paypal_fake.py), whose
Orders answers have the shape of the real smoke fixtures.
"""

import asyncio
import time

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import func, select, text

from backend.core.config import load_settings
from backend.core.dates import today_ny
from backend.db.models import Allocation, AuditLog, Buyer, Invoice, Payment
from backend.db.session import SessionLocal
from backend.paypal import client as ppclient
from backend.paypal.client import PayPal
from backend.services import payments as pay
from backend.services.demo import build_csv
from backend.services.importer import import_csv
from backend.services.statement import StatementNotFoundError
from tests.paypal_fake import FakePayPal, error

pytestmark = pytest.mark.anyio
D = today_ny()


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch):
    async def instant(_s):
        return None

    monkeypatch.setattr(ppclient, "_sleep", instant)
    ppclient.clear_token_cache()


@pytest.fixture
def settings():
    return load_settings(
        _env_file=None,
        app_env="test",
        paypal_client_id="id",
        paypal_client_secret=SecretStr("s"),
    )


@pytest.fixture
def fake():
    return FakePayPal()


@pytest.fixture
def pp(fake, settings):
    return PayPal(settings, transport=fake.transport())


def seed(db, email="buyer@example.com"):
    res = import_csv(
        db, build_csv(D, email), demo_session=None if email == "buyer@example.com" else email
    )
    assert res.ok
    b = db.get(Buyer, res.buyer_ids[0])
    b.demo_ready = True
    db.commit()
    return b


@pytest.fixture
def buyer(db):
    return seed(db)


def inv(db, no, buyer_id=None):
    db.expire_all()
    q = select(Invoice).where(Invoice.erp_invoice_no == no)
    if buyer_id:
        q = q.where(Invoice.buyer_id == buyer_id)
    return db.scalars(q).first()


def payment(db, payment_id):
    db.expire_all()
    return db.get(Payment, payment_id)


async def order(pp, buyer, amount=500000, ids=None, funding="paypal"):
    return await pay.create_order(SessionLocal, pp, buyer.statement_token, ids, amount, funding)


async def capture(pp, buyer, order_id):
    return await pay.capture_payment(SessionLocal, pp, buyer.statement_token, order_id)


def allocations(db, payment_id):
    db.expire_all()
    return {
        (inv_no, cents)
        for inv_no, cents in db.execute(
            select(Invoice.erp_invoice_no, Allocation.amount_cents)
            .join(Invoice, Allocation.invoice_id == Invoice.id)
            .where(Allocation.payment_id == payment_id)
        )
    }


# ---------------------------------------------------------------- happy path


async def test_order_then_capture_5000(db, pp, fake, buyer, settings):
    o = await order(pp, buyer)
    p = payment(db, o.payment_id)
    assert p.status == "CREATED" and p.paypal_order_id == o.paypal_order_id
    assert [x["amount_cents"] for x in p.planned_split] == [350000, 150000]
    assert fake.request_ids("order_create") == [f"test-ord-{p.uid}"]

    fake.approve(o.paypal_order_id)
    r = await capture(pp, buyer, o.paypal_order_id)
    assert r.status == "CAPTURED"
    p = payment(db, o.payment_id)
    assert p.paypal_capture_id.startswith("CAP-FAKE") and p.received_at and p.unapplied_cents == 0
    assert allocations(db, p.id) == {("INV-1038", 350000), ("INV-1042", 150000)}
    assert (inv(db, "INV-1038").paid_cents, inv(db, "INV-1038").ledger_status) == (350000, "PAID")
    assert (inv(db, "INV-1042").paid_cents, inv(db, "INV-1042").ledger_status) == (
        150000,
        "PARTIALLY_PAID",
    )
    assert inv(db, "INV-1051").paid_cents == 0
    assert fake.request_ids("order_capture") == [f"test-cap-{p.uid}"]
    a = db.scalars(select(AuditLog).where(AuditLog.action == "payment.captured")).one()
    assert a.actor == "paypal" and a.detail["payment_id"] == p.id


async def test_capture_twice_is_a_no_op(db, pp, fake, buyer):
    o = await order(pp, buyer)
    fake.approve(o.paypal_order_id)
    first = await capture(pp, buyer, o.paypal_order_id)
    again = await capture(pp, buyer, o.paypal_order_id)
    assert first == again and again.status == "CAPTURED"
    assert fake.ops().count("order_capture") == 1
    assert db.scalar(select(func.count()).select_from(Allocation)) == 2


async def test_each_click_is_a_new_payment(db, pp, fake, buyer):
    a, b = await order(pp, buyer), await order(pp, buyer)
    assert a.payment_id != b.payment_id and a.paypal_order_id != b.paypal_order_id


# ---------------------------------------------------------------- invariant 9


async def test_concurrent_captures_serialized(db, pp, fake, buyer):
    """Two full-amount orders captured at once: the lock makes the second wait, re-split,
    find nothing left and void — never a second capture, never over-allocated."""
    a = await order(pp, buyer, amount=842000)
    b = await order(pp, buyer, amount=842000)
    fake.approve(a.paypal_order_id)
    fake.approve(b.paypal_order_id)
    fake.delay["order_capture"] = 0.5
    results = await asyncio.gather(
        capture(pp, buyer, a.paypal_order_id),
        capture(pp, buyer, b.paypal_order_id),
        return_exceptions=True,
    )
    ok = [r for r in results if isinstance(r, pay.CaptureResult)]
    errors = [r for r in results if isinstance(r, pay.PaymentError)]
    assert len(ok) == 1 and len(errors) == 1, results
    assert errors[0].status == 409 and errors[0].code == "STATEMENT_CHANGED"
    assert fake.ops().count("order_capture") == 1  # the R1 GET is allowed, a 2nd capture isn't
    statuses = sorted(payment(db, x.payment_id).status for x in (a, b))
    assert statuses == ["CAPTURED", "VOIDED"]
    assert db.scalar(select(func.sum(Allocation.amount_cents))) == 842000
    assert all(i.paid_cents <= i.total_cents for i in db.scalars(select(Invoice)))


# ---------------------------------------------------------------- balance changed


async def test_balance_changed_but_fits_is_resplit(db, pp, fake, buyer):
    o = await order(pp, buyer, amount=400000)  # planned 1038:350000 + 1042:50000
    i1038 = inv(db, "INV-1038")
    i1038.paid_cents = 100000  # someone paid part of INV-1038 meanwhile
    db.commit()
    fake.approve(o.paypal_order_id)
    r = await capture(pp, buyer, o.paypal_order_id)
    assert r.status == "CAPTURED"
    assert allocations(db, o.payment_id) == {("INV-1038", 250000), ("INV-1042", 150000)}


async def test_balance_changed_and_no_longer_fits_voids_without_capture(db, pp, fake, buyer):
    o = await order(pp, buyer, amount=500000)
    i1038 = inv(db, "INV-1038")
    i1038.paid_cents = 350000
    i1038.ledger_status = "PAID"
    db.commit()
    fake.approve(o.paypal_order_id)
    with pytest.raises(pay.PaymentError) as e:
        await capture(pp, buyer, o.paypal_order_id)
    assert (e.value.status, e.value.code) == (409, "STATEMENT_CHANGED")
    assert payment(db, o.payment_id).status == "VOIDED"
    assert "order_capture" not in fake.ops() and "order_get" in fake.ops()  # R1 asked PayPal


async def test_r1_paypal_holds_the_money_never_voided(db, pp, fake, buyer):
    """Capture went through at PayPal but we never saw the answer; meanwhile balances
    changed so it no longer fits. Retry → GET says COMPLETED → finish + credit, not VOIDED."""
    o = await order(pp, buyer, amount=500000)
    fake.approve(o.paypal_order_id)
    fake._order("order_capture", o.paypal_order_id, {})  # PayPal captured; answer lost
    i1038 = inv(db, "INV-1038")
    i1038.paid_cents = 350000
    i1038.ledger_status = "PAID"
    db.commit()
    r = await capture(pp, buyer, o.paypal_order_id)
    p = payment(db, o.payment_id)
    assert r.status == "CAPTURED" and p.status == "CAPTURED"
    assert allocations(db, p.id) == {("INV-1042", 350000)}
    assert p.unapplied_cents == 150000  # credit, never lost (invariant 9)


# ---------------------------------------------------------------- PayPal outcomes


async def test_order_already_captured_is_finished(db, pp, fake, buyer):
    o = await order(pp, buyer)
    fake.approve(o.paypal_order_id)
    fake._order("order_capture", o.paypal_order_id, {})  # captured, answer lost
    r = await capture(pp, buyer, o.paypal_order_id)
    assert r.status == "CAPTURED" and len(allocations(db, o.payment_id)) == 2


async def test_declined_then_retry_uses_a_new_request_id(db, pp, fake, buyer):
    o = await order(pp, buyer)
    fake.approve(o.paypal_order_id)
    fake.fail(
        "order_capture",
        error(422, "UNPROCESSABLE_ENTITY", details=[{"issue": "INSTRUMENT_DECLINED"}]),
    )
    with pytest.raises(pay.PaymentError) as e:
        await capture(pp, buyer, o.paypal_order_id)
    assert (e.value.status, e.value.code) == (402, "PAYMENT_DECLINED")
    p = payment(db, o.payment_id)
    assert p.status == "CREATED" and p.capture_attempts == 1
    r = await capture(pp, buyer, o.paypal_order_id)
    assert r.status == "CAPTURED"
    assert fake.request_ids("order_capture") == [f"test-cap-{p.uid}", f"test-cap-{p.uid}-1"]


@pytest.mark.parametrize(
    "issue",
    ["TRANSACTION_REFUSED", "PAYER_ACTION_REQUIRED", "MAX_NUMBER_OF_PAYMENT_ATTEMPTS_EXCEEDED"],
)
async def test_other_declines_are_402(db, pp, fake, buyer, issue):
    o = await order(pp, buyer)
    fake.approve(o.paypal_order_id)
    fake.fail("order_capture", error(422, "UNPROCESSABLE_ENTITY", details=[{"issue": issue}]))
    with pytest.raises(pay.PaymentError) as e:
        await capture(pp, buyer, o.paypal_order_id)
    assert e.value.status == 402 and payment(db, o.payment_id).status == "CREATED"


async def test_not_approved_is_409(db, pp, fake, buyer):
    o = await order(pp, buyer)
    with pytest.raises(pay.PaymentError) as e:
        await capture(pp, buyer, o.paypal_order_id)
    assert (e.value.status, e.value.code) == (409, "ORDER_NOT_APPROVED")
    assert payment(db, o.payment_id).status == "CREATED"


async def test_duplicate_invoice_id_not_completed_is_502(db, pp, fake, buyer):
    o = await order(pp, buyer)
    fake.approve(o.paypal_order_id)
    fake.fail(
        "order_capture",
        error(422, "UNPROCESSABLE_ENTITY", details=[{"issue": "DUPLICATE_INVOICE_ID"}]),
    )
    with pytest.raises(pay.PaymentError) as e:
        await capture(pp, buyer, o.paypal_order_id)
    assert (e.value.status, e.value.code) == (502, "PAYPAL_REJECTED")
    assert "order_get" in fake.ops()


async def test_timeout_then_get_completed_is_finished(db, pp, fake, buyer):
    o = await order(pp, buyer)
    fake.approve(o.paypal_order_id)
    fake._order("order_capture", o.paypal_order_id, {})  # PayPal did it...
    fake.fail("order_capture", httpx.ReadTimeout("slow"))  # ...but our call timed out
    r = await capture(pp, buyer, o.paypal_order_id)
    assert r.status == "CAPTURED" and len(allocations(db, o.payment_id)) == 2


async def test_timeout_unconfirmed_is_503_and_retry_finishes(db, pp, fake, buyer):
    o = await order(pp, buyer)
    fake.approve(o.paypal_order_id)
    fake.fail("order_capture", httpx.ReadTimeout("slow"))
    with pytest.raises(pay.PaymentError) as e:
        await capture(pp, buyer, o.paypal_order_id)
    assert (e.value.status, e.value.code) == (503, "CAPTURE_UNCONFIRMED")
    assert payment(db, o.payment_id).status == "CREATED"
    r = await capture(pp, buyer, o.paypal_order_id)  # "Check again" re-POSTs /capture (R4)
    assert r.status == "CAPTURED"


async def test_r7_slow_capture_releases_the_lock(db, pp, fake, buyer, monkeypatch):
    monkeypatch.setattr(pay, "CAPTURE_CAP_S", 0.3)
    o = await order(pp, buyer)
    fake.approve(o.paypal_order_id)
    fake.delay["order_capture"] = 2.0
    with pytest.raises(pay.PaymentError) as e:
        await capture(pp, buyer, o.paypal_order_id)
    assert e.value.code == "CAPTURE_UNCONFIRMED"
    t0 = time.perf_counter()
    with SessionLocal() as s, s.begin():  # the buyer lock must be free now
        s.execute(text("SET LOCAL lock_timeout = '2s'"))
        pay.lock_buyer(s, buyer.id)
    assert time.perf_counter() - t0 < 1.5
    assert payment(db, o.payment_id).status == "CREATED"


async def test_r8_capture_pending(db, pp, fake, buyer):
    o = await order(pp, buyer)
    fake.approve(o.paypal_order_id)
    fake.capture_status = "PENDING"
    r = await capture(pp, buyer, o.paypal_order_id)
    assert r.status == "CAPTURE_PENDING"
    assert allocations(db, o.payment_id) == set() and inv(db, "INV-1038").paid_cents == 0
    again = await capture(pp, buyer, o.paypal_order_id)
    assert again.status == "CAPTURE_PENDING" and fake.ops().count("order_capture") == 1


@pytest.mark.parametrize("status", ["DECLINED", "FAILED"])
async def test_r8_201_with_declined_capture_is_402(db, pp, fake, buyer, status):
    o = await order(pp, buyer)
    fake.approve(o.paypal_order_id)
    fake.capture_status = status
    with pytest.raises(pay.PaymentError) as e:
        await capture(pp, buyer, o.paypal_order_id)
    assert e.value.status == 402
    p = payment(db, o.payment_id)
    assert p.status == "CREATED" and p.capture_attempts == 1 and allocations(db, p.id) == set()


async def test_captured_more_than_ordered_becomes_credit(db, pp, fake, buyer, caplog):
    o = await order(pp, buyer)
    fake.approve(o.paypal_order_id)
    fake.captured_value = "5100.00"
    await capture(pp, buyer, o.paypal_order_id)
    p = payment(db, o.payment_id)
    assert p.unapplied_cents == 10000 and sum(c for _, c in allocations(db, p.id)) == 500000
    assert "captured 510000 cents, expected 500000" in caplog.text


# ---------------------------------------------------------------- order failures + states


async def test_order_create_transient_failure_is_503_and_failed(db, pp, fake, buyer):
    fake.fail("order_create", *[error(503)] * 4)
    with pytest.raises(pay.PaymentError) as e:
        await order(pp, buyer)
    assert (e.value.status, e.value.code) == (503, "PAYPAL_UNAVAILABLE")
    assert db.scalars(select(Payment.status)).all() == ["FAILED"]


async def test_order_create_rejected_is_502(db, pp, fake, buyer):
    fake.fail("order_create", error(400, "INVALID_REQUEST"))
    with pytest.raises(pay.PaymentError) as e:
        await order(pp, buyer)
    assert (e.value.status, e.value.code) == (502, "PAYPAL_REJECTED")


async def test_order_split_errors_write_nothing(db, pp, fake, buyer):
    from backend.core.split import SplitError

    with pytest.raises(SplitError):
        await order(pp, buyer, amount=999999999)
    assert db.scalar(select(func.count()).select_from(Payment)) == 0 and fake.calls == []


async def test_repeat_capture_on_voided_or_failed(db, pp, fake, buyer):
    a, b = await order(pp, buyer), await order(pp, buyer)
    db.expire_all()
    db.get(Payment, a.payment_id).status = "VOIDED"  # set both, then commit (no expire between)
    db.get(Payment, b.payment_id).status = "FAILED"
    db.commit()
    with pytest.raises(pay.PaymentError) as e1:
        await capture(pp, buyer, a.paypal_order_id)
    with pytest.raises(pay.PaymentError) as e2:
        await capture(pp, buyer, b.paypal_order_id)
    assert (e1.value.status, e1.value.code) == (409, "STATEMENT_CHANGED")
    assert (e2.value.status, e2.value.code) == (409, "PAYMENT_FAILED")
    assert "order_capture" not in fake.ops()


async def test_another_buyers_order_is_not_found(db, pp, fake, buyer):
    other = seed(db, "other@example.com")
    o = await order(pp, other)
    fake.approve(o.paypal_order_id)
    with pytest.raises(StatementNotFoundError):
        await capture(pp, buyer, o.paypal_order_id)
    assert "order_capture" not in fake.ops()
