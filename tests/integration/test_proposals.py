"""P4 step 5b — supplier approve → our ledger → PayPal invoice update (FR-10; X2, Z4).

A demo copy is started through the API (fake PayPal + fake LLM), so the invoices are mirrored
and the agent's −$20 S-120 proposal on INV-1051 exists, exactly as in the live demo."""

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import func, select

from backend.agent import llm
from backend.core.config import get_settings
from backend.db.models import AuditLog, Invoice, InvoiceLine, Proposal
from backend.main import app
from backend.paypal import client as ppclient
from backend.paypal import invoices as pinv
from backend.paypal.client import PayPal, get_paypal
from tests.integration.test_demo import SEED_TRIAGE, make_settings
from tests.llm_fake import FakeLLM
from tests.paypal_fake import FakePayPal, error

ADMIN = "admin-token-should-never-leak"
AUTH = {"Authorization": f"Bearer {ADMIN}"}


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch):
    async def instant(_s):
        return None

    monkeypatch.setattr(ppclient, "_sleep", instant)
    ppclient.clear_token_cache()


@pytest.fixture
def fake():
    return FakePayPal()


@pytest.fixture
def settings():
    return make_settings(admin_token=SecretStr(ADMIN))


@pytest.fixture
def api(db, fake, settings):
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_paypal] = lambda: PayPal(settings, transport=fake.transport())
    app.dependency_overrides[llm.get_job_client] = lambda: FakeLLM(triage=[SEED_TRIAGE])
    with TestClient(app, base_url="https://testserver") as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def copy(api, db):
    r = api.post("/api/demo/start", headers={"X-Forwarded-For": "1.2.3.4"})
    assert r.status_code == 200, r.text
    bid = r.json()["buyer_id"]
    prop = db.scalars(select(Proposal).where(Proposal.buyer_id == bid)).one()
    inv = db.get(Invoice, prop.payload["invoice_id"])
    assert prop.evidence["source"] == "agent" and inv.erp_invoice_no == "INV-1051"
    return {"buyer_id": bid, "proposal": prop.id, "invoice": inv.id,
            "paypal_id": inv.paypal_invoice_id}  # fmt: skip


def decide(api, pid, decision="APPROVE", headers=AUTH):
    return api.post(f"/api/supplier/proposals/{pid}/decide", json={"decision": decision},
                    headers=headers)  # fmt: skip


def paypal_adj_lines(fake, paypal_id):
    return [i for i in fake.invoices[paypal_id]["items"] if "[MTC-ADJ:" in i["description"]]


def test_approve_updates_our_ledger_and_the_paypal_invoice(api, db, fake, copy):
    r = decide(api, copy["proposal"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "DONE" and body["result"]["paypal_due_cents"] == 140000
    db.expire_all()
    inv = db.get(Invoice, copy["invoice"])
    assert inv.total_cents == 140000 and inv.ledger_status == "OPEN"
    assert inv.paypal_due_cents == 140000 and inv.mismatch is None
    adj = db.scalars(select(InvoiceLine).where(InvoiceLine.invoice_id == inv.id,
                                               InvoiceLine.amount_cents < 0)).one()  # fmt: skip
    assert adj.amount_cents == -2000 and adj.memo_line_id is None and "S-120" in adj.name
    (line,) = paypal_adj_lines(fake, copy["paypal_id"])
    assert line["unit_amount"]["value"] == "-20.00"
    assert fake.invoices[copy["paypal_id"]]["due_amount"]["value"] == "1400.00"
    prop = db.get(Proposal, copy["proposal"])
    assert fake.request_ids("put") == [f"test-adj-{prop.uid}"]
    actors = dict(db.execute(select(AuditLog.action, AuditLog.actor)).all())
    assert actors["proposal.approved"] == "supplier" and actors["proposal.done"] == "paypal"


def test_repeat_approve_is_a_no_op(api, db, fake, copy):
    decide(api, copy["proposal"])
    puts = fake.ops().count("put")
    again = decide(api, copy["proposal"])
    assert again.status_code == 200 and again.json()["status"] == "DONE"
    assert fake.ops().count("put") == puts
    db.expire_all()
    assert db.get(Invoice, copy["invoice"]).total_cents == 140000
    assert len(paypal_adj_lines(fake, copy["paypal_id"])) == 1


def test_reject(api, db, fake, copy):
    r = decide(api, copy["proposal"], "REJECT")
    assert r.status_code == 200 and r.json()["status"] == "REJECTED"
    assert "put" not in fake.ops()
    again = decide(api, copy["proposal"])
    assert again.status_code == 409 and again.json()["error"]["code"] == "NOT_PENDING"
    db.expire_all()
    assert db.get(Invoice, copy["invoice"]).total_cents == 142000


def test_paypal_failure_is_failed_then_a_retry_succeeds_once(api, db, fake, copy):
    fake.fail("put", error(400, "VALIDATION_ERROR"))
    r = decide(api, copy["proposal"])
    assert r.status_code == 200 and r.json()["status"] == "FAILED"
    assert r.json()["result"]["error"] == "VALIDATION_ERROR"
    db.expire_all()
    assert db.get(Invoice, copy["invoice"]).total_cents == 140000  # ours applied once
    retry = decide(api, copy["proposal"])
    assert retry.json()["status"] == "DONE" and retry.json()["result"]["attempts"] == 2
    db.expire_all()
    assert db.get(Invoice, copy["invoice"]).total_cents == 140000  # not applied twice
    assert db.scalar(select(func.count()).select_from(InvoiceLine).where(
        InvoiceLine.amount_cents < 0)) == 1  # fmt: skip
    assert len(paypal_adj_lines(fake, copy["paypal_id"])) == 1


def test_retry_after_a_lost_put_answer_doesnt_add_the_line_twice(api, db, fake, copy):
    prop_uid = str(db.get(Proposal, copy["proposal"]).uid)
    fake.fail("put", error(400, "VALIDATION_ERROR"))
    decide(api, copy["proposal"])  # FAILED
    # …but say PayPal had applied it (answer lost): the line is already there
    inv = fake.invoices[copy["paypal_id"]]
    inv["items"].append({"name": "Adjustment S-120", "quantity": "1",
                         "description": f"x {pinv.adjustment_tag(prop_uid)}",
                         "unit_amount": {"currency_code": "USD", "value": "-20.00"}})  # fmt: skip
    puts = fake.ops().count("put")
    assert decide(api, copy["proposal"]).json()["status"] == "DONE"
    assert fake.ops().count("put") == puts  # found the tag → no second PUT


def test_needs_credit_when_the_invoice_is_already_paid(api, db, fake, copy):
    inv = db.get(Invoice, copy["invoice"])
    inv.paid_cents = inv.total_cents  # the judge paid the full $8,420 (P2 D20)
    inv.ledger_status = "PAID"
    db.commit()
    calls = list(fake.ops())
    r = decide(api, copy["proposal"])
    assert r.status_code == 409
    assert r.json()["error"] == {
        "code": "NEEDS_CREDIT",
        "message": "Invoice already paid — record it as credit instead",
    }
    assert fake.ops() == calls  # no PayPal call at all
    db.expire_all()
    assert db.get(Proposal, copy["proposal"]).status == "PENDING"
    assert db.get(Invoice, copy["invoice"]).total_cents == 142000


def test_other_kinds_are_approved_without_paypal(api, db, fake, copy):
    p = Proposal(buyer_id=copy["buyer_id"], kind="RETURN_CHECK", payload={"sku": "S-103"},
                 evidence={"source": "agent"})  # fmt: skip
    db.add(p)
    db.commit()
    calls = list(fake.ops())
    r = decide(api, p.id)
    assert r.json()["status"] == "APPROVED" and fake.ops() == calls


def test_paid_elsewhere_on_paypal_is_a_recorded_mismatch(api, db, fake, copy):
    fake.pay_elsewhere(copy["paypal_id"], 10000)  # PayPal knows a $100 payment we don't
    r = decide(api, copy["proposal"])
    assert r.json()["status"] == "DONE" and r.json()["result"]["paypal_due_cents"] == 130000
    db.expire_all()
    inv = db.get(Invoice, copy["invoice"])
    assert inv.total_cents - inv.paid_cents == 140000  # ours untouched (ledger rule)
    assert inv.mismatch == "PayPal due 130000 ≠ ours 140000 (cents)"


def test_demo_cookie_decides_its_own_copy(api, db, copy):
    """P3 A1: the visitor's demo cookie (set by /demo/start) may decide its own proposals."""
    assert api.cookies.get("mtc_demo")
    r = decide(api, copy["proposal"], headers={})
    assert r.status_code == 200 and r.json()["status"] == "DONE"


def test_admin_only(api, db, copy, settings):
    api.cookies.clear()  # no demo cookie: only the admin token works
    assert decide(api, copy["proposal"], headers={}).status_code == 401
    assert (
        decide(api, copy["proposal"], headers={"Authorization": "Bearer nope"}).status_code == 401
    )
    settings.admin_token = None
    assert decide(api, copy["proposal"]).status_code == 503
    assert decide(api, 999999, headers=AUTH).status_code == 503


def test_unknown_proposal_and_bad_input(api, db, copy):
    assert decide(api, 999999).json()["error"]["code"] == "NOT_FOUND"
    r = api.post(
        f"/api/supplier/proposals/{copy['proposal']}/decide",
        json={"decision": "APPROVE", "edits": {"amount_cents": -100}},
        headers=AUTH,
    )
    assert r.status_code == 422  # edits come with P3a
    assert api.post(f"/api/supplier/proposals/{copy['proposal']}/decide",
                    json={"decision": "MAYBE"}, headers=AUTH).status_code == 422  # fmt: skip


def test_adjustment_body_strips_computed_fields_and_is_idempotent():
    current = {"id": "X", "status": "UNPAID", "amount": {}, "due_amount": {}, "payments": {},
               "refunds": {}, "links": [], "gratuity": {}, "detail": {"invoice_number": "N"},
               "items": [{"name": "S-120", "description": "SKU S-120", "quantity": "1",
                          "unit_amount": {"currency_code": "USD", "value": "14.00"}}]}  # fmt: skip
    body = pinv.adjustment_body(current, proposal_uid="u1", sku="S-120", amount_cents=-2000,
                                reason="scratch")  # fmt: skip
    assert set(body) == {"id", "detail", "items"}
    assert body["items"][-1]["unit_amount"] == {"currency_code": "USD", "value": "-20.00"}
    assert pinv.adjustment_body({**current, "items": body["items"]}, proposal_uid="u1",
                                sku="S-120", amount_cents=-2000, reason="") is None  # fmt: skip
    with pytest.raises(ValueError):
        pinv.adjustment_body(current, proposal_uid="u1", sku="S-120", amount_cents=2000,
                             reason="")  # fmt: skip


def test_two_concurrent_approves_apply_the_deduction_once(api, db, fake, settings, copy):
    import asyncio

    from backend.db.session import SessionLocal
    from backend.services import proposals as svc

    fake.delay["put"] = 0.2
    pp = PayPal(settings, transport=fake.transport())

    async def both():
        return await asyncio.gather(*(svc.decide(SessionLocal, pp, copy["proposal"], "APPROVE")
                                      for _ in range(2)))  # fmt: skip

    results = asyncio.run(both())
    assert {r["status"] for r in results} == {"DONE"}
    db.expire_all()
    assert db.get(Invoice, copy["invoice"]).total_cents == 140000
    assert db.scalar(select(func.count()).select_from(InvoiceLine).where(
        InvoiceLine.amount_cents < 0)) == 1  # fmt: skip
    assert len(paypal_adj_lines(fake, copy["paypal_id"])) == 1
    assert fake.invoices[copy["paypal_id"]]["due_amount"]["value"] == "1400.00"
