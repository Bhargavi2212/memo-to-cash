"""Invariant 7: a statement token reaches only its own buyer (P2 G23; P3 adds the supplier and
demo-cookie cases). Every statement route is tried with another buyer's ids."""

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select

from backend.core.config import get_settings, load_settings
from backend.core.dates import today_ny
from backend.db.models import Buyer, Invoice
from backend.main import app
from backend.paypal import client as ppclient
from backend.paypal.client import PayPal, get_paypal
from backend.services.demo import build_csv
from backend.services.importer import import_csv
from tests.paypal_fake import FakePayPal

D = today_ny()


@pytest.fixture
def fake():
    return FakePayPal()


@pytest.fixture
def api(db, fake):
    ppclient.clear_token_cache()
    s = load_settings(
        _env_file=None, app_env="test", paypal_client_id="id", paypal_client_secret=SecretStr("s")
    )
    app.dependency_overrides[get_settings] = lambda: s
    app.dependency_overrides[get_paypal] = lambda: PayPal(s, transport=fake.transport())
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def buyer(db, session, ready=True):
    res = import_csv(db, build_csv(D, "buyer@example.com"), demo_session=session)
    b = db.get(Buyer, res.buyer_ids[0])
    b.demo_ready = ready
    db.commit()
    return b


def test_a_token_never_reaches_another_buyer(db, api, fake):
    mine, theirs = buyer(db, "mine"), buyer(db, "theirs")
    t_mine, t_theirs = mine.statement_token, theirs.statement_token
    their_inv = db.scalars(select(Invoice.id).where(Invoice.buyer_id == theirs.id)).first()

    for path in ("/preview", "/order"):  # their invoice through my token
        r = api.post(f"/api/statement/{t_mine}{path}", json={"invoice_ids": [their_inv]})
        assert r.status_code == 422 and r.json()["error"]["code"] == "INVOICE_NOT_OPEN"

    oid = api.post(f"/api/statement/{t_theirs}/order", json={"amount_cents": 1000}).json()[
        "paypal_order_id"
    ]
    fake.approve(oid)
    r = api.post(f"/api/statement/{t_mine}/capture", json={"paypal_order_id": oid})
    assert r.status_code == 404
    pid = api.post(f"/api/statement/{t_theirs}/capture", json={"paypal_order_id": oid}).json()[
        "payment_id"
    ]
    assert api.get(f"/api/statement/{t_mine}/payments/{pid}").status_code == 404
    assert api.post(f"/api/statement/{t_mine}/payments/{pid}/refresh").status_code == 404
    assert api.get(f"/api/statement/{t_theirs}/payments/{pid}").status_code == 200

    s = api.get(f"/api/statement/{t_mine}").json()  # my statement shows only my things
    assert s["payments"] == [] and s["owed_cents"] == 842000


def test_not_ready_demo_copy_is_unreachable_everywhere(db, api):
    t = buyer(db, "half-built", ready=False).statement_token
    assert api.get(f"/api/statement/{t}").status_code == 404
    assert api.post(f"/api/statement/{t}/preview", json={}).status_code == 404
    assert api.post(f"/api/statement/{t}/order", json={}).status_code == 404
    r = api.post(f"/api/statement/{t}/capture", json={"paypal_order_id": "X"})
    assert r.status_code == 404
    assert api.get(f"/api/statement/{t}/payments/1").status_code == 404
