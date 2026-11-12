"""PayPal webhooks (FR-7; P3b B1–B3, §9 F1; plan 1a–1b; TRD §6b–6b-2; invariants 4, 10).

1a: the route verifies the signature with PayPal (the RAW body spliced in, R9), stores the event
once (unique `paypal_event_id`) and answers 200; unverified → 400 and nothing stored; PayPal's
verify endpoint unreachable → 503 so PayPal retries (plan G4)."""

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from backend.core.config import get_settings
from backend.db.models import WebhookEvent
from backend.main import app
from backend.paypal import client as ppclient
from backend.paypal.client import PayPal, get_paypal
from tests.integration.test_demo import make_settings
from tests.paypal_fake import FakePayPal, error

WEBHOOK_ID = "WH-ID-TEST"
HEADERS = {
    "PayPal-Transmission-Id": "tx-1",
    "PayPal-Transmission-Time": "2026-11-11T10:00:00Z",
    "PayPal-Transmission-Sig": "sig==",
    "PayPal-Cert-Url": "https://api.sandbox.paypal.com/v1/notifications/certs/CERT-1",
    "PayPal-Auth-Algo": "SHA256withRSA",
}


def raw_event(event_id="WH-EVT-1", event_type="INVOICING.INVOICE.PAID", resource=None) -> bytes:
    # odd spacing and a "1.10" on purpose: a re-serialised dict would change these bytes
    res = json.dumps(resource or {"invoice": {"id": "INV2-UNKNOWN"}, "x": 1})
    return (f'{{"id": "{event_id}",  "event_type":"{event_type}", "amount": 1.10, '
            f'"resource": {res}}}').encode()  # fmt: skip


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
    return make_settings(paypal_webhook_id=WEBHOOK_ID)


@pytest.fixture
def api(db, fake, settings):
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_paypal] = lambda: PayPal(settings, transport=fake.transport())
    with TestClient(app, base_url="https://testserver") as c:
        yield c
    app.dependency_overrides.clear()


def post(api, body=None, headers=None):
    return api.post("/api/paypal/webhook", content=body if body is not None else raw_event(),
                    headers=HEADERS if headers is None else headers)  # fmt: skip


def stored(db):
    db.expire_all()
    return db.scalar(select(func.count()).select_from(WebhookEvent))


# ---------- 1a: verification + store ----------


def test_a_verified_event_is_stored_once_and_answered_200(api, db, fake):
    r = post(api)
    assert r.status_code == 200, r.text
    ev = db.scalars(select(WebhookEvent)).one()
    assert (ev.paypal_event_id, ev.event_type) == ("WH-EVT-1", "INVOICING.INVOICE.PAID")
    assert ev.payload["resource"]["invoice"]["id"] == "INV2-UNKNOWN"
    assert post(api).status_code == 200  # PayPal redelivers: still 200, nothing new
    assert stored(db) == 1


def test_verification_sends_the_raw_body_unchanged(api, db, fake):
    body = raw_event()
    post(api, body)
    (req,) = [r for r in fake.requests if fake.classify(r) == ("verify", "")]
    sent = req.content.decode()
    assert f'"webhook_event":{body.decode()}' in sent.replace(
        '"webhook_event": ', '"webhook_event":'
    )
    v = json.loads(sent)
    assert v["webhook_id"] == WEBHOOK_ID and v["transmission_id"] == "tx-1"
    assert v["auth_algo"] == "SHA256withRSA" and v["cert_url"].endswith("CERT-1")
    assert v["transmission_sig"] == "sig==" and v["transmission_time"] == "2026-11-11T10:00:00Z"


def test_a_failed_signature_is_400_and_not_stored(api, db, fake):
    fake.verify_status = "FAILURE"
    assert post(api).status_code == 400
    assert stored(db) == 0


def test_missing_transmission_headers_are_400_without_asking_paypal(api, db, fake):
    assert post(api, headers={}).status_code == 400
    assert stored(db) == 0 and fake.calls == []


def test_verify_unreachable_is_503_so_paypal_retries(api, db, fake):
    fake.fail("verify", *[error(503)] * 10)
    r = post(api)
    assert r.status_code == 503 and r.json()["error"]["code"] == "PAYPAL_UNAVAILABLE"
    assert stored(db) == 0


def test_no_webhook_id_configured_is_503(db, fake):
    s = make_settings()  # PAYPAL_WEBHOOK_ID unset
    app.dependency_overrides[get_settings] = lambda: s
    app.dependency_overrides[get_paypal] = lambda: PayPal(s, transport=fake.transport())
    try:
        with TestClient(app, base_url="https://testserver") as c:
            r = post(c)
    finally:
        app.dependency_overrides.clear()
    assert r.status_code == 503 and r.json()["error"]["code"] == "NOT_CONFIGURED"
    assert stored(db) == 0 and fake.calls == []


def test_a_body_that_isnt_an_event_is_400(api, db, fake):
    assert post(api, b'{"no": "id"}').status_code == 400
    assert post(api, b"not json").status_code == 400
    assert stored(db) == 0


# ---------- 1b: the route hands verified events to processing ----------


def test_a_stored_event_is_processed_after_the_response(api, db, fake):
    assert post(api).status_code == 200  # TestClient runs the BackgroundTask before returning
    ev = db.scalars(select(WebhookEvent)).one()
    db.refresh(ev)
    assert ev.processed_at is not None and ev.error == "ignored: unknown"  # G16: not ours


def test_processing_is_handed_off_with_the_stored_row(api, db, fake, monkeypatch):
    """Processing runs as a Starlette BackgroundTask (after the response is sent — Starlette's
    guarantee; TestClient itself waits for it, so ordering isn't observable here)."""
    from backend.services import sync

    seen: list[int] = []

    async def record(_sf, _pp, _settings, row_id, **_k):
        seen.append(row_id)

    monkeypatch.setattr(sync, "process_event", record)
    assert post(api).status_code == 200
    assert seen == [db.scalars(select(WebhookEvent.id)).one()]
    assert post(api).status_code == 200 and len(seen) == 1  # redelivery: not handed off again
