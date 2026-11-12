"""P3a step a1 — supplier scope, overview, buyer detail, proposal cards, /demo/whoami (FR-11;
P3 A1–A3, A5, A10; invariant 7). Demo copies are started through the API (fake PayPal + fake
LLM), exactly as a judge gets them."""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select

from backend.agent import llm
from backend.core.config import get_settings
from backend.core.split import split_payment
from backend.db.models import Allocation, Buyer, Payment, Proposal
from backend.main import app
from backend.paypal import client as ppclient
from backend.paypal.client import PayPal, get_paypal
from backend.services.statement import open_invoices
from tests.integration.test_demo import SEED_TRIAGE, make_settings
from tests.llm_fake import FakeLLM
from tests.paypal_fake import FakePayPal

ADMIN = "admin-token-should-never-leak"
AUTH = {"Authorization": f"Bearer {ADMIN}"}


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch):
    async def instant(_s):
        return None

    monkeypatch.setattr(ppclient, "_sleep", instant)
    ppclient.clear_token_cache()


@pytest.fixture
def settings():
    return make_settings(admin_token=SecretStr(ADMIN))


@pytest.fixture
def fake():
    return FakePayPal()


@pytest.fixture
def clients(db, settings, fake):
    """Two visitors (two cookie jars) on one app with fake PayPal + fake LLM."""
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_paypal] = lambda: PayPal(settings, transport=fake.transport())
    app.dependency_overrides[llm.get_job_client] = lambda: FakeLLM(triage=[SEED_TRIAGE])
    with (TestClient(app, base_url="https://testserver") as a,
          TestClient(app, base_url="https://testserver") as b):  # fmt: skip
        yield a, b
    app.dependency_overrides.clear()


def start(c, ip):
    r = c.post("/api/demo/start", headers={"X-Forwarded-For": ip})
    assert r.status_code == 200, r.text
    return r.json()


def test_overview_shows_the_visitors_own_copy(clients, db):
    a, _ = clients
    mine = start(a, "1.1.1.1")
    r = a.get("/api/supplier/overview")
    assert r.status_code == 200, r.text
    o = r.json()
    # phase M: the copy has three real buyers (Northside $8,420 + Lakeside $5,960 + Harbor $2,300)
    assert o["totals"] == {"open_cents": 1668000, "past_due_cents": 700000,
                           "paid_this_week_cents": 0}  # fmt: skip
    assert {b["name"] for b in o["buyers"]} == {
        "Northside Jewelers", "Lakeside Fine Jewelry", "Harbor Gems"}  # fmt: skip
    (row,) = [b for b in o["buyers"] if b["id"] == mine["buyer_id"]]
    assert row["name"] == "Northside Jewelers"
    assert (row["out"], row["kept"], row["returned"]) == (2, 5, 5)
    assert row["owed_cents"] == 842000 and row["pending_proposals"] == 1
    (card,) = o["pending"]
    assert card["kind"] == "ADJUSTMENT" and card["source"] == "agent"
    assert card["summary"] == "−$20.00 on INV-1051 for S-120"
    assert card["checks"] == ["S-120 kept on INV-1051", "$20.00 ≤ price $1,420.00"]
    assert "S-120" in card["message_text"] and card["due_cents"] == 142000
    assert card["suggested"] == "APPLY" and card["invoice_no"] == "INV-1051"
    assert o["recent_payments"] == []


def test_overview_counts_a_payment_this_week_and_its_split(clients, db):
    a, _ = clients
    bid = start(a, "1.1.1.2")["buyer_id"]
    rows, open_ = open_invoices(db, bid)
    pay = Payment(buyer_id=bid, source="STATEMENT", amount_cents=500000, status="CAPTURED",
                  received_at=datetime.now(UTC))  # fmt: skip
    db.add(pay)
    db.flush()
    by_id = {i.id: i for i in rows}
    for inv_id, cents in split_payment(500000, open_, [o.id for o in open_]):
        db.add(Allocation(payment_id=pay.id, invoice_id=inv_id, amount_cents=cents,
                          record_state="RECORDED"))  # fmt: skip
        by_id[inv_id].paid_cents += cents
    db.commit()
    o = a.get("/api/supplier/overview").json()
    assert o["totals"]["paid_this_week_cents"] == 500000 and o["totals"]["open_cents"] == 1168000
    (p,) = o["recent_payments"]
    assert p["split"] == [{"invoice_no": "INV-1038", "amount_cents": 350000},
                          {"invoice_no": "INV-1042", "amount_cents": 150000}]  # fmt: skip
    assert p["recorded_all"] is True and p["buyer"]["id"] == bid


def test_buyer_detail(clients, db, settings):
    a, _ = clients
    mine = start(a, "1.1.1.3")
    d = a.get(f"/api/supplier/buyers/{mine['buyer_id']}").json()
    assert d["statement_url"].endswith(f"/s/{mine['statement_token']}")
    assert [m["memo_no"] for m in d["memos"]] == ["M-201", "M-207", "M-212"]
    inv = {i["invoice_no"]: i for i in d["invoices"]}
    assert inv["INV-1051"]["paypal_status"] == "UNPAID" and inv["INV-1051"]["mismatch"] is None
    assert inv["INV-1051"]["paypal_url"] and inv["INV-1051"]["adjustments"] == []
    (msg,) = d["messages"]
    assert msg["label"] == "DEDUCTION_REQUEST" and "S-120" in msg["text"]
    assert [p["kind"] for p in d["proposals"]] == ["ADJUSTMENT"]


def test_proposal_list_filters_by_status(clients, db):
    a, _ = clients
    start(a, "1.1.1.4")
    assert len(a.get("/api/supplier/proposals").json()) == 1  # PENDING,FAILED by default
    assert a.get("/api/supplier/proposals?status=DONE").json() == []


# ---------- scoping (invariant 7, A1) ----------


def test_a_demo_cookie_never_reaches_another_copy(clients, db):
    a, b = clients
    mine, theirs = start(a, "2.2.2.1"), start(b, "2.2.2.2")
    their_prop = db.scalars(select(Proposal.id).where(
        Proposal.buyer_id == theirs["buyer_id"])).one()  # fmt: skip
    assert a.get(f"/api/supplier/buyers/{theirs['buyer_id']}").status_code == 404
    r = a.post(f"/api/supplier/proposals/{their_prop}/decide", json={"decision": "REJECT"})
    assert r.status_code == 404
    my_copy = set(db.scalars(select(Buyer.id).where(
        Buyer.demo_session == db.get(Buyer, mine["buyer_id"]).demo_session)))  # fmt: skip
    assert len(my_copy) == 3
    assert {x["id"] for x in a.get("/api/supplier/overview").json()["buyers"]} == my_copy
    assert {p["buyer"]["id"] for p in a.get("/api/supplier/proposals").json()} == {
        mine["buyer_id"]}  # fmt: skip
    db.expire_all()
    assert db.get(Proposal, their_prop).status == "PENDING"


def test_admin_lists_exclude_demo_copies_but_admin_can_act_on_them(clients, db):
    a, _ = clients
    mine = start(a, "3.3.3.1")
    a.cookies.clear()
    o = a.get("/api/supplier/overview", headers=AUTH).json()
    assert o["buyers"] == [] and o["pending"] == [] and o["totals"]["open_cents"] == 0
    assert a.get("/api/supplier/proposals", headers=AUTH).json() == []
    d = a.get(f"/api/supplier/buyers/{mine['buyer_id']}", headers=AUTH)
    assert d.status_code == 200 and d.json()["buyer"]["is_demo"] is True


def test_no_auth_and_unknown_ids(clients, db):
    a, _ = clients
    assert a.get("/api/supplier/overview").status_code == 401
    assert a.get("/api/supplier/buyers/1").status_code == 401
    assert a.get("/api/supplier/proposals").status_code == 401
    start(a, "4.4.4.1")
    assert a.get("/api/supplier/buyers/999999").status_code == 404
    assert (
        a.get("/api/supplier/overview", headers={"Authorization": "Bearer nope"}).status_code == 200
    )  # a wrong header falls back to the valid demo cookie


def test_a_half_built_copy_is_invisible(clients, db):
    a, _ = clients
    mine = start(a, "5.5.5.1")
    session = db.get(Buyer, mine["buyer_id"]).demo_session
    for b in db.scalars(select(Buyer).where(Buyer.demo_session == session)):
        b.demo_ready = False  # a copy is ready all together (phase M, G14)
    db.commit()
    assert a.get(f"/api/supplier/buyers/{mine['buyer_id']}").status_code == 404
    assert a.get("/api/supplier/overview").json()["buyers"] == []


def test_whoami_returns_only_the_cookies_own_token(clients, db):
    a, b = clients
    assert a.get("/api/demo/whoami").json() == {"statement_token": None}
    mine, theirs = start(a, "6.6.6.1"), start(b, "6.6.6.2")
    assert a.get("/api/demo/whoami").json() == {"statement_token": mine["statement_token"]}
    assert b.get("/api/demo/whoami").json() == {"statement_token": theirs["statement_token"]}
    assert mine["statement_token"] != theirs["statement_token"]


def test_supplier_reads_are_ledger_only(clients, db, fake):
    """No PayPal call on the supplier read side (A2)."""
    a, _ = clients
    bid = start(a, "7.7.7.1")["buyer_id"]
    before = list(fake.ops())
    a.get("/api/supplier/overview")
    a.get("/api/supplier/proposals")
    a.get(f"/api/supplier/buyers/{bid}")
    assert fake.ops() == before
