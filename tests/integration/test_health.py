"""Health endpoints. DB, PayPal and the LLM are mocked; no TEST_DATABASE_URL needed (P0)."""

import time

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from backend.core.config import settings
from backend.main import app
from backend.routes import health

REAL_CHECK_PAYPAL_TOKEN = health.check_paypal_token  # captured before fixtures mock it

ADMIN = "admin-token-should-never-leak"
PAYPAL_SECRET = "paypal-secret-should-never-leak"
LLM_KEY = "llm-key-should-never-leak"
AUTH = {"Authorization": f"Bearer {ADMIN}"}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(settings, "admin_token", SecretStr(ADMIN))
    monkeypatch.setattr(settings, "paypal_client_id", "client-id")
    monkeypatch.setattr(settings, "paypal_client_secret", SecretStr(PAYPAL_SECRET))
    monkeypatch.setattr(settings, "llm_api_key", SecretStr(LLM_KEY))
    monkeypatch.setattr(health, "check_db", lambda: True)
    monkeypatch.setattr(health, "check_paypal_token", lambda: True)
    monkeypatch.setattr(health, "check_llm", lambda: {"ok": True, "model": "m", "ms": 12})
    return TestClient(app)


def _boom():
    raise RuntimeError("down")


def test_health_shape(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "sandbox": True, "db": True, "env": "test"}


def test_health_db_down_is_false_not_500(client, monkeypatch):
    monkeypatch.setattr(health, "check_db", _boom)
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["db"] is False


def test_health_db_timeout_is_false(client, monkeypatch):
    monkeypatch.setattr(health, "DB_TIMEOUT_S", 0.05)
    monkeypatch.setattr(health, "check_db", lambda: time.sleep(0.5) or True)
    assert client.get("/api/health").json()["db"] is False


def test_deep_ok_shape(client):
    r = client.get("/api/health/deep", headers=AUTH)
    assert r.status_code == 200
    assert r.json() == {
        "db": True,
        "paypal_token": True,
        "llm": {"ok": True, "model": "m", "ms": 12},
    }


@pytest.mark.parametrize(
    "headers", [{}, {"Authorization": "Bearer wrong"}, {"Authorization": ADMIN}]
)
def test_deep_requires_admin(client, headers):
    r = client.get("/api/health/deep", headers=headers)
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "UNAUTHORIZED"


def test_deep_503_when_admin_token_unset(client, monkeypatch):
    monkeypatch.setattr(settings, "admin_token", None)
    r = client.get("/api/health/deep", headers=AUTH)
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "ADMIN_TOKEN_UNSET"


def test_deep_paypal_false_when_keys_missing(client, monkeypatch):
    # the real check returns False before any network call when keys are missing
    monkeypatch.setattr(health, "check_paypal_token", REAL_CHECK_PAYPAL_TOKEN)
    monkeypatch.setattr(settings, "paypal_client_secret", None)
    assert client.get("/api/health/deep", headers=AUTH).json()["paypal_token"] is False


def test_deep_failing_checks_report_false(client, monkeypatch):
    monkeypatch.setattr(health, "check_db", _boom)
    monkeypatch.setattr(health, "check_paypal_token", _boom)
    monkeypatch.setattr(health, "check_llm", _boom)
    body = client.get("/api/health/deep", headers=AUTH).json()
    assert body["db"] is False and body["paypal_token"] is False and body["llm"]["ok"] is False


def test_no_secret_in_any_response(client, monkeypatch):
    monkeypatch.setattr(health, "check_db", _boom)
    texts = [
        client.get("/api/health").text,
        client.get("/api/health/deep", headers=AUTH).text,
        client.get("/api/health/deep").text,
        client.get("/api/health/deep", headers={"Authorization": "Bearer wrong"}).text,
    ]
    for t in texts:
        for secret in (ADMIN, PAYPAL_SECRET, LLM_KEY, "test:test@"):
            assert secret not in t


def test_docs_live_under_api(client):
    assert client.get("/api/openapi.json").status_code == 200
    assert client.get("/api/docs").status_code == 200
    assert client.get("/openapi.json").status_code == 404


def test_live_never_touches_the_database(client, monkeypatch):
    """Render's health check must let Neon sleep (P2 D24)."""

    def boom():
        raise AssertionError("health/live must not query the database")

    monkeypatch.setattr(health, "check_db", boom)
    r = client.get("/api/health/live")
    assert r.status_code == 200 and r.json() == {"ok": True}
