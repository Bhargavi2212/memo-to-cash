# ruff: noqa: F811 — the fixtures are shared from test_supplier_api
"""Phase M option 2 (P3 §M M2, M9): the demo start gates on Northside; Harbor Gems and Lakeside
Fine Jewelry go on PayPal right after the response. Until a buyer is on PayPal it can't be paid
or recorded; opening it runs or joins the same setup; failures retry without duplicates."""

import asyncio
import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select

from backend.db.models import Buyer, Invoice, Payment
from backend.db.session import SessionLocal
from backend.paypal.client import PayPal
from backend.services import demo, mirror
from tests.integration.test_demo import SEED_TRIAGE
from tests.integration.test_supplier_api import (  # noqa: F401 — fixtures
    AUTH,
    clients,
    fake,
    no_backoff,
    settings,
)
from tests.llm_fake import FakeLLM
from tests.paypal_fake import error

HARBOR, LAKESIDE = "Harbor Gems", "Lakeside Fine Jewelry"


def started_copy(settings, fake, ip="7.7.7.1"):
    """`start_demo` as the route runs it, WITHOUT the BackgroundTask: Harbor/Lakeside pending."""
    pp = PayPal(settings, transport=fake.transport())
    llm = FakeLLM(triage=[SEED_TRIAGE])
    st = asyncio.run(
        demo.start_demo(settings, pp, SessionLocal, ip=ip, now=datetime.now(UTC), client=llm)
    )
    return st, pp


def buyers_of(db, st):
    db.expire_all()
    return {
        b.name: b for b in db.scalars(select(Buyer).where(Buyer.demo_session == st.demo_session))
    }


def state(db, buyer):
    return mirror.buyer_setup_state(db, buyer.id)


def test_start_returns_before_harbor_and_lakeside_are_on_paypal(clients, db, settings, fake):
    st, pp = started_copy(settings, fake)
    b = buyers_of(db, st)
    assert fake.ops().count("create") == 3  # Northside's three, inside the request
    assert state(db, b["Northside Jewelers"]) == "READY"
    assert state(db, b[HARBOR]) == state(db, b[LAKESIDE]) == "SETTING_UP"
    assert sorted(st.others) == sorted([b[HARBOR].id, b[LAKESIDE].id])
    assert all(x.demo_ready for x in b.values())  # the copy is reachable; the two show "setting up"

    a, _ = clients
    s = a.get(f"/api/statement/{b[HARBOR].statement_token}").json()
    assert s["paypal_setup"] == "SETTING_UP" and len(s["invoices"]) == 2  # invoices still shown

    asyncio.run(mirror.setup_buyers(st.others, pp, settings, SessionLocal))  # the BackgroundTask
    assert fake.ops().count("create") == 7 and fake.ops().count("send") == 7
    assert {n: state(db, x) for n, x in buyers_of(db, st).items()} == dict.fromkeys(
        ["Northside Jewelers", HARBOR, LAKESIDE], "READY")  # fmt: skip


def test_the_route_runs_the_background_setup(clients, db, fake):
    a, _ = clients
    r = a.post("/api/demo/start", headers={"X-Forwarded-For": "7.7.7.2"})
    assert r.status_code == 200, r.text  # TestClient finishes the BackgroundTask before returning
    assert db.scalar(select(func.count()).where(Invoice.ledger_status == "DRAFT")) == 0
    assert fake.ops().count("create") == 7
    rows = {x["name"]: x["paypal_setup"] for x in a.get("/api/supplier/overview").json()["buyers"]}
    assert set(rows.values()) == {"READY"} and len(rows) == 3


def test_a_failed_background_setup_is_retried_from_the_page_without_duplicates(
    clients, db, settings, fake
):
    st, pp = started_copy(settings, fake, "7.7.7.3")
    fake.fail("send", *[error(400, "VALIDATION_ERROR")] * 4)  # all four sends refused
    asyncio.run(mirror.setup_buyers(st.others, pp, settings, SessionLocal))
    b = buyers_of(db, st)
    assert state(db, b[HARBOR]) == state(db, b[LAKESIDE]) == "FAILED"
    assert fake.ops().count("create") == 7  # created on PayPal, ids stored, sends failed

    a, _ = clients
    fake.faults.clear()
    r = a.post(f"/api/statement/{b[HARBOR].statement_token}/setup")
    assert r.status_code == 200 and r.json() == {"paypal_setup": "READY"}
    a.cookies.set(demo.COOKIE_NAME, demo.make_demo_cookie(st.demo_session, settings))
    r = a.post(f"/api/supplier/buyers/{b[LAKESIDE].id}/setup")  # the supplier page's Retry
    assert r.status_code == 200 and r.json() == {"paypal_setup": "READY"}
    assert fake.ops().count("create") == 7 and len(fake.invoices) == 7  # adopted, no duplicates
    numbers = [i["detail"]["invoice_number"] for i in fake.invoices.values()]
    assert len(set(numbers)) == 7


def test_a_buyer_not_on_paypal_cant_be_paid_or_recorded(clients, db, settings, fake):
    st, _ = started_copy(settings, fake, "7.7.7.4")
    harbor = buyers_of(db, st)[HARBOR]
    a, _ = clients
    r = a.post(f"/api/statement/{harbor.statement_token}/order", json={})
    assert r.status_code == 409 and r.json()["error"]["code"] == "PAYPAL_NOT_READY"
    r = a.post("/api/supplier/payments/record", headers=AUTH, json={
        "buyer_id": harbor.id, "client_uid": str(uuid.uuid4()), "method": "CHECK",
        "reference": "1", "amount_cents": 10000})  # fmt: skip
    assert r.status_code == 409 and r.json()["error"]["code"] == "PAYPAL_NOT_READY"
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(Payment)) == 0
    assert "order_create" not in fake.ops()


def test_two_setups_at_once_create_each_invoice_once(db, settings, fake):
    st, pp = started_copy(settings, fake, "7.7.7.5")
    harbor = buyers_of(db, st)[HARBOR]

    async def both():
        return await asyncio.gather(
            mirror.ensure_setup(harbor.id, pp, settings, SessionLocal),
            mirror.ensure_setup(harbor.id, pp, settings, SessionLocal),
        )

    assert asyncio.run(both()) == ["READY", "READY"]
    assert fake.ops().count("create") == 3 + 2  # Northside 3 + Harbor's 2, once each


def test_setup_on_a_ready_buyer_makes_no_paypal_call(clients, db, fake):
    a, _ = clients
    tok = a.post("/api/demo/start", headers={"X-Forwarded-For": "7.7.7.6"}).json()
    fake.calls.clear()
    r = a.post(f"/api/statement/{tok['statement_token']}/setup")
    assert r.json() == {"paypal_setup": "READY"} and fake.calls == []


def test_setup_is_scoped(clients, db, settings, fake):
    st, _ = started_copy(settings, fake, "7.7.7.7")
    harbor = buyers_of(db, st)[HARBOR]
    _, b = clients
    b.post("/api/demo/start", headers={"X-Forwarded-For": "7.7.7.8"})  # another visitor's copy
    assert b.post(f"/api/supplier/buyers/{harbor.id}/setup").status_code == 404
    assert b.post("/api/statement/not-a-real-token/setup").status_code == 404
