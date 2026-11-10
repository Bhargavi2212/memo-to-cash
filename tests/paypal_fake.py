"""In-memory PayPal Invoicing sandbox for tests (httpx.MockTransport).

Behaves like the real sandbox did in the P1 open checks (tests/fixtures/paypal): create
with `Prefer: return=representation` returns the full invoice (DRAFT); a duplicate number is
422 UNPROCESSABLE_ENTITY with details[].issue DUPLICATE_INVOICE_NUMBER (the real fixture's
body); send returns `{rel: payer-view, href}` and makes the invoice UNPAID; search finds by
invoice_number. Faults can be queued per operation.
"""

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
        return httpx.MockTransport(self.handle)

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

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": "fake", "expires_in": 32400})
        body = json.loads(request.content) if request.content else {}
        base = "/v2/invoicing/invoices"
        if request.method == "POST" and path == base:
            op, key = "create", body["detail"]["invoice_number"]
        elif request.method == "POST" and path == "/v2/invoicing/search-invoices":
            op, key = "search", body.get("invoice_number", "")
        elif request.method == "POST" and path.endswith("/send"):
            op, key = "send", path.split("/")[-2]
        elif request.method == "GET" and path.startswith(base + "/"):
            op, key = "get", path.split("/")[-1]
        else:
            return error(404, "RESOURCE_NOT_FOUND")
        self.calls.append((op, key))
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
