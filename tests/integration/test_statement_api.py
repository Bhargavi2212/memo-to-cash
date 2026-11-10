"""Statement read model + preview + config + token-safe logs (FR-3, FR-4; P2 D2, D3, D12, D14).

Payments, notices and credit are tested in step 8 (test_payments_flow / test_scoping),
once payments exist (plan G22).
"""

import logging

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import event, select

from backend.core.config import get_settings, load_settings
from backend.core.dates import offset, today_ny
from backend.core.logging import RedactTokenFilter, redact
from backend.db.models import Buyer, Invoice
from backend.db.session import engine
from backend.main import app
from backend.services import statement as svc
from backend.services.demo import build_csv
from backend.services.importer import import_csv

D = today_ny()


def make_settings(**kw):
    base = dict(
        _env_file=None,
        app_env="test",
        demo_mode=True,
        paypal_client_id="public-client-id",
        sandbox_buyer_email="sb-buyer@personal.example.com",
        sandbox_buyer_password=SecretStr("sandbox-pass"),
    )
    return load_settings(**(base | kw))


@pytest.fixture
def settings():
    return make_settings()


@pytest.fixture
def client(db, settings):
    app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def seed(db, *, demo_session=None, ready=True):
    res = import_csv(db, build_csv(D, "buyer@example.com"), demo_session=demo_session)
    assert res.ok
    b = db.get(Buyer, res.buyer_ids[0])
    b.demo_ready = ready
    db.commit()
    return b


# ---------- statement ----------


def test_statement_from_the_seed(client, db):
    b = seed(db)
    r = client.get(f"/api/statement/{b.statement_token}")
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["buyer"] == {"name": "Northside Jewelers"}
    assert s["supplier"] == {"name": "Demo Diamond Co."}
    assert (s["owed_cents"], s["past_due_cents"], s["oldest_age_days"]) == (842000, 350000, 48)
    assert s["payments"] == [] and s["notices"] == [] and s["credit_cents"] == 0
    assert s["pending_proposals"] == 0

    lines = {ln["sku"]: ln for m in s["memos"] for ln in m["lines"]}
    statuses = [ln["status"] for ln in lines.values()]
    assert (statuses.count("KEPT"), statuses.count("RETURNED"), statuses.count("OUT")) == (5, 5, 2)
    assert lines["S-101"]["invoice_no"] == "INV-1038" and lines["S-120"]["invoice_no"] == "INV-1051"
    assert lines["S-102"]["return_ref"] == "R-311"
    assert lines["S-102"]["returned_on"] == offset(D, -38).isoformat()
    assert lines["S-103"]["due_back_on"] == offset(D, 11).isoformat()
    assert lines["S-102"]["invoice_no"] is None and lines["S-102"]["due_back_on"] is None
    assert [m["memo_no"] for m in s["memos"]] == ["M-201", "M-207", "M-212"]

    invs = {i["invoice_no"]: i for i in s["invoices"]}
    assert [i["invoice_no"] for i in s["invoices"]] == ["INV-1038", "INV-1042", "INV-1051"]
    assert invs["INV-1038"]["days_overdue"] == 18 and invs["INV-1042"]["days_overdue"] == 0
    assert invs["INV-1042"]["memo_no"] == "M-207" and invs["INV-1042"]["due_cents"] == 350000
    assert all(isinstance(i["id"], int) for i in s["invoices"])
    assert s["demo_login"] is None  # real buyer


def test_paid_invoice_listed_but_not_owed(client, db):
    b = seed(db)
    inv = db.scalars(select(Invoice).where(Invoice.erp_invoice_no == "INV-1038")).one()
    inv.paid_cents = inv.total_cents
    inv.ledger_status = "PAID"
    db.commit()
    s = client.get(f"/api/statement/{b.statement_token}").json()
    assert s["owed_cents"] == 492000 and s["past_due_cents"] == 0 and s["oldest_age_days"] == 20
    paid = next(i for i in s["invoices"] if i["invoice_no"] == "INV-1038")
    assert paid["due_cents"] == 0 and paid["days_overdue"] == 0 and paid["ledger_status"] == "PAID"


def test_unknown_token_404(client, db):
    r = client.get("/api/statement/not-a-real-token")
    assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND"


def test_demo_copy_needs_ready_and_shows_login(client, db):
    b = seed(db, demo_session="s1", ready=False)
    assert client.get(f"/api/statement/{b.statement_token}").status_code == 404
    b.demo_ready = True
    db.commit()
    s = client.get(f"/api/statement/{b.statement_token}").json()
    assert s["demo_login"] == {"email": "sb-buyer@personal.example.com", "password": "sandbox-pass"}


def test_no_demo_login_when_demo_mode_off(db):
    b = seed(db, demo_session="s1")
    s = make_settings(demo_mode=False)
    app.dependency_overrides[get_settings] = lambda: s
    try:
        with TestClient(app) as c:
            body = c.get(f"/api/statement/{b.statement_token}").json()
    finally:
        app.dependency_overrides.clear()
    assert body["demo_login"] is None


def test_statement_uses_one_query_per_table(db, settings):
    b = seed(db)
    n = [0]

    def count(*_a, **_k):
        n[0] += 1

    event.listen(engine, "before_cursor_execute", count)
    try:
        svc.get_statement(db, b.statement_token, D, settings)
    finally:
        event.remove(engine, "before_cursor_execute", count)
    assert n[0] <= 8, n[0]


# ---------- preview (BACKEND_SCHEMA §8 defaults, D3) ----------


def ids(db):
    return {i.erp_invoice_no: i.id for i in db.scalars(select(Invoice))}


def test_preview_defaults(client, db):
    b = seed(db)
    url = f"/api/statement/{b.statement_token}/preview"
    i = ids(db)

    full = client.post(url, json={}).json()
    assert full["total_cents"] == 842000 and len(full["allocations"]) == 3

    only = client.post(url, json={"invoice_ids": [i["INV-1051"]]}).json()
    assert only["total_cents"] == 142000
    assert only["allocations"] == [
        {"invoice_id": i["INV-1051"], "invoice_no": "INV-1051", "amount_cents": 142000}
    ]

    demo = client.post(url, json={"amount_cents": 500000}).json()
    assert [(a["invoice_no"], a["amount_cents"]) for a in demo["allocations"]] == [
        ("INV-1038", 350000),
        ("INV-1042", 150000),
    ]
    assert demo["remaining_after"] == [
        {"invoice_no": "INV-1038", "due_cents": 0},
        {"invoice_no": "INV-1042", "due_cents": 200000},
    ]

    both = client.post(url, json={"invoice_ids": [i["INV-1042"]], "amount_cents": 1000}).json()
    assert both["allocations"][0]["amount_cents"] == 1000


@pytest.mark.parametrize(
    ("body", "code"),
    [
        ({"amount_cents": 0}, "AMOUNT_NOT_POSITIVE"),
        ({"amount_cents": -5}, "AMOUNT_NOT_POSITIVE"),
        ({"amount_cents": 842001}, "AMOUNT_EXCEEDS_SELECTED"),
        ({"invoice_ids": []}, "NOTHING_SELECTED"),
        ({"invoice_ids": [999999]}, "INVOICE_NOT_OPEN"),
    ],
)
def test_preview_errors(client, db, body, code):
    b = seed(db)
    r = client.post(f"/api/statement/{b.statement_token}/preview", json=body)
    assert r.status_code == 422 and r.json()["error"]["code"] == code
    assert r.json()["error"]["message"]


def test_preview_rejects_float_amounts(client, db):
    b = seed(db)
    r = client.post(f"/api/statement/{b.statement_token}/preview", json={"amount_cents": 5000.5})
    assert r.status_code == 422


def test_preview_cannot_use_another_buyers_invoice(client, db):
    mine = seed(db, demo_session="mine")
    other = seed(db, demo_session="other")
    theirs = db.scalars(select(Invoice).where(Invoice.buyer_id == other.id)).first()
    r = client.post(
        f"/api/statement/{mine.statement_token}/preview", json={"invoice_ids": [theirs.id]}
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "INVOICE_NOT_OPEN"


def test_preview_unknown_token_404(client, db):
    assert client.post("/api/statement/nope/preview", json={}).status_code == 404


# ---------- config + logs (D12, D14) ----------


def test_public_config(client, db):
    assert client.get("/api/config").json() == {
        "paypal_client_id": "public-client-id",
        "demo_mode": True,
    }


def test_access_log_never_shows_the_token(caplog):
    token = "abcDEF123-secretish_token"
    logger = logging.getLogger("uvicorn.access")
    assert any(isinstance(f, RedactTokenFilter) for f in logger.filters)  # installed by main
    with caplog.at_level(logging.INFO, logger="uvicorn.access"):
        logger.info(
            '%s - "%s %s HTTP/%s" %d',
            "1.2.3.4:5",
            "GET",
            f"/api/statement/{token}/preview?x=1",
            "1.1",
            200,
        )
    assert token not in caplog.text and "/api/statement/<redacted>/preview" in caplog.text
    assert redact(f"/api/statement/{token}") == "/api/statement/<redacted>"


def test_app_logs_use_a_token_hash(client, db, caplog):
    with caplog.at_level(logging.INFO, logger="statement"):
        client.get("/api/statement/some-unknown-token")
    assert "some-unknown-token" not in caplog.text
    assert svc.token_ref("some-unknown-token") in caplog.text
