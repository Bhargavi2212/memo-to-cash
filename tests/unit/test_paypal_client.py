"""PayPal client (P1 D14–D16, D19, R4): token cache, retries, Request-Id, errors, safe logs.

Error shape {name, message, debug_id, details} per APIMatic ContextMatic (docs/APIMATIC.md #2).
"""

import json
import logging

import httpx
import pytest
from pydantic import SecretStr

from backend.core.config import load_settings
from backend.paypal import client as pp

SECRET = "paypal-secret-should-never-leak"
TOKEN = "A21-token-should-never-leak"
EMAIL = "buyer-should-never-leak@example.com"

pytestmark = pytest.mark.anyio


@pytest.fixture
def settings():
    return load_settings(
        _env_file=None,
        paypal_client_id="client-id",
        paypal_client_secret=SecretStr(SECRET),
        app_env="test",
    )


@pytest.fixture(autouse=True)
def fresh_cache(monkeypatch):
    pp.clear_token_cache()
    sleeps: list[float] = []

    async def no_sleep(s):
        sleeps.append(s)

    monkeypatch.setattr(pp, "_sleep", no_sleep)
    return sleeps


class Recorder:
    """MockTransport handler: token endpoint + a scripted queue of API responses."""

    def __init__(self, *responses, expires_in=32400):
        self.responses = list(responses)
        self.calls: list[httpx.Request] = []
        self.token_calls = 0
        self.expires_in = expires_in

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/oauth2/token":
            self.token_calls += 1
            return httpx.Response(
                200,
                json={"access_token": TOKEN, "token_type": "Bearer", "expires_in": self.expires_in},
            )
        self.calls.append(request)
        nxt = self.responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


def err(status, name="INTERNAL_SERVER_ERROR", debug_id="dbg123"):
    return httpx.Response(
        status,
        json={
            "name": name,
            "message": "it failed",
            "debug_id": debug_id,
            "details": [{"issue": "X"}],
        },
        headers={"PayPal-Debug-Id": debug_id},
    )


def ok(body=None):
    return httpx.Response(200, json=body or {"id": "INV2-1"})


def make(settings, rec):
    return pp.PayPal(settings, transport=httpx.MockTransport(rec))


async def test_token_cached_across_calls(settings):
    rec = Recorder(ok(), ok())
    c = make(settings, rec)
    await c.request("GET", "/v2/invoicing/invoices/INV2-1")
    await c.request("GET", "/v2/invoicing/invoices/INV2-1")
    assert rec.token_calls == 1
    assert rec.calls[0].headers["Authorization"] == f"Bearer {TOKEN}"


async def test_token_refreshed_when_close_to_expiry(settings, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(pp, "_now", lambda: now[0])
    rec = Recorder(ok(), ok(), expires_in=600)
    c = make(settings, rec)
    await c.request("GET", "/x")
    now[0] += 600 - 60 - 1  # still inside expires_in - 60 s
    await c.request("GET", "/x")
    assert rec.token_calls == 1
    now[0] += 2  # past expires_in - 60 s
    rec.responses.append(ok())
    await c.request("GET", "/x")
    assert rec.token_calls == 2


async def test_401_refreshes_token_once(settings):
    rec = Recorder(err(401, "INVALID_TOKEN"), ok())
    assert await make(settings, rec).request("GET", "/x") == {"id": "INV2-1"}
    assert rec.token_calls == 2


@pytest.mark.parametrize(
    "first", [err(503), err(429, "RATE_LIMIT_REACHED"), httpx.ReadTimeout("slow")]
)
async def test_transient_errors_are_retried_with_same_request_id(settings, first, fresh_cache):
    rec = Recorder(first, ok())
    body = await make(settings, rec).request(
        "POST", "/v2/invoicing/invoices", json={}, request_id="test-inv-u1"
    )
    assert body == {"id": "INV2-1"}
    assert [r.headers["PayPal-Request-Id"] for r in rec.calls] == ["test-inv-u1"] * 2
    assert fresh_cache == [0.5]


async def test_gives_up_after_three_retries(settings, fresh_cache):
    rec = Recorder(err(503), err(503), err(503), err(503))
    with pytest.raises(pp.PayPalError) as e:
        await make(settings, rec).request("POST", "/x", json={}, request_id="k")
    assert len(rec.calls) == 4 and fresh_cache == [0.5, 1.0, 2.0]
    assert e.value.transient and e.value.status == 503


async def test_timeout_after_retries_is_transient(settings):
    rec = Recorder(*[httpx.ConnectTimeout("t")] * 4)
    with pytest.raises(pp.PayPalError) as e:
        await make(settings, rec).request("GET", "/x")
    assert e.value.transient and e.value.status is None and e.value.name == "TIMEOUT"


@pytest.mark.parametrize("status", [400, 404, 422])
async def test_4xx_is_not_retried_and_is_deterministic(settings, status):
    rec = Recorder(err(status, "DUPLICATE_INVOICE_NUMBER", "dbg-4xx"))
    with pytest.raises(pp.PayPalError) as e:
        await make(settings, rec).request("POST", "/x", json={}, request_id="k")
    assert len(rec.calls) == 1
    x = e.value
    assert (x.status, x.name, x.message, x.debug_id) == (
        status,
        "DUPLICATE_INVOICE_NUMBER",
        "it failed",
        "dbg-4xx",
    )
    assert x.details == [{"issue": "X"}] and not x.transient


async def test_writes_without_request_id_are_refused(settings):
    with pytest.raises(ValueError, match="PayPal-Request-Id"):
        await make(settings, Recorder()).request("POST", "/x", json={})


async def test_per_call_timeout_is_8s(settings):
    assert pp.TIMEOUT_S == 8.0
    seen = {}

    def handler(request):
        if request.url.path != "/v1/oauth2/token":
            seen["timeout"] = request.extensions["timeout"]
        return Recorder(ok())(request) if request.url.path == "/v1/oauth2/token" else ok()

    await pp.PayPal(settings, transport=httpx.MockTransport(handler)).request("GET", "/x")
    assert seen["timeout"] == {"connect": 8.0, "read": 8.0, "write": 8.0, "pool": 8.0}


async def test_not_configured_names_missing_settings():
    s = load_settings(_env_file=None, paypal_client_id=None, paypal_client_secret=None)
    with pytest.raises(pp.PayPalNotConfiguredError) as e:
        await pp.PayPal(s, transport=httpx.MockTransport(Recorder())).request("GET", "/x")
    assert e.value.missing == ["PAYPAL_CLIENT_ID", "PAYPAL_CLIENT_SECRET"]


async def test_request_id_uses_env_and_uid(settings):
    assert pp.request_id(settings, "inv", "1b2c") == "test-inv-1b2c"
    assert pp.request_id(settings, "send", "1b2c", 2) == "test-send-1b2c-2"


async def test_logs_have_debug_id_but_no_secrets_or_bodies(settings, caplog):
    caplog.set_level(logging.DEBUG)
    rec = Recorder(err(400, "VALIDATION_ERROR", "dbg-log"))
    with pytest.raises(pp.PayPalError):
        await make(settings, rec).request(
            "POST",
            "/v2/invoicing/invoices",
            json={"primary_recipients": [{"email": EMAIL}]},
            request_id="k",
        )
    text = caplog.text
    assert "dbg-log" in text and "/v2/invoicing/invoices" in text and "400" in text
    for leak in (SECRET, TOKEN, EMAIL, "client-id:"):
        assert leak not in text
    # the error itself never carries the request body either
    assert EMAIL not in json.dumps(vars(pp.PayPalError("n", "m", None, [], 400)))
