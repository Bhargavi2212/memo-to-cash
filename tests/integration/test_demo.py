"""POST /api/demo/start (FR-12; P1 D3, D8, D9, D16–D18; review R3, R4; plan G23, G30)."""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import func, select

from backend.core.config import get_settings, load_settings
from backend.core.dates import days_overdue, today_ny
from backend.db.models import AuditLog, Buyer, DemoRate, Invoice, MemoLine, Message, Proposal
from backend.main import app
from backend.paypal import client as ppclient
from backend.paypal.client import PayPal, get_paypal
from backend.services import demo
from tests.paypal_fake import FakePayPal, error

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
def client(db, fake, settings):
    """`db` makes these Postgres tests (schema + truncate); the app uses its own sessions."""
    app.dependency_overrides[get_settings] = lambda: settings
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

    statuses = list(db.scalars(select(MemoLine.status)))
    assert (statuses.count("KEPT"), statuses.count("RETURNED"), statuses.count("OUT")) == (5, 5, 2)

    msg = db.scalars(select(Message).where(Message.buyer_id == buyer.id)).one()
    assert msg.kind == "MESSAGE" and msg.ai_result == {"seeded": True} and "S-120" in msg.text
    prop = db.scalars(select(Proposal).where(Proposal.buyer_id == buyer.id)).one()
    assert prop.kind == "ADJUSTMENT" and prop.status == "PENDING"
    assert prop.payload["invoice_id"] == invs["INV-1051"].id
    assert prop.payload["amount_cents"] == -2000 and prop.payload["reason"]
    assert (
        prop.evidence["source"] == "seed" and prop.evidence["checks"] and prop.evidence["records"]
    )
    assert prop.source_message_id == msg.id
    seeded = db.scalars(select(AuditLog).where(AuditLog.action == "proposal.seeded")).one()
    assert seeded.actor == "system"

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
