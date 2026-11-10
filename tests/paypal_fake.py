"""In-memory PayPal Invoicing sandbox for tests (httpx.MockTransport).

Behaves like the real sandbox did in the P1 open checks (tests/fixtures/paypal): create
with `Prefer: return=representation` returns the full invoice (DRAFT); a duplicate number is
422 UNPROCESSABLE_ENTITY with details[].issue DUPLICATE_INVOICE_NUMBER (the real fixture's
body); send returns `{rel: payer-view, href}` and makes the invoice UNPAID; search finds by
invoice_number. Faults can be queued per operation.

Orders (P2): create → CREATED with an `approve` link (tests call `approve(order_id)` for the
buyer's click); capture of an APPROVED order → COMPLETED in the shape of the real smoke
fixture (`capture_order_paypal.json`); a second capture → 422 ORDER_ALREADY_CAPTURED; not
approved → 422 ORDER_NOT_APPROVED. `capture_status` makes the capture PENDING / DECLINED,
`delay[op]` makes an operation slow (async), and every op can be failed with `fail()`.
"""

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx

from backend.core.money import to_cents

FIX = Path(__file__).resolve().parent / "fixtures" / "paypal"


def _fixture(name: str) -> dict[str, Any]:
    return json.loads((FIX / f"{name}.json").read_text(encoding="utf-8"))


def error(status: int, name: str = "INTERNAL_SERVER_ERROR", **extra: Any) -> httpx.Response:
    body = {"name": name, "message": "fake failure", "debug_id": "fake-debug", "details": []}
    body.update(extra)
    return httpx.Response(status, json=body, headers={"PayPal-Debug-Id": "fake-debug"})


class FakePayPal:
    def __init__(self) -> None:
        self.invoices: dict[str, dict[str, Any]] = {}
        self.faults: dict[str, list[httpx.Response | Exception]] = {}
        self.calls: list[tuple[str, str]] = []  # (op, invoice id or number)
        self.due_override: dict[str, str] = {}  # invoice number -> due value PayPal reports
        self.orders: dict[str, dict[str, Any]] = {}
        self.capture_status = "COMPLETED"  # what the next successful capture reports
        self.captured_value: str | None = None  # override the captured amount (mismatch tests)
        self.delay: dict[str, float] = {}  # op -> seconds (slow PayPal)
        self.requests: list[httpx.Request] = []  # every API request (headers for Request-Id)
        self.hooks: dict[str, Any] = {}  # op -> callable(request), run before answering
        self._n = 0

    # -- helpers for tests --
    def fail(self, op: str, *responses: httpx.Response | Exception) -> None:
        self.faults.setdefault(op, []).extend(responses)

    def ops(self) -> list[str]:
        return [op for op, _ in self.calls]

    def by_number(self, number: str) -> dict[str, Any] | None:
        return next(
            (i for i in self.invoices.values() if i["detail"]["invoice_number"] == number), None
        )

    def transport(self) -> httpx.MockTransport:
        async def handler(request: httpx.Request) -> httpx.Response:
            op = self.classify(request)
            if op and self.delay.get(op[0]):
                await asyncio.sleep(self.delay[op[0]])
            return self.handle(request)

        return httpx.MockTransport(handler)

    def approve(self, order_id: str) -> None:
        """The buyer approves in PayPal's window."""
        self.orders[order_id]["status"] = "APPROVED"

    def request_ids(self, op: str) -> list[str]:
        return [
            r.headers.get("PayPal-Request-Id", "")
            for r in self.requests
            if (self.classify(r) or ("",))[0] == op
        ]

    def classify(self, request: httpx.Request) -> tuple[str, str] | None:
        """(op, key) for any API request — Orders or Invoicing."""
        found = self._op(request)
        if found:
            return found
        path, m = request.url.path, request.method
        base = "/v2/invoicing/invoices"
        if m == "POST" and path == base:
            return ("create", "")
        if m == "POST" and path == "/v2/invoicing/search-invoices":
            return ("search", "")
        if m == "POST" and path.endswith("/send"):
            return ("send", path.split("/")[-2])
        if m == "POST" and path.endswith("/payments"):
            return ("record", path.split("/")[-2])
        if m == "GET" and path.startswith(base + "/"):
            return ("get", path.split("/")[-1])
        return None

    def add_existing(self, body: dict[str, Any], status: str = "DRAFT") -> dict[str, Any]:
        """Simulate an invoice PayPal already has (e.g. a create whose answer was lost)."""
        return self._store(body, status)

    # -- the fake API --
    def _store(self, body: dict[str, Any], status: str) -> dict[str, Any]:
        self._n += 1
        inv_id = f"INV2-FAKE-{self._n:04d}"
        total = sum(to_cents(i["unit_amount"]["value"]) for i in body.get("items", []))
        number = body["detail"]["invoice_number"]
        due = self.due_override.get(number, f"{total // 100}.{total % 100:02d}")
        inv = {
            **body,
            "id": inv_id,
            "status": status,
            "amount": {"currency_code": "USD", "value": f"{total // 100}.{total % 100:02d}"},
            "due_amount": {"currency_code": "USD", "value": due},
            "detail": {**body["detail"], "metadata": {}},
        }
        self.invoices[inv_id] = inv
        return inv

    def _op(self, request: httpx.Request) -> tuple[str, str] | None:
        path, m = request.url.path, request.method
        ob = "/v2/checkout/orders"
        if m == "POST" and path == ob:
            return ("order_create", "")
        if m == "POST" and path.startswith(ob + "/") and path.endswith("/capture"):
            return ("order_capture", path.split("/")[-2])
        if m == "GET" and path.startswith(ob + "/"):
            return ("order_get", path.split("/")[-1])
        return None

    def _order(self, op: str, key: str, body: dict[str, Any]) -> httpx.Response:
        if op == "order_create":
            self._n += 1
            oid = f"ORDER-FAKE-{self._n:04d}"
            order = {
                "id": oid,
                "status": "CREATED",
                "intent": body.get("intent"),
                "purchase_units": body["purchase_units"],
                "links": [
                    {
                        "rel": "approve",
                        "href": f"https://www.sandbox.paypal.com/checkoutnow?token={oid}",
                    }
                ],
            }
            self.orders[oid] = order
            return httpx.Response(201, json=order)
        order = self.orders.get(key)
        if order is None:
            return error(404, "RESOURCE_NOT_FOUND")
        if op == "order_get":
            return httpx.Response(200, json=order)
        # capture
        if order["status"] == "COMPLETED":
            return error(422, "UNPROCESSABLE_ENTITY", details=[{"issue": "ORDER_ALREADY_CAPTURED"}])
        if order["status"] != "APPROVED":
            return error(422, "UNPROCESSABLE_ENTITY", details=[{"issue": "ORDER_NOT_APPROVED"}])
        pu = order["purchase_units"][0]
        amount = dict(pu["amount"])
        amount.pop("breakdown", None)
        if self.captured_value is not None:
            amount["value"] = self.captured_value
        cap = {
            "id": f"CAP-FAKE-{self._n:04d}",
            "status": self.capture_status,
            "amount": amount,
            "custom_id": pu.get("custom_id"),
            "invoice_id": pu.get("invoice_id"),
            "final_capture": True,
        }
        if self.capture_status in ("COMPLETED", "PENDING"):
            order["status"] = "COMPLETED"
        order["purchase_units"] = [{**pu, "payments": {"captures": [cap]}}]
        return httpx.Response(201, json=order)

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": "fake", "expires_in": 32400})
        self.requests.append(request)
        body = json.loads(request.content) if request.content else {}
        order_op = self._op(request)
        if order_op:
            op, key = order_op
            self.calls.append((op, key))
            queued = self.faults.get(op)
            if queued:
                nxt = queued.pop(0)
                if isinstance(nxt, Exception):
                    raise nxt
                return nxt
            return self._order(op, key, body)
        base = "/v2/invoicing/invoices"
        if request.method == "POST" and path == base:
            op, key = "create", body["detail"]["invoice_number"]
        elif request.method == "POST" and path == "/v2/invoicing/search-invoices":
            op, key = "search", body.get("invoice_number", "")
        elif request.method == "POST" and path.endswith("/send"):
            op, key = "send", path.split("/")[-2]
        elif request.method == "POST" and path.endswith("/payments"):
            op, key = "record", path.split("/")[-2]
        elif request.method == "GET" and path.startswith(base + "/"):
            op, key = "get", path.split("/")[-1]
        else:
            return error(404, "RESOURCE_NOT_FOUND")
        self.calls.append((op, key))
        if op in self.hooks:
            self.hooks[op](request)
        queued = self.faults.get(op)
        if queued:
            nxt = queued.pop(0)
            if isinstance(nxt, Exception):
                raise nxt
            return nxt

        if op == "create":
            if self.by_number(key):
                dup = _fixture("create_duplicate_number_error")
                dup["details"][0]["value"] = key
                return httpx.Response(422, json=dup)
            return httpx.Response(201, json=self._store(body, "DRAFT"))
        if op == "search":
            found = self.by_number(key)
            return httpx.Response(
                200, json={"total_items": int(bool(found)), "items": [found] if found else []}
            )
        inv = self.invoices.get(key)
        if inv is None:
            return error(404, "RESOURCE_NOT_FOUND")
        if op == "get":
            return httpx.Response(200, json=inv)
        if op == "record":
            return self.record(key, body)
        # send
        if inv["status"] == "DRAFT":
            inv["status"] = "UNPAID"
            url = f"https://www.sandbox.paypal.com/invoice/p/#{key}"
            inv["detail"]["metadata"]["recipient_view_url"] = url
        return httpx.Response(
            200,
            json={
                "href": inv["detail"]["metadata"]["recipient_view_url"],
                "rel": "payer-view",
                "method": "GET",
            },
        )

    def record(self, inv_id: str, body: dict[str, Any]) -> httpx.Response:
        """Record payment like the sandbox: due drops, note visible in transactions."""
        inv = self.invoices[inv_id]
        due = to_cents(inv["due_amount"]["value"])
        amount = to_cents(body["amount"]["value"])
        if amount > due:
            return error(
                422,
                "UNPROCESSABLE_ENTITY",
                details=[{"issue": "PAYMENT_AMOUNT_GREATER_THAN_AMOUNT_DUE"}],
            )
        self._n += 1
        txn = {
            "payment_id": f"TXN-FAKE-{self._n:04d}",
            "note": body.get("note"),
            "method": body.get("method"),
            "amount": body["amount"],
        }
        payments = inv.setdefault("payments", {"transactions": []})
        payments["transactions"].append(txn)
        left = due - amount
        inv["due_amount"] = {"currency_code": "USD", "value": f"{left // 100}.{left % 100:02d}"}
        inv["status"] = "MARKED_AS_PAID" if left == 0 else "PARTIALLY_PAID"
        return httpx.Response(200, json={"payment_id": txn["payment_id"]})

    def pay_elsewhere(self, inv_id: str, amount_cents: int) -> None:
        """Someone pays this invoice on PayPal's own page (P2 D23)."""
        self.record(
            inv_id,
            {
                "amount": {"value": f"{amount_cents // 100}.{amount_cents % 100:02d}"},
                "note": "paid on PayPal page",
                "method": "PAYPAL",
            },
        )
