"""P1 open checks against the REAL PayPal sandbox (P1 step 7 / plan step 4).

    python -m scripts.sandbox.p1_open_checks

Answers TRD §9 checks 1, 6, 8, 9, 10, 11 plus review R6 (latency) and R12 (send retry),
confirms the D7 field paths, saves redacted responses to tests/fixtures/paypal/, and
cancels the invoices it created (failures tolerated). Prints a results table only —
never tokens, secrets or emails. Uses the real body builder (backend/paypal/invoices.py).
"""

import asyncio
import json
import re
import secrets
import string
import time
import uuid
from pathlib import Path
from typing import Any

from backend.core.config import settings
from backend.core.dates import offset, today_ny
from backend.paypal import invoices as inv
from backend.paypal.client import PayPal, PayPalError, request_id

FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "paypal"
D = today_ny()
results: list[tuple[str, str, str]] = []
created: list[str] = []


def note(check: str, outcome: str, detail: str = "") -> None:
    results.append((check, outcome, detail))
    print(f"[{check}] {outcome} {detail}", flush=True)


def number() -> str:
    tail = "".join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(6))
    return f"INV-9{secrets.randbelow(1000):03d}-{tail}"


def draft(**kw: Any) -> inv.InvoiceDraft:
    base = dict(
        uid=str(uuid.uuid4()),
        paypal_invoice_number=number(),
        erp_invoice_no="INV-9001",
        memo_no="M-201",
        issued_on=offset(D, -48),
        due_on=offset(D, -18),
        lines=(
            inv.DraftLine("S-101", "Round 1.01ct", 235000),
            inv.DraftLine("S-104", "Stud pair", 115000),
        ),
        buyer_name="Northside Jewelers",
        buyer_email=settings.sandbox_buyer_email or "",
        statement_token="open-checks-token",
    )
    return inv.InvoiceDraft(**(base | kw))


# ---------- redaction (public repo, review R8) ----------

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def redact(obj: Any) -> Any:
    buyer = (settings.sandbox_buyer_email or "").lower()

    def email(s: str) -> str:
        def sub(m: re.Match[str]) -> str:
            e = m.group(0).lower()
            if e.endswith("@example.com"):
                return e
            return "buyer@example.com" if e == buyer else "supplier@example.com"

        return _EMAIL.sub(sub, s)

    def walk(x: Any, key: str = "") -> Any:
        if isinstance(x, dict):
            out = {}
            for k, v in x.items():
                if k in ("phones",):
                    out[k] = []
                elif k in ("address", "shipping_info"):
                    out[k] = {}
                elif k == "name" and isinstance(v, dict):
                    out[k] = {}
                elif k in ("given_name", "surname", "full_name", "website", "tax_id", "logo_url"):
                    out[k] = ""
                elif k in ("recipient_view_url", "invoicer_view_url"):
                    out[k] = re.sub(r"(#|/p/|token=)[^\s\"&]*", r"\1REDACTED", str(v))
                else:
                    out[k] = walk(v, k)
            return out
        if isinstance(x, list):
            return [walk(v, key) for v in x]
        if isinstance(x, str):
            return email(x)
        return x

    return walk(obj)


def save(name: str, body: Any) -> None:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    text = json.dumps(redact(body), indent=2, sort_keys=True)
    leaked = [e for e in _EMAIL.findall(text) if not e.endswith("@example.com")]
    assert not leaked, f"redaction missed an email in {name}"
    (FIXTURES / f"{name}.json").write_text(text + "\n", encoding="utf-8")


def err_body(e: PayPalError) -> dict[str, Any]:
    return {"name": e.name, "message": e.message, "debug_id": "REDACTED", "details": e.details}


async def timed(coro: Any) -> tuple[Any, int]:
    t = time.perf_counter()
    r = await coro
    return r, int((time.perf_counter() - t) * 1000)


# ---------- checks ----------


async def main() -> int:
    if not settings.sandbox_buyer_email:
        print("SANDBOX_BUYER_EMAIL is not set in .env — stop.")
        return 2
    pp = PayPal(settings)

    # #8 + R6 + D7: create with past dates, Prefer: return=representation
    d1 = draft()
    try:
        body, ms = await timed(inv.create(pp, d1))
    except PayPalError as e:
        note("8", "FAIL create with past dates", f"{e.status} {e.name}: {e.message}")
        save("create_past_dates_error", err_body(e))
        return 1
    inv_id = body["id"]
    created.append(inv_id)
    full = "detail" in body
    note(
        "R6a",
        "full invoice returned by create" if full else "create returns only a link",
        f"{ms} ms",
    )
    save("create_invoice", body)

    sent, ms = await timed(inv.send(pp, inv_id, d1.uid))
    save("send_invoice", sent)
    has_link = inv.payer_url_from_send(sent) is not None
    note(
        "R6b",
        "send returns payer link" if has_link else "send has no payer link",
        f"{ms} ms; keys={sorted(sent)}",
    )

    got, ms = await timed(inv.get(pp, inv_id))
    save("get_invoice_sent", got)
    det = got.get("detail", {})
    ok8 = (
        det.get("invoice_date") == d1.issued_on.isoformat()
        and (det.get("payment_term") or {}).get("due_date") == d1.due_on.isoformat()
    )
    note(
        "8",
        "PASS past dates kept" if ok8 else "FAIL dates changed",
        f"status={got.get('status')} invoice_date={det.get('invoice_date')} "
        f"due={(det.get('payment_term') or {}).get('due_date')} (D-48 / D-18)",
    )
    view = (det.get("metadata") or {}).get("recipient_view_url")
    note("payer-url", "detail.metadata.recipient_view_url present" if view else "MISSING payer url")
    bname = (got.get("invoicer") or {}).get("business_name")
    billto = ((got.get("primary_recipients") or [{}])[0].get("billing_info") or {}).get(
        "business_name"
    )
    note(
        "D7",
        "field paths",
        f"invoicer.business_name={bname!r} billing_info.business_name={billto!r}",
    )
    due = (got.get("due_amount") or {}).get("value")
    note("due", "due_amount", f"{due} (expect 3500.00)")

    # #10: same create twice with the same Request-Id
    d2 = draft()
    a = await inv.create(pp, d2)
    created.append(a["id"])
    try:
        b = await inv.create(pp, d2)
        same = a["id"] == b["id"]
        if not same:
            created.append(b["id"])
        note(
            "10",
            "PASS Request-Id honoured (same invoice)" if same else "FAIL second invoice created",
            f"{a['id']} / {b['id']}",
        )
    except PayPalError as e:
        dup = inv.is_duplicate_number(e)
        why = " (repeat = duplicate number)" if dup else ""
        note("10", "FAIL Request-Id NOT honoured" + why, f"{e.status} {e.name}")
        save("create_same_request_id_error", err_body(e))

    # #11: existing invoice_number, new Request-Id
    d3 = draft(paypal_invoice_number=d2.paypal_invoice_number)
    try:
        c = await inv.create(pp, d3)
        created.append(c["id"])
        note("11", "FAIL duplicate number accepted", c["id"])
    except PayPalError as e:
        issues = [d.get("issue") for d in e.details if isinstance(d, dict)]
        note("11", f"duplicate number -> name={e.name} issues={issues}", f"HTTP {e.status}")
        save("create_duplicate_number_error", err_body(e))

    # search by number (D4 safety net)
    found = await inv.search_by_number(pp, d2.paypal_invoice_number)
    note(
        "search",
        "found by invoice_number" if found and found.get("id") == a["id"] else "NOT found",
        "",
    )
    if found:
        save("search_invoice_item", found)

    # R12: does a reused send Request-Id replay a cached failure?
    key_uid = str(uuid.uuid4())
    try:
        await inv.send(pp, "INV2-AAAA-BBBB-CCCC-DDDD", key_uid)
    except PayPalError as e:
        save("send_not_found_error", err_body(e))
    try:
        await inv.send(pp, a["id"], key_uid)
        note("R12", "no cached failure: same key worked on a valid invoice", "")
    except PayPalError as e:
        note(
            "R12",
            "cached failure replayed -> use <env>-send-<uid>-<attempt>",
            f"{e.status} {e.name}",
        )

    # #6 / #9: record $50 then $0.05 on the first invoice
    rec_uid = str(uuid.uuid4())
    r50 = await inv.record_payment(
        pp,
        inv_id,
        allocation_uid=rec_uid,
        amount_cents=5000,
        method="PAYPAL",
        payment_date=D,
        note=f"Memo-to-Cash open check [MTC:{rec_uid}]",
    )
    save("record_payment", r50)
    note(
        "6",
        "transaction id in response" if r50.get("payment_id") else "no id in response",
        f"keys={sorted(r50)}",
    )
    tiny_uid = str(uuid.uuid4())
    try:
        await inv.record_payment(
            pp,
            inv_id,
            allocation_uid=tiny_uid,
            amount_cents=5,
            method="PAYPAL",
            payment_date=D,
            note=f"tiny [MTC:{tiny_uid}]",
        )
        note("9", "PASS $0.05 accepted", "")
    except PayPalError as e:
        note("9", f"FAIL $0.05 refused: {e.name}", e.message)
        save("record_tiny_error", err_body(e))
    after = await inv.get(pp, inv_id)
    save("get_invoice_partially_paid", after)
    txs = (after.get("payments") or {}).get("transactions") or []
    token_found = any(f"[MTC:{rec_uid}]" in (t.get("note") or "") for t in txs)
    note(
        "mtc",
        "note token visible in payments.transactions" if token_found else "note token NOT found",
        f"status={after.get('status')} due={(after.get('due_amount') or {}).get('value')}",
    )

    # #1: PUT a -$20 line on the part-paid invoice (strip rule, CLAUDE.md §4)
    body = {
        k: v
        for k, v in after.items()
        if k not in ("amount", "due_amount", "payments", "refunds", "links", "status", "gratuity")
    }
    body["items"] = [
        *body.get("items", []),
        {"name": "Adjustment: scratch", "quantity": "1", "unit_amount": inv.money(-2000)},
    ]
    try:
        put = await pp.request(
            "PUT",
            f"{inv.BASE}/{inv_id}?send_to_recipient=false&send_to_invoicer=false",
            json=body,
            request_id=request_id(settings, "adj", uuid.uuid4()),
        )
        save("put_adjustment", put)
        g = await inv.get(pp, inv_id)  # PUT answers with a link only
        save("get_invoice_after_put", g)
        due_after = (g.get("due_amount") or {}).get("value")
        note(
            "1",
            "PASS PUT on part-paid" if due_after == "3429.95" else "CHECK PUT result",
            f"status={g.get('status')} due={due_after} (expect 3429.95); emails off via query",
        )
    except PayPalError as e:
        note("1", f"FAIL PUT on part-paid: {e.name}", e.message)
        save("put_adjustment_error", err_body(e))

    # clean up
    for i in created:
        try:
            await pp.request(
                "POST",
                f"{inv.BASE}/{i}/cancel",
                json={"send_to_recipient": False, "send_to_invoicer": False},
                request_id=request_id(settings, "cancel", uuid.uuid4()),
            )
        except PayPalError as e:
            print(f"(cancel {i} failed: {e.name} — ignored)")

    print("\n| Check | Result | Detail |\n|---|---|---|")
    for c, o, d in results:
        print(f"| {c} | {o} | {d} |")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
