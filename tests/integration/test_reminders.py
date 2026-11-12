# ruff: noqa: F811 — the fixtures are shared from test_supplier_api
"""Job C reminders (FR-10; P3b B6, F9, F10, G11; plan step 2). The fake LLM drafts (never a real
model in tests); the fake PayPal answers remind like the sandbox did (empty 2xx, no limit of its
own — CLAUDE §4), so our 2-a-day count is what's tested."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select

from backend.agent import llm
from backend.core.dates import today_ny
from backend.db.models import Buyer, Invoice, Proposal, ReminderSent
from backend.db.session import SessionLocal
from backend.main import app
from backend.services import reminders
from backend.services.demo import build_demo_csv
from backend.services.importer import import_csv
from tests.integration.test_demo import SEED_TRIAGE
from tests.integration.test_supplier_api import (  # noqa: F401 — fixtures
    AUTH,
    clients,
    fake,
    no_backoff,
    settings,
    start,
)
from tests.llm_fake import FakeLLM
from tests.paypal_fake import error

NY = ZoneInfo("America/New_York")
GOOD = {"text": "Hello Northside Jewelers, a friendly reminder that INV-1038 ($3,500.00) is 18 "
                "days past due. If you've already paid, thank you.",
        "send_after": None}  # fmt: skip


def draft_for(text=None, send_after=None):
    return {"text": text or GOOD["text"], "send_after": send_after or today_ny().isoformat()}


@pytest.fixture
def writer():
    """The reminder-writing fake LLM, swapped in for the app's job client."""
    w = FakeLLM(triage=[SEED_TRIAGE], reminder=[draft_for()])
    app.dependency_overrides[llm.get_job_client] = lambda: w
    return w


def invoice_id(a, name, invoice_no):
    rows = a.get("/api/supplier/overview").json()["buyers"]
    bid = next(b["id"] for b in rows if b["name"] == name)
    d = a.get(f"/api/supplier/buyers/{bid}").json()
    return next(i["id"] for i in d["invoices"] if i["invoice_no"] == invoice_no)


def draft(a, iid):
    return a.post(f"/api/supplier/invoices/{iid}/reminder")


def test_draft_a_reminder_for_a_past_due_invoice(clients, db, writer):
    a, _ = clients
    start(a, "9.1.1.1")
    r = draft(a, invoice_id(a, "Northside Jewelers", "INV-1038"))
    assert r.status_code == 200, r.text
    card = r.json()
    assert card["kind"] == "REMINDER" and card["status"] == "PENDING"
    p = db.get(Proposal, card["id"])
    assert p.payload["text"] == GOOD["text"] and p.payload["send_after"] == today_ny().isoformat()
    assert (
        p.evidence["source"] == "agent" and "INV-1038 is 18 days past due" in p.evidence["checks"]
    )
    assert writer.jobs()[-1] == "reminder"
    call = writer.calls[-1]
    assert call["messages"][1]["content"].startswith("Draft a reminder for INV-1038.")


@pytest.mark.parametrize("bad", [
    "Hello Northside Jewelers, INV-1038 ($3,600.00) is 18 days past due.",  # wrong amount
    "Hello Northside Jewelers, INV-1038 ($3,500.00) is 40 days past due.",  # wrong day count
    "Hello Northside Jewelers, invoice:INV-1038 is past due.",  # record id
    "Hello Northside Jewelers, your invoice is past due.",  # doesn't name the invoice
])  # fmt: skip
def test_a_draft_that_states_anything_not_in_the_records_creates_nothing(clients, db, bad):
    a, _ = clients
    start(a, "9.1.1.2")
    w = FakeLLM(triage=[SEED_TRIAGE], reminder=[draft_for(bad)])
    app.dependency_overrides[llm.get_job_client] = lambda: w
    r = draft(a, invoice_id(a, "Northside Jewelers", "INV-1038"))
    assert r.status_code == 503 and r.json()["error"]["code"] == "DRAFT_FAILED"
    assert db.scalar(select(func.count()).where(Proposal.kind == "REMINDER")) == 0


def test_send_after_in_the_past_is_rejected(clients, db):
    a, _ = clients
    start(a, "9.1.1.3")
    old = (today_ny() - timedelta(days=1)).isoformat()
    w = FakeLLM(triage=[SEED_TRIAGE], reminder=[draft_for(send_after=old)])
    app.dependency_overrides[llm.get_job_client] = lambda: w
    assert draft(a, invoice_id(a, "Northside Jewelers", "INV-1038")).status_code == 503


def test_one_pending_reminder_per_invoice(clients, db, writer):
    a, _ = clients
    start(a, "9.1.1.4")
    iid = invoice_id(a, "Northside Jewelers", "INV-1038")
    assert draft(a, iid).status_code == 200
    calls = len(writer.calls)
    r = draft(a, iid)
    assert r.status_code == 409 and r.json()["error"]["code"] == "REMINDER_PENDING"
    assert len(writer.calls) == calls  # no model call for a refused draft (F9)


def test_not_past_due_or_promised_is_refused(clients, db, writer):
    a, _ = clients
    start(a, "9.1.1.5")
    r = draft(a, invoice_id(a, "Harbor Gems", "INV-1055"))  # Harbor: nothing past due (F10)
    assert r.status_code == 422 and r.json()["error"]["code"] == "NOT_PAST_DUE"
    iid = invoice_id(a, "Lakeside Fine Jewelry", "INV-1040")
    buyer_id = db.get(Invoice, iid).buyer_id
    db.add(Proposal(buyer_id=buyer_id, kind="PROMISE_NOTE", status="PENDING",
                    payload={"promise_date": (today_ny() + timedelta(days=3)).isoformat()},
                    evidence={"source": "agent"}))  # fmt: skip
    db.commit()
    r = draft(a, iid)
    assert r.status_code == 422 and r.json()["error"]["code"] == "PROMISE_ACTIVE"


def approve(a, proposal_id, **body):
    return a.post(f"/api/supplier/proposals/{proposal_id}/decide",
                  json={"decision": "APPROVE", **body})  # fmt: skip


def test_approve_sends_one_reminder_on_paypal(clients, db, fake, writer):
    a, _ = clients
    start(a, "9.1.1.6")
    iid = invoice_id(a, "Northside Jewelers", "INV-1038")
    pid = draft(a, iid).json()["id"]
    r = approve(a, pid, edits={"text": "Edited: INV-1038 ($3,500.00) is 18 days past due."})
    assert r.status_code == 200 and r.json()["status"] == "DONE", r.text
    inv = db.get(Invoice, iid)
    day = today_ny().strftime("%Y%m%d")
    assert fake.request_ids("remind") == [f"test-rem-{inv.uid}-{day}-1"]
    (req,) = [q for q in fake.requests if fake.classify(q) and fake.classify(q)[0] == "remind"]
    sent = json.loads(req.content)
    assert sent["note"].startswith("Edited: INV-1038") and sent["send_to_recipient"] is True
    assert db.scalar(select(func.count()).select_from(ReminderSent)) == 1
    assert approve(a, pid).json()["status"] == "DONE"  # repeat: no-op
    assert len(fake.request_ids("remind")) == 1


def test_a_third_reminder_in_one_new_york_day_is_refused(clients, db, fake, writer):
    a, _ = clients
    start(a, "9.1.1.7")
    iid = invoice_id(a, "Northside Jewelers", "INV-1038")
    now = datetime.now(UTC)
    yesterday_late = datetime.combine(today_ny() - timedelta(days=1), datetime.min.time(),
                                      NY).replace(hour=23, minute=59)  # fmt: skip
    db.add_all([ReminderSent(invoice_id=iid, sent_at=yesterday_late),
                ReminderSent(invoice_id=iid, sent_at=now)])  # fmt: skip
    db.commit()
    pid = draft(a, iid).json()["id"]
    assert approve(a, pid).json()["status"] == "DONE"  # yesterday's doesn't count → 2nd today
    assert fake.request_ids("remind")[-1].endswith("-2")
    p2 = draft(a, iid).json()["id"]
    r = approve(a, p2)
    assert r.status_code == 409 and r.json()["error"]["code"] == "REMIND_LIMIT"
    assert len(fake.request_ids("remind")) == 1
    assert db.get(Proposal, p2).status == "PENDING"  # still decidable tomorrow


def test_a_failed_send_is_retried_with_the_same_request_id(clients, db, fake, writer):
    a, _ = clients
    start(a, "9.1.1.8")
    pid = draft(a, invoice_id(a, "Northside Jewelers", "INV-1038")).json()["id"]
    fake.fail("remind", error(400, "INVALID_REQUEST"))
    assert approve(a, pid).json()["status"] == "FAILED"
    assert db.scalar(select(func.count()).select_from(ReminderSent)) == 0
    assert approve(a, pid).json()["status"] == "DONE"
    first, second = fake.request_ids("remind")
    assert first == second


def test_reminders_are_scoped_to_the_visitors_copy(clients, db, writer):
    a, b = clients
    start(a, "9.1.1.9")
    start(b, "9.1.2.0")
    theirs = invoice_id(b, "Northside Jewelers", "INV-1038")
    assert draft(a, theirs).status_code == 404
    pid = draft(b, theirs).json()["id"]
    assert approve(a, pid).status_code == 404
    a.cookies.clear()
    assert draft(a, theirs).status_code == 401


def test_daily_run_drafts_for_real_buyers_only(clients, db, settings):
    a, _ = clients
    start(a, "9.1.2.1")  # a demo copy: never in the daily run (B6)
    res = import_csv(db, build_demo_csv(today_ny(), "real@example.com"))  # real buyers
    for inv in db.scalars(select(Invoice).where(Invoice.buyer_id.in_(res.buyer_ids))):
        inv.ledger_status = "OPEN"
    lakeside = db.scalars(select(Buyer).where(Buyer.name == "Lakeside Fine Jewelry",
                                              Buyer.is_demo.is_(False))).one()  # fmt: skip
    db.add(Proposal(buyer_id=lakeside.id, kind="PROMISE_NOTE", status="DONE",
                    payload={"promise_date": (today_ny() + timedelta(days=2)).isoformat()},
                    evidence={"source": "agent"}))  # fmt: skip
    db.commit()
    w = FakeLLM(reminder=[draft_for()])
    made = asyncio.run(reminders.daily_run(SessionLocal, client=w))
    assert made == 1  # Northside's INV-1038 only: Lakeside promised, Harbor not past due
    (p,) = db.scalars(select(Proposal).where(Proposal.kind == "REMINDER")).all()
    assert db.get(Buyer, p.buyer_id).is_demo is False


def test_buyer_page_shows_where_a_reminder_can_be_drafted(clients, db, writer):
    a, _ = clients
    start(a, "9.1.2.2")
    rows = a.get("/api/supplier/overview").json()["buyers"]
    north = next(b["id"] for b in rows if b["name"] == "Northside Jewelers")

    def state():
        return {
            i["invoice_no"]: i["reminder"]
            for i in a.get(f"/api/supplier/buyers/{north}").json()["invoices"]
        }

    assert state() == {"INV-1038": "AVAILABLE", "INV-1042": None, "INV-1051": None}
    card = draft(a, invoice_id(a, "Northside Jewelers", "INV-1038")).json()
    assert card["summary"] == "Payment reminder for INV-1038" and card["invoice_no"] == "INV-1038"
    assert state()["INV-1038"] == "PENDING"


def test_one_day_past_due_reads_singular(clients, db):
    a, _ = clients
    start(a, "9.1.2.3")
    text = (
        "Hello Lakeside Fine Jewelry, a friendly reminder that INV-1040 ($3,500.00) is 1 day "
        "past due."
    )
    w = FakeLLM(triage=[SEED_TRIAGE], reminder=[draft_for(text)])
    app.dependency_overrides[llm.get_job_client] = lambda: w
    card = draft(a, invoice_id(a, "Lakeside Fine Jewelry", "INV-1040")).json()
    assert "INV-1040 is 1 day past due" in card["checks"]  # not "1 days"
