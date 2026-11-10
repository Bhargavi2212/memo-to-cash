"""PayPal Orders client (P2 D9, D10, R2, R3) — body, keys, error mapping, capture limits."""

import json

import httpx
import pytest
from pydantic import SecretStr

from backend.core.config import load_settings
from backend.paypal import client as ppclient
from backend.paypal import orders
from backend.paypal.client import PayPal, PayPalError

UID = "5b1f8a7e-0000-4000-8000-000000000001"
LINES = [
    orders.OrderLine("INV-1038", "M-201", 350000),
    orders.OrderLine("INV-1042", "M-207", 150000),
]


@pytest.fixture
def settings():
    return load_settings(
        _env_file=None,
        app_env="test",
        paypal_client_id="id",
        paypal_client_secret=SecretStr("s"),
    )


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    ppclient.clear_token_cache()

    async def instant(_s):
        return None

    monkeypatch.setattr(ppclient, "_sleep", instant)


def test_body_items_sum_to_amount_and_item_total(settings):
    b = orders.build_order_body(UID, LINES, "paypal", settings)
    pu = b["purchase_units"][0]
    assert b["intent"] == "CAPTURE"
    assert pu["amount"] == {
        "currency_code": "USD",
        "value": "5000.00",
        "breakdown": {"item_total": {"currency_code": "USD", "value": "5000.00"}},
    }
    assert pu["items"] == [
        {
            "name": "INV-1038 (memo M-201)",
            "quantity": "1",
            "unit_amount": {"currency_code": "USD", "value": "3500.00"},
        },
        {
            "name": "INV-1042 (memo M-207)",
            "quantity": "1",
            "unit_amount": {"currency_code": "USD", "value": "1500.00"},
        },
    ]
    assert pu["custom_id"] == UID and pu["invoice_id"] == f"test-stmt-{UID}"
    assert (
        pu["description"] == "Statement payment · Demo Diamond Co."
        and len(pu["description"]) <= 127
    )


@pytest.mark.parametrize("funding", ["paypal", "paylater", "credit", "PayPal"])
def test_paypal_family_uses_paypal_experience_context(settings, funding):
    b = orders.build_order_body(UID, LINES, funding, settings)
    assert b["payment_source"] == {
        "paypal": {
            "experience_context": {
                "brand_name": "Demo Diamond Co.",
                "shipping_preference": "NO_SHIPPING",
                "user_action": "PAY_NOW",
            }
        }
    }
    assert "application_context" not in b


def test_venmo_uses_venmo_experience_context(settings):
    b = orders.build_order_body(UID, LINES, "venmo", settings)
    assert set(b["payment_source"]) == {"venmo"}
    assert (
        b["payment_source"]["venmo"]["experience_context"]["shipping_preference"] == "NO_SHIPPING"
    )


@pytest.mark.parametrize("funding", ["card", None, "", "something-new"])
def test_card_or_unknown_has_no_payment_source(settings, funding):
    b = orders.build_order_body(UID, LINES, funding, settings)
    assert "payment_source" not in b
    assert b["application_context"]["shipping_preference"] == "NO_SHIPPING"


@pytest.mark.parametrize(
    "bad", [[], [orders.OrderLine("INV-1", "M-1", 0)], [orders.OrderLine("INV-1", "M-1", 1.5)]]
)
def test_body_rejects_bad_lines(settings, bad):
    with pytest.raises(ValueError):
        orders.build_order_body(UID, bad, "paypal", settings)


def test_capture_request_ids(settings):
    assert orders.capture_request_id(settings, UID, 0) == f"test-cap-{UID}"
    assert orders.capture_request_id(settings, UID, 1) == f"test-cap-{UID}-1"
    assert orders.capture_request_id(settings, UID, 2) == f"test-cap-{UID}-2"


def _err(status, issue=None, transient=False):
    details = [{"issue": issue}] if issue else []
    return PayPalError("UNPROCESSABLE_ENTITY", "m", "dbg", details, status, transient)


@pytest.mark.parametrize(
    ("err", "kind"),
    [
        (_err(422, "ORDER_ALREADY_CAPTURED"), "ALREADY_CAPTURED"),
        (_err(422, "INSTRUMENT_DECLINED"), "DECLINED"),
        (_err(422, "PAYER_ACTION_REQUIRED"), "DECLINED"),
        (_err(422, "TRANSACTION_REFUSED"), "DECLINED"),
        (_err(422, "MAX_NUMBER_OF_PAYMENT_ATTEMPTS_EXCEEDED"), "DECLINED"),
        (_err(422, "ORDER_NOT_APPROVED"), "NOT_APPROVED"),
        (_err(422, "DUPLICATE_INVOICE_ID"), "DUPLICATE_INVOICE_ID"),
        (_err(503, transient=True), "TRANSIENT"),
        (_err(None, transient=True), "TRANSIENT"),
        (_err(400, "SOMETHING_ELSE"), "REJECTED"),
        (PayPalError("ORDER_ALREADY_CAPTURED", "m", None, [], 422), "REJECTED"),  # issue, not name
    ],
)
def test_classify_by_issue(err, kind):
    assert orders.classify(err) == kind


def test_approve_link_falls_back_to_payer_action():
    assert (
        orders.approve_link(
            {"links": [{"rel": "approve", "href": "A"}, {"rel": "payer-action", "href": "P"}]}
        )
        == "A"
    )
    assert (
        orders.approve_link(
            {"links": [{"rel": "self", "href": "S"}, {"rel": "payer-action", "href": "P"}]}
        )
        == "P"
    )
    assert orders.approve_link({}) is None


def test_first_capture():
    order = {"purchase_units": [{"payments": {"captures": [{"id": "C1", "status": "COMPLETED"}]}}]}
    assert orders.first_capture(order) == {"id": "C1", "status": "COMPLETED"}
    assert orders.first_capture({"purchase_units": [{}]}) is None


# ---------- wire behaviour ----------


class Rec:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls: list[httpx.Request] = []

    def __call__(self, req):
        if req.url.path == "/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": "t", "expires_in": 32400})
        self.calls.append(req)
        nxt = self.responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


@pytest.mark.anyio
async def test_create_sends_request_id_and_prefer(settings):
    rec = Rec(httpx.Response(201, json={"id": "O1", "status": "CREATED", "links": []}))
    pp = PayPal(settings, transport=httpx.MockTransport(rec))
    body = await orders.create(pp, UID, LINES, "paypal")
    req = rec.calls[0]
    assert body["id"] == "O1"
    assert req.headers["PayPal-Request-Id"] == f"test-ord-{UID}"
    assert req.headers["Prefer"] == "return=representation"
    assert json.loads(req.content)["purchase_units"][0]["custom_id"] == UID


@pytest.mark.anyio
async def test_capture_is_one_try_with_10s_timeout(settings):
    rec = Rec(httpx.Response(503, json={"name": "INTERNAL_SERVER_ERROR", "debug_id": "d"}))
    pp = PayPal(settings, transport=httpx.MockTransport(rec))
    with pytest.raises(PayPalError) as e:
        await orders.capture(pp, "O1", UID, 1)
    assert len(rec.calls) == 1 and e.value.transient  # no client retries under the lock
    req = rec.calls[0]
    assert req.url.path == "/v2/checkout/orders/O1/capture"
    assert req.headers["PayPal-Request-Id"] == f"test-cap-{UID}-1"
    assert req.extensions["timeout"]["read"] == 10.0


@pytest.mark.anyio
async def test_capture_timeout_is_transient(settings):
    rec = Rec(httpx.ReadTimeout("slow"))
    pp = PayPal(settings, transport=httpx.MockTransport(rec))
    with pytest.raises(PayPalError) as e:
        await orders.capture(pp, "O1", UID)
    assert e.value.name == "TIMEOUT" and len(rec.calls) == 1


@pytest.mark.anyio
async def test_ensure_token_caches_before_any_call(settings):
    rec = Rec(httpx.Response(200, json={"id": "O1", "status": "APPROVED"}))
    tokens = []

    def handler(req):
        if req.url.path == "/v1/oauth2/token":
            tokens.append(1)
        return rec(req)

    pp = PayPal(settings, transport=httpx.MockTransport(handler))
    await pp.ensure_token()
    await orders.get(pp, "O1")
    assert len(tokens) == 1 and rec.calls[0].method == "GET"
