"""POST /api/demo/start (FR-12; P1 D3, D8, D9, D16–D18; review R3, R4; plan G23, G30)."""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import openai
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import func, select

from backend.agent import llm
from backend.core.config import get_settings, load_settings
from backend.core.dates import days_overdue, today_ny
from backend.db.models import (
    AuditLog,
    Buyer,
    DemoRate,
    Invoice,
    LlmUsage,
    Memo,
    MemoLine,
    Message,
    Proposal,
)
from backend.main import app
from backend.paypal import client as ppclient
from backend.paypal.client import PayPal, get_paypal
from backend.services import demo
from tests.llm_fake import FakeLLM, triage
from tests.paypal_fake import FakePayPal, error

SEED_TRIAGE = triage("DEDUCTION_REQUEST", sku="S-120", amount_cents=2000, reason="scratch")

SANDBOX_BUYER = "sb-buyer@personal.example.com"
COOKIE_SECRET = "cookie-secret-should-never-leak"
IP_SECRET = "ip-secret-should-never-leak"


def make_settings(**kw):
    base = dict(
        _env_file=None,
        app_env="test",
        demo_mode=True,
        app_base_url="https://memo-to-cash-web.onrender.com",
        paypal_business_email="supplier@example.com",
        paypal_client_id="id",
        paypal_client_secret=SecretStr("paypal-secret"),
        sandbox_buyer_email=SANDBOX_BUYER,
        demo_cookie_secret=SecretStr(COOKIE_SECRET),
        ip_hash_secret=SecretStr(IP_SECRET),
    )
    return load_settings(**(base | kw))


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
    return make_settings()


@pytest.fixture
def fake_llm():
    return FakeLLM(triage=[SEED_TRIAGE])


@pytest.fixture
def client(db, fake, settings, fake_llm):
    """`db` makes these Postgres tests (schema + truncate); the app uses its own sessions."""
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[llm.get_job_client] = lambda: fake_llm
    app.dependency_overrides[get_paypal] = lambda: PayPal(settings, transport=fake.transport())
    with TestClient(app, base_url="https://testserver") as c:  # https so the Secure cookie sticks
        yield c
    app.dependency_overrides.clear()


def start(client, xff="1.2.3.4"):
    return client.post("/api/demo/start", headers={"X-Forwarded-For": xff} if xff else {})


def bucket_now():
    return demo.hour_bucket(datetime.now(UTC))


def put_rate(db, key, count):
    db.add(DemoRate(ip_hash=key, window_start=bucket_now(), count=count))
    db.commit()


# ---------- success ----------


def test_start_creates_a_ready_private_copy(client, db, settings):
    r = start(client)
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {"statement_token", "buyer_id", "ms"} and body["ms"] >= 0

    buyer = db.get(Buyer, body["buyer_id"])
    assert buyer.statement_token == body["statement_token"]
    assert buyer.is_demo and buyer.demo_ready and buyer.demo_session
    assert buyer.email == SANDBOX_BUYER  # R4
    assert buyer.name == "Northside Jewelers"

    invs = {
        i.erp_invoice_no: i for i in db.scalars(select(Invoice).where(Invoice.buyer_id == buyer.id))
    }
    assert sum(i.total_cents - i.paid_cents for i in invs.values()) == 842000
    assert days_overdue(invs["INV-1038"].due_on, today_ny()) == 18
    assert all(i.ledger_status == "OPEN" and i.paypal_invoice_id for i in invs.values())

    statuses = list(
        db.scalars(select(MemoLine.status).join(Memo).where(Memo.buyer_id == buyer.id))
    )  # Northside's own lines (phase M adds two buyers to the copy)
    assert (statuses.count("KEPT"), statuses.count("RETURNED"), statuses.count("OUT")) == (5, 5, 2)

    # P4 D13 (A3): the seed message went through the real triage → the agent's proposal
    msg = db.scalars(select(Message).where(Message.buyer_id == buyer.id)).one()
    assert msg.kind == "MESSAGE" and "S-120" in msg.text
    assert msg.ai_result["seeded"] is True
    assert msg.ai_result["triage"]["action"] == "ADJUSTMENT:INV-1051:-2000"
    prop = db.scalars(select(Proposal).where(Proposal.buyer_id == buyer.id)).one()
    assert prop.kind == "ADJUSTMENT" and prop.status == "PENDING"
    assert prop.payload["invoice_id"] == invs["INV-1051"].id
    assert prop.payload["amount_cents"] == -2000 and prop.payload["reason"]
    assert prop.payload["sku"] == "S-120"
    assert (
        prop.evidence["source"] == "agent" and prop.evidence["checks"] and prop.evidence["records"]
    )
    assert prop.source_message_id == msg.id
    created = db.scalars(select(AuditLog).where(AuditLog.action == "proposal.created")).one()
    assert created.actor == "agent"
    assert db.scalar(select(LlmUsage.calls).where(LlmUsage.scope == "global")) == 1

    cookie = r.cookies.get(demo.COOKIE_NAME)
    assert demo.read_demo_cookie(cookie, settings) == buyer.demo_session
    raw = r.headers["set-cookie"].lower()
    for attr in ("httponly", "secure", "samesite=lax", "path=/", "max-age=604800"):
        assert attr in raw


def test_two_starts_are_two_independent_copies(client, db):
    a, b = start(client).json(), start(client).json()
    assert a["buyer_id"] != b["buyer_id"] and a["statement_token"] != b["statement_token"]
    sessions = set(db.scalars(select(Buyer.demo_session)))
    assert len(sessions) == 2 and None not in sessions


# ---------- rate limits (R3: rows inserted directly) ----------


def test_16th_start_from_one_ip_is_limited(client, db, settings):
    put_rate(db, demo.ip_hash("1.2.3.4", settings), 15)
    r = start(client)
    assert r.status_code == 429 and r.json()["error"]["code"] == "RATE_LIMITED"
    assert db.scalar(select(func.count()).select_from(Buyer)) == 0
    assert start(client, xff="5.6.7.8").status_code == 200  # another network is fine


def test_global_hourly_cap(client, db):
    put_rate(db, "global", 60)
    r = start(client)
    assert r.status_code == 429 and r.json()["error"]["code"] == "DEMO_HOURLY_LIMIT"


def test_warning_at_80_percent_of_global(client, db, caplog):
    put_rate(db, "global", 47)
    with caplog.at_level(logging.WARNING, logger="demo"):
        assert start(client).status_code == 200
    assert "48 of 60" in caplog.text


def test_counts_use_first_forwarded_ip_and_only_on_success(client, db, settings):
    assert start(client, xff="1.2.3.4, 10.0.0.1").status_code == 200
    rows = dict(db.execute(select(DemoRate.ip_hash, DemoRate.count)).all())
    assert rows == {demo.ip_hash("1.2.3.4", settings): 1, "global": 1}
    assert IP_SECRET not in str(rows) and "1.2.3.4" not in str(rows)


def test_client_ip_rules():
    assert demo.client_ip("1.2.3.4, 10.0.0.1", "9.9.9.9") == "1.2.3.4"
    assert demo.client_ip(None, "9.9.9.9") == "9.9.9.9"
    assert demo.client_ip(" ", "9.9.9.9") == "9.9.9.9"


def test_hour_bucket_is_utc():
    ny = datetime(2026, 3, 8, 23, 47, tzinfo=UTC).astimezone(ZoneInfo("America/New_York"))
    assert demo.hour_bucket(ny) == datetime(2026, 3, 8, 23, 0, tzinfo=UTC)


def test_rate_upsert_is_atomic(db):
    b = bucket_now()
    assert [demo._bump(db, "k", b) for _ in range(3)] == [1, 2, 3]
    assert demo._bump(db, "k", b + timedelta(hours=1)) == 1


# ---------- failures leave nothing reachable ----------


def assert_nothing_reachable(r, db):
    assert demo.COOKIE_NAME not in r.cookies and "set-cookie" not in r.headers
    assert db.scalar(select(func.count()).where(Buyer.demo_ready.is_(True))) == 0
    assert db.scalar(select(func.count()).select_from(DemoRate)) == 0  # not counted
    assert db.scalar(select(func.count()).select_from(Proposal)) == 0  # triage discarded (D13)


def test_transient_paypal_failure_is_503(client, db, fake):
    fake.fail("create", *[error(503)] * 20)
    r = start(client)
    assert r.status_code == 503 and r.json()["error"]["code"] == "PAYPAL_UNAVAILABLE"
    assert "try again" in r.json()["error"]["message"]
    assert_nothing_reachable(r, db)


def test_deterministic_paypal_failure_is_502_and_logged(client, db, fake, caplog):
    fake.fail("create", error(400, "VALIDATION_ERROR"))
    with caplog.at_level(logging.ERROR, logger="demo"):
        r = start(client)
    assert r.status_code == 502
    err = r.json()["error"]
    assert err["code"] == "PAYPAL_REJECTED" and err["paypal_debug_id"] == "fake-debug"
    assert "fake-debug" in caplog.text
    assert_nothing_reachable(r, db)


def test_deadline_is_503(db, fake, settings, monkeypatch):
    monkeypatch.setattr(demo, "DEADLINE_S", 0.3)

    async def slow(request):
        await asyncio.sleep(1)
        return fake.handle(request)

    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_paypal] = lambda: PayPal(
        settings, transport=httpx.MockTransport(slow)
    )
    try:
        with TestClient(app, base_url="https://testserver") as c:
            r = start(c)
    finally:
        app.dependency_overrides.clear()
    assert r.status_code == 503 and r.json()["error"]["code"] == "PAYPAL_UNAVAILABLE"
    assert_nothing_reachable(r, db)


def test_missing_settings_is_503_with_names_only(db, fake):
    s = make_settings(sandbox_buyer_email=None, ip_hash_secret=None)
    app.dependency_overrides[get_settings] = lambda: s
    app.dependency_overrides[get_paypal] = lambda: PayPal(s, transport=fake.transport())
    try:
        with TestClient(app, base_url="https://testserver") as c:
            r = start(c)
    finally:
        app.dependency_overrides.clear()
    assert r.status_code == 503
    err = r.json()["error"]
    assert err["code"] == "NOT_CONFIGURED"
    assert "SANDBOX_BUYER_EMAIL" in err["message"] and "IP_HASH_SECRET" in err["message"]
    assert COOKIE_SECRET not in r.text and fake.calls == []


def test_demo_mode_off_is_404(db, fake):
    s = make_settings(demo_mode=False)
    app.dependency_overrides[get_settings] = lambda: s
    try:
        with TestClient(app, base_url="https://testserver") as c:
            r = start(c)
    finally:
        app.dependency_overrides.clear()
    assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND"


# ---------- cookie helper (G23) ----------


def test_read_demo_cookie_rejects_tampered_and_expired(settings, monkeypatch):
    good = demo.make_demo_cookie("sess-1", settings)
    assert demo.read_demo_cookie(good, settings) == "sess-1"
    assert demo.read_demo_cookie(good[:-2] + "xx", settings) is None
    assert demo.read_demo_cookie(None, settings) is None
    other = make_settings(demo_cookie_secret=SecretStr("another-secret"))
    assert demo.read_demo_cookie(good, other) is None
    monkeypatch.setattr(demo, "COOKIE_MAX_AGE", -1)
    assert demo.read_demo_cookie(good, settings) is None


# ---------- live triage at demo start (P4 D13, R2) ----------


@pytest.mark.parametrize(
    "outcome",
    [
        openai.APITimeoutError(request=None),  # timeout
        "not json",
        ("length", SEED_TRIAGE),
        triage("DEDUCTION_REQUEST", sku="S-120", amount_cents=20000),  # misread amount
        triage("OTHER"),
    ],
)
def test_seed_proposal_when_the_triage_isnt_the_expected_one(client, db, fake_llm, outcome):
    fake_llm.script["triage"] = [outcome]
    r = start(client)
    assert r.status_code == 200, r.text
    (prop,) = db.scalars(select(Proposal)).all()  # exactly one proposal per copy
    assert prop.evidence["source"] == "seed" and prop.payload["amount_cents"] == -2000
    seeded = db.scalars(select(AuditLog).where(AuditLog.action == "proposal.seeded")).one()
    assert seeded.actor == "system"


def test_over_the_global_llm_cap_uses_the_seed_without_a_call(client, db, fake_llm):
    db.add(LlmUsage(day=today_ny(), scope="global", calls=500))
    db.commit()
    assert start(client).status_code == 200
    assert fake_llm.calls == []
    assert db.scalars(select(Proposal)).one().evidence["source"] == "seed"


def test_two_starts_have_one_proposal_each(client, db):
    a, b = start(client, "1.1.1.1").json(), start(client, "2.2.2.2").json()
    for body in (a, b):
        props = db.scalars(select(Proposal).where(Proposal.buyer_id == body["buyer_id"])).all()
        assert [p.evidence["source"] for p in props] == ["agent"]


def test_triage_runs_alongside_the_mirror(db, fake, settings):
    """Total ≈ max(mirror, triage), not the sum (R2)."""

    async def slow(request):
        # Each invoice's `send` waits 0.8 s, so the mirror takes ≥ 0.8 s on any machine
        # (CI's local Postgres made the old per-request 0.08 s mirror finish in 285 ms).
        if request.url.path.endswith("/send"):
            await asyncio.sleep(0.8)
        return fake.handle(request)

    def run(delay):
        llm_fake = FakeLLM(triage=[SEED_TRIAGE], delay=delay)
        app.dependency_overrides[get_settings] = lambda: settings
        app.dependency_overrides[get_paypal] = lambda: PayPal(
            settings, transport=httpx.MockTransport(slow)
        )
        app.dependency_overrides[llm.get_job_client] = lambda: llm_fake
        try:
            with TestClient(app, base_url="https://testserver") as c:
                r = start(c, f"9.9.9.{int(delay * 10)}")
        finally:
            app.dependency_overrides.clear()
        assert r.status_code == 200, r.text
        return r.json()["ms"]

    mirror_only = run(0.0)
    with_triage = run(0.6)
    assert mirror_only >= 800, mirror_only  # by construction: the mirror covers the triage
    assert with_triage < mirror_only + 300, (mirror_only, with_triage)  # sequential: + 600


# ---------- phase M: three real buyers per copy (P3 §M, M2, M5, M7; PM-plan G9) ----------

NAMES = {"Northside Jewelers", "Harbor Gems", "Lakeside Fine Jewelry"}


def copy_buyers(db, session):
    return {b.name: b for b in db.scalars(select(Buyer).where(Buyer.demo_session == session))}


def test_start_makes_three_ready_buyers_all_on_paypal(client, db, fake):
    r = start(client)
    assert r.status_code == 200, r.text
    north = db.get(Buyer, r.json()["buyer_id"])
    assert north.name == "Northside Jewelers"  # the redirect still opens Northside
    buyers = copy_buyers(db, north.demo_session)
    assert set(buyers) == NAMES and all(b.is_demo and b.demo_ready for b in buyers.values())
    assert buyers["Harbor Gems"].email == "sb-buyer+harbor@personal.example.com"  # G1
    assert buyers["Lakeside Fine Jewelry"].email == "sb-buyer+lakeside@personal.example.com"
    invs = list(db.scalars(select(Invoice)))
    assert len(invs) == 7
    assert all(i.paypal_invoice_id and i.payer_view_url and i.ledger_status == "OPEN"
               for i in invs)  # fmt: skip
    assert fake.ops().count("create") == 7 and fake.ops().count("send") == 7
    owed = {b.name: sum(i.total_cents for i in invs if i.buyer_id == b.id)
            for b in buyers.values()}  # fmt: skip
    assert owed == {"Northside Jewelers": 842000, "Harbor Gems": 230000,
                    "Lakeside Fine Jewelry": 596000}  # fmt: skip
    # the seeded $20 request (and its message) stays on Northside only (M6)
    assert [p.buyer_id for p in db.scalars(select(Proposal))] == [north.id]
    assert [m.buyer_id for m in db.scalars(select(Message))] == [north.id]


def test_northside_failing_on_paypal_fails_the_start(client, db, fake):
    """Option 2 (M2): the start gates on Northside only — mirrored first, inside the request."""
    fake.fail("create", error(400, "VALIDATION_ERROR"))  # Northside's first create is refused
    r = start(client)
    assert r.status_code == 502
    assert_nothing_reachable(r, db)
    assert db.scalar(select(func.count()).select_from(Buyer)) == 3  # rows exist, none ready


def test_a_retried_start_gives_exactly_three_ready_buyers(client, db, fake):
    """G9 (a): a start that fails part-way leaves none of its buyers reachable; the retry is a
    fresh copy with exactly three ready buyers, and the failed copy's buyers stay 404."""
    fake.fail("send", *[error(503)] * 20)
    failed = start(client)
    assert failed.status_code == 503
    fake.faults.clear()  # PayPal is healthy again for the retry
    old_ids = list(db.scalars(select(Buyer.id)))
    ok = start(client, "5.6.7.8")
    assert ok.status_code == 200, ok.text
    db.expire_all()
    ready = list(db.scalars(select(Buyer).where(Buyer.demo_ready.is_(True))))
    assert {b.name for b in ready} == NAMES and len(ready) == 3
    assert not set(old_ids) & {b.id for b in ready}
    for old in old_ids:
        assert client.get(f"/api/supplier/buyers/{old}").status_code == 404


def test_resuming_a_copys_mirror_creates_no_duplicate_invoices(client, db, fake, settings):
    """G9 (b): Northside's sends failed after its creates (start 503, nobody reachable) → the
    resume adopts Northside's 3 PayPal invoices and creates the other 4 once; PayPal ends with
    exactly 7 invoices."""
    from backend.db.session import SessionLocal
    from backend.services.mirror import mirror_invoices

    fake.fail("send", *[error(503)] * 20)
    assert start(client).status_code == 503
    assert len(fake.invoices) == 3  # Northside only: the others go after a successful start
    fake.faults.clear()
    pp = PayPal(settings, transport=fake.transport())
    for b in db.scalars(select(Buyer)).all():
        res = asyncio.run(mirror_invoices(b.id, pp, settings, SessionLocal))
        assert res.ok, res.errors
    assert len(fake.invoices) == 7 and fake.ops().count("create") == 7
