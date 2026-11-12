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


# ---------- P3a a2: modes, credit, edits, replies (A6–A8, A11, R1, R5, G4, G5, G7, G23) ----------


def act(api, pid, decision="APPROVE", headers=AUTH, **body):
    return api.post(f"/api/supplier/proposals/{pid}/decide",
                    json={"decision": decision, **body}, headers=headers)  # fmt: skip


def pay_in_full(db, invoice_id):
    """Simulate the judge paying the full $8,420 between page load and the click (P2 D20)."""
    inv = db.get(Invoice, invoice_id)
    inv.paid_cents = inv.total_cents
    inv.ledger_status = "PAID"
    db.commit()


def statement(api, db, copy):
    from backend.db.models import Buyer

    token = db.get(Buyer, copy["buyer_id"]).statement_token
    return api.get(f"/api/statement/{token}").json()


def test_no_mode_on_a_paid_invoice_becomes_a_credit(api, db, fake, copy):
    """G4: the mode is decided under the lock — a payment since page load never gives a 409."""
    card = api.get("/api/supplier/proposals", headers=AUTH)  # admin lists skip demo copies…
    assert card.json() == []
    cards = api.get("/api/supplier/proposals").json()  # …the visitor's cookie sees it
    assert cards[0]["suggested"] == "APPLY"  # at page load
    pay_in_full(db, copy["invoice"])
    calls = list(fake.ops())
    r = act(api, copy["proposal"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "DONE" and body["payload"]["mode"] == "CREDIT"
    assert body["payload"]["credit_cents"] == 2000 and body["payload"]["amount_cents"] == -2000
    assert body["payload"]["original"]["amount_cents"] == -2000
    assert fake.ops() == calls  # no PayPal call
    db.expire_all()
    assert db.get(Invoice, copy["invoice"]).total_cents == 142000  # invoice untouched
    s = statement(api, db, copy)
    assert s["credit_cents"] == 2000
    (req,) = [q for q in s["requests"] if q["id"] == copy["proposal"]]
    assert req["summary"] == "Approved as $20.00 credit on your account"
    assert req["status"] == "APPROVED"
    actions = [a for (a,) in db.execute(select(AuditLog.action))]
    assert "proposal.credited" in actions


def test_agent_facts_see_the_same_credit_as_the_statement(api, db, copy):
    """G5: one credit helper — the agent can't quote a different credit than the page."""
    from backend.agent.facts import facts_for_buyer
    from backend.core.dates import today_ny

    pay_in_full(db, copy["invoice"])
    act(api, copy["proposal"])
    db.expire_all()
    f = facts_for_buyer(db, copy["buyer_id"], today_ny())
    assert f.credit_cents == statement(api, db, copy)["credit_cents"] == 2000


def test_explicit_apply_on_a_paid_invoice_is_409(api, db, fake, copy):
    pay_in_full(db, copy["invoice"])
    calls = list(fake.ops())
    r = act(api, copy["proposal"], mode="APPLY")
    assert r.status_code == 409
    msg = "Invoice already paid — record it as credit instead"
    assert r.json()["error"] == {"code": "NEEDS_CREDIT", "message": msg}
    assert fake.ops() == calls
    db.expire_all()
    assert db.get(Proposal, copy["proposal"]).status == "PENDING"


def test_explicit_credit_even_when_due_is_enough(api, db, fake, copy):
    """G23: a goodwill credit leaves the invoice alone."""
    calls = list(fake.ops())
    r = act(api, copy["proposal"], mode="CREDIT")
    assert r.json()["status"] == "DONE" and r.json()["payload"]["credit_cents"] == 2000
    assert fake.ops() == calls
    db.expire_all()
    assert db.get(Invoice, copy["invoice"]).total_cents == 142000


def test_split_is_not_yet(api, db, copy):
    r = act(api, copy["proposal"], mode="SPLIT")
    assert r.status_code == 422 and r.json()["error"]["code"] == "NOT_YET"


def test_an_edited_amount_is_the_amount_everywhere(api, db, fake, copy):
    """R1: ledger line, PayPal line and the buyer's summary all show the edited $15.00."""
    r = act(api, copy["proposal"], edits={"amount_cents": 1500, "reason": "small scratch"})
    assert r.status_code == 200 and r.json()["status"] == "DONE", r.text
    pl = r.json()["payload"]
    assert pl["amount_cents"] == -1500 and pl["original"]["amount_cents"] == -2000
    (line,) = paypal_adj_lines(fake, copy["paypal_id"])
    assert line["unit_amount"]["value"] == "-15.00"
    db.expire_all()
    assert db.get(Invoice, copy["invoice"]).total_cents == 140500
    (req,) = [q for q in statement(api, db, copy)["requests"] if q["id"] == copy["proposal"]]
    assert req["summary"] == "Approved — $15.00 off INV-1051"


@pytest.mark.parametrize(
    "edits",
    [{"amount_cents": 0}, {"amount_cents": -100}, {"amount_cents": 150000},
     {"amount_cents": "15"}, {"invoice_id": 1}],
)  # fmt: skip
def test_bad_edits_are_422(api, db, copy, edits):
    r = act(api, copy["proposal"], edits=edits)
    assert r.status_code == 422 and r.json()["error"]["code"] == "BAD_EDIT"
    db.expire_all()
    assert db.get(Proposal, copy["proposal"]).status == "PENDING"


def test_retry_after_a_failed_put_sends_the_payload_amount(api, db, fake, copy):
    fake.fail("put", error(400, "VALIDATION_ERROR"))
    r = act(api, copy["proposal"], edits={"amount_cents": 1500})
    assert r.json()["status"] == "FAILED"
    retry = act(api, copy["proposal"])
    assert retry.json()["status"] == "DONE"
    res = retry.json()["result"]
    assert res["attempts"] == 2 and res["error"] is None  # merged, not replaced
    (line,) = paypal_adj_lines(fake, copy["paypal_id"])
    assert line["unit_amount"]["value"] == "-15.00"


def test_a_failed_adjustment_can_only_be_retried(api, db, fake, copy):
    """G7: the ledger already changed — no edit, no reject."""
    fake.fail("put", error(400, "VALIDATION_ERROR"))
    act(api, copy["proposal"])
    for r in (act(api, copy["proposal"], edits={"amount_cents": 500}),
              act(api, copy["proposal"], "REJECT")):  # fmt: skip
        assert r.status_code == 409 and r.json()["error"]["code"] == "NOT_PENDING"


def add_proposal(db, copy, kind, payload):
    p = Proposal(buyer_id=copy["buyer_id"], kind=kind, payload=payload,
                 evidence={"source": "agent"})  # fmt: skip
    db.add(p)
    db.commit()
    return p.id


def test_reply_text_reaches_the_buyer(api, db, fake, copy):
    pid = add_proposal(db, copy, "REPLY", {"reason": "Demo Diamond Co. will write back"})
    calls = list(fake.ops())
    r = act(api, pid, edits={"text": "We'll call you this afternoon."})
    assert r.json()["status"] == "DONE" and fake.ops() == calls
    (req,) = [q for q in statement(api, db, copy)["requests"] if q["id"] == pid]
    assert req["status"] == "ANSWERED" and req["reply"] == "We'll call you this afternoon."


def test_mark_as_seen_and_reject_with_a_reply(api, db, copy):
    seen = add_proposal(db, copy, "REPLY", {"reason": "x"})
    assert act(api, seen).json()["status"] == "DONE"  # G21: no text = "has seen this"
    r = act(api, copy["proposal"], "REJECT", edits={"text": "The scratch was on delivery."})
    assert r.json()["status"] == "REJECTED"
    (req,) = [q for q in statement(api, db, copy)["requests"] if q["id"] == copy["proposal"]]
    assert req["status"] == "DECLINED" and req["reply"] == "The scratch was on delivery."


def test_return_check_needs_a_reply_in_p3a(api, db, fake, copy):
    pid = add_proposal(db, copy, "RETURN_CHECK", {"sku": "S-103"})
    calls = list(fake.ops())
    r = act(api, pid)
    assert r.status_code == 422 and r.json()["error"]["code"] == "TEXT_REQUIRED"
    r = act(api, pid, edits={"text": "We'll check our returns log."})
    assert r.json()["status"] == "DONE" and fake.ops() == calls  # no line or money change


def test_promise_note_approves_and_date_edits_wait(api, db, copy):
    pid = add_proposal(db, copy, "PROMISE_NOTE", {"promise_date": "2026-11-13"})
    r = act(api, pid, edits={"promise_date": "2026-11-20"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "NOT_YET"
    assert act(api, pid).json()["status"] == "DONE"


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
