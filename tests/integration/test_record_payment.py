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
    payload = {"buyer_id": buyer_id, "client_uid": str(uid or uuid.uuid4()), "method": "CHECK",
               "reference": "1043"} | body  # fmt: skip
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
        ("INV-1038", 200000)]  # fmt: skip
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
    inv = db.scalars(select(Invoice).where(Invoice.erp_invoice_no == "INV-1038",
                                           Invoice.buyer_id == bid)).one()  # fmt: skip
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


def test_a_back_dated_wire_keeps_its_date(clients, db, fake):
    a, _ = clients
    bid = start(a, "8.8.8.7")["buyer_id"]
    day = today_ny() - timedelta(days=3)
    r = record(a, bid, amount_cents=10000, method="WIRE_TRANSFER", reference="FED 77",
               received_on=day.isoformat())  # fmt: skip
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
    r = a.post("/api/supplier/payments/record", headers=AUTH,
               json={"buyer_id": bid, "client_uid": str(uuid.uuid4()), "method": "OTHER",
                     "reference": "Zelle 5521", "amount_cents": 10000})  # fmt: skip
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
    assert steps == [("payment.recorded", None), ("allocation.recorded", "INV-1038"),
                     ("allocation.recorded", "INV-1042")]  # fmt: skip
    start(b, "9.9.9.2")
    assert b.get(f"/api/supplier/payments/{pid}").status_code == 404  # another visitor
    assert a.get("/api/supplier/payments/999999").status_code == 404
