# ruff: noqa: F811 — the fixtures are shared from test_supplier_api
"""P3a step a3 — record an off-PayPal payment (FR-14; P3 A9, R3; G8, G9). Same split rule as the
statement, recorded on each PayPal invoice with the cheque's method, date and reference."""

import json
import uuid
from datetime import timedelta

from sqlalchemy import func, select

from backend.core.dates import today_ny
from backend.db.models import Allocation, Buyer, Invoice, Payment
from tests.integration.test_supplier_api import (  # noqa: F401 — fixtures
    AUTH,
    clients,
    fake,
    no_backoff,
    settings,
    start,
)
from tests.paypal_fake import error


def record(c, buyer_id, uid=None, **body):
    payload = {
        "buyer_id": buyer_id,
        "client_uid": str(uid or uuid.uuid4()),
        "method": "CHECK",
        "reference": "1043",
    } | body
    return c.post("/api/supplier/payments/record", json=payload)


def statement(c, db, buyer_id):
    return c.get(f"/api/statement/{db.get(Buyer, buyer_id).statement_token}").json()


def record_bodies(fake):
    return [json.loads(r.content) for r in fake.requests if fake.classify(r)[0] == "record"]


def test_a_cheque_splits_oldest_first_and_is_recorded_on_paypal(clients, db, fake):
    a, _ = clients
    bid = start(a, "8.8.8.1")["buyer_id"]
    uid = uuid.uuid4()
    r = record(a, bid, uid, amount_cents=200000)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source"] == "OFF_PAYPAL" and body["status"] == "RECORDED"
    assert body["repeat"] is False and body["received_at"] == today_ny().isoformat()
    assert [(x["invoice_no"], x["amount_cents"]) for x in body["allocations"]] == [
        ("INV-1038", 200000)
    ]
    db.expire_all()
    (alloc,) = db.scalars(select(Allocation)).all()
    assert alloc.record_state == "RECORDED"  # the background job ran
    (sent,) = record_bodies(fake)
    assert sent["method"] == "CHECK" and sent["amount"]["value"] == "2000.00"
    assert sent["note"] == f"[MTC:{alloc.uid}] Cheque 1043"
    assert sent["payment_date"] == today_ny().isoformat()
    s = statement(a, db, bid)
    assert s["owed_cents"] == 642000
    (pay,) = s["payments"]
    assert pay["source"] == "OFF_PAYPAL" and pay["method"] == "CHECK"
    assert db.get(Payment, body["payment_id"]).uid == uid
    inv = db.scalars(
        select(Invoice).where(Invoice.erp_invoice_no == "INV-1038", Invoice.buyer_id == bid)
    ).one()
    assert inv.paid_cents == 200000 and inv.ledger_status == "PARTIALLY_PAID"


def test_a_double_click_records_one_cheque(clients, db, fake):
    a, _ = clients
    bid = start(a, "8.8.8.2")["buyer_id"]
    uid = uuid.uuid4()
    first = record(a, bid, uid, amount_cents=50000).json()
    again = record(a, bid, uid, amount_cents=50000).json()
    assert again["payment_id"] == first["payment_id"] and again["repeat"] is True
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(Payment)) == 1
    assert len(record_bodies(fake)) == 1


def test_a_reused_uid_for_another_buyer_is_409(clients, db):
    a, b = clients
    mine, theirs = start(a, "8.8.8.3")["buyer_id"], start(b, "8.8.8.4")["buyer_id"]
    uid = uuid.uuid4()
    assert record(a, mine, uid, amount_cents=10000).status_code == 200
    r = record(b, theirs, uid, amount_cents=10000)
    assert r.status_code == 409 and r.json()["error"]["code"] == "CLIENT_UID_REUSED"


def test_scoping_and_validation(clients, db):
    a, b = clients
    mine, theirs = start(a, "8.8.8.5")["buyer_id"], start(b, "8.8.8.6")["buyer_id"]
    assert record(a, theirs, amount_cents=10000).status_code == 404  # another visitor's copy
    over = record(a, mine, amount_cents=900000)
    assert over.status_code == 422 and over.json()["error"]["code"] == "AMOUNT_EXCEEDS_SELECTED"
    future = (today_ny() + timedelta(days=1)).isoformat()
    r = record(a, mine, amount_cents=10000, received_on=future)
    assert r.status_code == 422 and r.json()["error"]["code"] == "DATE_IN_FUTURE"
    assert record(a, mine, amount_cents=10000, method="CASH").status_code == 422
    assert record(a, mine, amount_cents=10.5).status_code == 422  # integer cents only
    a.cookies.clear()
    assert record(a, mine, amount_cents=10000).status_code == 401
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(Payment)) == 0


def test_an_over_amount_cheque_is_refused_and_records_nothing(clients, db, fake):
    """More than the buyer owes → 422 and no trace: no payment, no allocation, no PayPal call,
    invoices unchanged. Overpayments as credit are carried forward (P3 §8), not v1."""
    a, _ = clients
    buyer = start(a, "8.8.8.9")["buyer_id"]
    owed = statement(a, db, buyer)["owed_cents"]
    paid = lambda: [(i.id, i.paid_cents) for i in db.scalars(select(Invoice).order_by(Invoice.id))]  # noqa: E731
    before = paid()
    fake.requests.clear()
    r = record(a, buyer, amount_cents=owed + 1)
    assert r.status_code == 422 and r.json()["error"]["code"] == "AMOUNT_EXCEEDS_SELECTED"
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(Payment)) == 0
    assert db.scalar(select(func.count()).select_from(Allocation)) == 0
    assert paid() == before
    assert fake.requests == []
    assert record(a, buyer, amount_cents=owed).status_code == 200  # exactly what's owed is fine


def test_a_back_dated_wire_keeps_its_date(clients, db, fake):
    a, _ = clients
    bid = start(a, "8.8.8.7")["buyer_id"]
    day = today_ny() - timedelta(days=3)
    r = record(
        a,
        bid,
        amount_cents=10000,
        method="WIRE_TRANSFER",
        reference="FED 77",
        received_on=day.isoformat(),
    )
    assert r.json()["received_at"] == day.isoformat()
    (sent,) = record_bodies(fake)
    assert sent["payment_date"] == day.isoformat() and sent["method"] == "WIRE_TRANSFER"
    assert sent["note"].endswith("Wire FED 77")


def test_a_failed_recording_is_retried_by_refresh(clients, db, fake):
    a, _ = clients
    bid = start(a, "8.8.8.8")["buyer_id"]
    fake.fail("record", error(400, "VALIDATION_ERROR"))
    pid = record(a, bid, amount_cents=10000).json()["payment_id"]
    db.expire_all()
    assert db.scalars(select(Allocation.record_state)).one() == "FAILED"
    token = db.get(Buyer, bid).statement_token
    r = a.post(f"/api/statement/{token}/payments/{pid}/refresh")
    assert r.status_code == 200, r.text
    db.expire_all()
    assert db.scalars(select(Allocation.record_state)).one() == "RECORDED"


def test_admin_can_record_for_a_demo_copy(clients, db):
    a, _ = clients
    bid = start(a, "8.8.8.9")["buyer_id"]
    a.cookies.clear()
    r = a.post(
        "/api/supplier/payments/record",
        headers=AUTH,
        json={
            "buyer_id": bid,
            "client_uid": str(uuid.uuid4()),
            "method": "OTHER",
            "reference": "Zelle 5521",
            "amount_cents": 10000,
        },
    )
    assert r.status_code == 200 and r.json()["method"] == "OTHER"


# ---------- P3a step a4: payment detail (A4, G11) ----------


def test_payment_detail_shows_split_stored_paypal_values_and_timeline(clients, db, fake):
    a, b = clients
    bid = start(a, "9.9.9.1")["buyer_id"]
    pid = record(a, bid, amount_cents=400000).json()["payment_id"]  # INV-1038 + part of 1042
    calls = list(fake.ops())
    r = a.get(f"/api/supplier/payments/{pid}")
    assert r.status_code == 200, r.text
    d = r.json()
    assert fake.ops() == calls  # stored values only — no PayPal call (live fetch is P3b)
    assert d["payment"]["source"] == "OFF_PAYPAL" and d["payment"]["method"] == "CHECK"
    assert d["buyer"]["id"] == bid
    split = [(x["invoice_no"], x["amount_cents"], x["record_state"]) for x in d["allocations"]]
    assert split == [("INV-1038", 350000, "RECORDED"), ("INV-1042", 50000, "RECORDED")]
    assert all(x["paypal_transaction_id"] and x["paypal_url"] for x in d["allocations"])
    first = d["allocations"][0]
    assert first["paypal_status"] == "MARKED_AS_PAID" and first["paypal_due_cents"] == 0
    steps = [(e["action"], e["invoice_no"]) for e in d["timeline"]]
    assert steps == [
        ("payment.recorded", None),
        ("allocation.recorded", "INV-1038"),
        ("allocation.recorded", "INV-1042"),
    ]
    start(b, "9.9.9.2")
    assert b.get(f"/api/supplier/payments/{pid}").status_code == 404  # another visitor
    assert a.get("/api/supplier/payments/999999").status_code == 404


def test_a_cheque_on_harbor_splits_oldest_first(clients, db, fake):
    """P3 §M M6/M7: Harbor Gems works like Northside — INV-1055 ($1,450, older) first."""
    a, _ = clients
    start(a, "8.2.2.1")
    harbor = next(
        x for x in a.get("/api/supplier/overview").json()["buyers"] if x["name"] == "Harbor Gems"
    )
    r = record(a, harbor["id"], amount_cents=200000)
    assert r.status_code == 200, r.text
    assert [(x["invoice_no"], x["amount_cents"]) for x in r.json()["allocations"]] == [
        ("INV-1055", 145000),
        ("INV-1058", 55000),
    ]
    over = record(a, harbor["id"], amount_cents=30001)  # $300.01 > the $300.00 left
    assert over.status_code == 422 and over.json()["error"]["code"] == "AMOUNT_EXCEEDS_SELECTED"
    s = statement(a, db, harbor["id"])
    assert s["owed_cents"] == 30000


def test_payment_page_live_fetch_asks_paypal_and_stores_what_it_says(clients, db, fake):
    """P3b step 5 (A4): ?live=1 GETs each invoice of the payment from PayPal, ≤ 5 s."""
    a, b = clients
    bid = start(a, "9.9.9.3")["buyer_id"]
    pid = record(a, bid, amount_cents=400000).json()["payment_id"]
    i42 = db.scalars(
        select(Invoice).where(Invoice.buyer_id == bid, Invoice.erp_invoice_no == "INV-1042")
    ).one()
    fake.pay_elsewhere(i42.paypal_invoice_id, 100000)  # PayPal changed meanwhile
    fake.calls.clear()
    d = a.get(f"/api/supplier/payments/{pid}?live=1").json()
    assert fake.ops().count("get") == 2  # one GET per invoice in the split
    row = next(x for x in d["allocations"] if x["invoice_no"] == "INV-1042")
    assert row["paypal_due_cents"] == 200000 and row["mismatch"]  # shown, ours untouched
    db.expire_all()
    assert db.get(Invoice, i42.id).paid_cents == 50000
    start(b, "9.9.9.4")
    fake.calls.clear()
    assert b.get(f"/api/supplier/payments/{pid}?live=1").status_code == 404
    assert fake.calls == []  # scope first: another visitor never makes us call PayPal


def test_payment_page_live_fetch_falls_back_to_stored_values(clients, db, fake, monkeypatch):
    from backend.services import sync

    a, _ = clients
    bid = start(a, "9.9.9.5")["buyer_id"]
    pid = record(a, bid, amount_cents=400000).json()["payment_id"]
    monkeypatch.setattr(sync, "LIVE_FETCH_S", 0.2)
    fake.delay["get"] = 1.0
    r = a.get(f"/api/supplier/payments/{pid}?live=1")
    assert r.status_code == 200
    assert r.json()["allocations"][0]["paypal_status"] == "MARKED_AS_PAID"  # the stored value
