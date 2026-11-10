"""P2 smoke against the REAL PayPal sandbox (P2 D22, plan step 5). Two parts, because a
person approves the order in a browser in between:

    python -m scripts.sandbox.p2_smoke create [paypal|card]   # demo copy + $5,000 order
    (approve it in the browser as the sandbox buyer — or pay by card for `card`)
    python -m scripts.sandbox.p2_smoke finish                 # [decline test] + capture + record

Calls `orders.py` and `invoices.record_payment` directly — no payments service (plan step 5).
Answers open checks 12 (capture shape) and 13 (items in PayPal's window: you look), and R2
(does a reused capture Request-Id replay a decline? — via PayPal's sandbox negative-testing
header `PayPal-Mock-Response`, found through APIMatic ContextMatic). Saves redacted fixtures.
Prints ids and amounts only — never tokens, secrets or emails.
"""

import asyncio
import json
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import select

from backend.core.config import settings
from backend.core.dates import today_ny
from backend.core.money import to_cents
from backend.core.split import OpenInvoice, split_payment
from backend.db.models import Invoice, Memo
from backend.db.session import SessionLocal
from backend.paypal import invoices as pinv
from backend.paypal import orders
from backend.paypal.client import PayPal, PayPalError
from backend.services import demo
from scripts.sandbox.p1_open_checks import save

STATE = Path(tempfile.gettempdir()) / "mtc_p2_smoke_state.json"
AMOUNT = 500_000  # the demo story: $5,000 across INV-1038 + INV-1042


def _blank_ids(body: Any) -> Any:
    """Extra redaction for Orders bodies: payer/account ids (emails/names handled by save)."""
    if isinstance(body, dict):
        return {
            k: ("REDACTED" if k in ("payer_id", "account_id", "id_token") else _blank_ids(v))
            for k, v in body.items()
        }
    if isinstance(body, list):
        return [_blank_ids(v) for v in body]
    return body


def fixture(name: str, body: Any) -> None:
    save(name, _blank_ids(body))


async def create(funding: str) -> int:
    started = await demo.start_demo(settings, PayPal(settings), SessionLocal, ip="p2-smoke")
    with SessionLocal() as s:
        rows = s.scalars(
            select(Invoice).where(Invoice.buyer_id == started.buyer_id).order_by(Invoice.due_on)
        ).all()
        memo_no = dict(s.execute(select(Memo.id, Memo.memo_no)).all())
        open_ = [
            OpenInvoice(i.id, i.due_on, i.issued_on, i.total_cents - i.paid_cents) for i in rows
        ]
        split = split_payment(AMOUNT, open_, [i.id for i in rows])
        by_id = {i.id: i for i in rows}
        lines = [
            orders.OrderLine(by_id[i].erp_invoice_no, memo_no[by_id[i].memo_id], c)
            for i, c in split
        ]
        plan = [
            {
                "invoice_no": by_id[i].erp_invoice_no,
                "paypal_invoice_id": by_id[i].paypal_invoice_id,
                "amount_cents": c,
                "ours_due_before": by_id[i].total_cents - by_id[i].paid_cents,
            }
            for i, c in split
        ]
    uid = str(uuid.uuid4())
    order = await orders.create(PayPal(settings), uid, lines, funding)
    fixture(f"create_order_{funding}", order)
    STATE.write_text(
        json.dumps({"order_id": order["id"], "uid": uid, "funding": funding, "plan": plan}),
        encoding="utf-8",
    )
    print(f"demo buyer {started.buyer_id} ready in {started.ms} ms")
    print(f"order {order['id']} status={order.get('status')} funding={funding}")
    print("split:", ", ".join(f"{p['invoice_no']} {p['amount_cents'] / 100:,.2f}" for p in plan))
    print(f"\nAPPROVE HERE:\n{orders.approve_link(order)}\n")
    print("Then run: python -m scripts.sandbox.p2_smoke finish")
    return 0


async def finish() -> int:
    st = json.loads(STATE.read_text(encoding="utf-8"))
    pp = PayPal(settings)
    order_id, uid = st["order_id"], st["uid"]
    before = await orders.get(pp, order_id)
    print(f"order {order_id} status before capture = {before.get('status')}")

    # R2: simulate a decline with sandbox negative testing, then reuse the same Request-Id.
    key = orders.capture_request_id(settings, uid, 0)
    decline_seen = False
    try:
        await pp.request(
            "POST",
            f"{orders.BASE}/{order_id}/capture",
            json={},
            request_id=key,
            headers={
                "Prefer": "return=representation",
                "PayPal-Mock-Response": json.dumps(
                    {"mock_application_codes": "INSTRUMENT_DECLINED"}
                ),
            },
            retries=0,
        )
        print("[R2] mock decline was NOT applied (capture went through) — skipping R2")
    except PayPalError as e:
        decline_seen = True
        print(f"[R2] mock decline -> {e.status} {e.name} issues={sorted(orders.issues(e))}")
        fixture(
            "capture_declined_error",
            {"name": e.name, "message": e.message, "debug_id": "REDACTED", "details": e.details},
        )

    captured = None
    if decline_seen:
        try:
            captured = await pp.request(
                "POST",
                f"{orders.BASE}/{order_id}/capture",
                json={},
                request_id=key,
                headers={"Prefer": "return=representation"},
                retries=0,
            )
            print(
                "[R2] same Request-Id after a decline -> captured: PayPal does NOT replay declines"
            )
        except PayPalError as e:
            print(
                f"[R2] same Request-Id after a decline -> {e.name} {sorted(orders.issues(e))}: "
                "PayPal REPLAYS the decline -> the -<n> suffix (R2) is required"
            )
    if captured is None:
        captured = await orders.capture(pp, order_id, uid, 1 if decline_seen else 0)

    fixture(f"capture_order_{st['funding']}", captured)
    cap = orders.first_capture(captured)
    print(
        f"\n[12] order status={captured.get('status')}; captures[0] = "
        f"id={'yes' if cap and cap.get('id') else 'MISSING'} status={cap and cap.get('status')} "
        f"amount={cap and cap.get('amount')} custom_id_ok={cap and cap.get('custom_id') == uid}"
    )
    if not cap or cap.get("status") != "COMPLETED":
        print("capture not COMPLETED — stop (nothing recorded)")
        return 1

    # record each allocation on its PayPal invoice (what services/payments will do)
    for p in st["plan"]:
        alloc_uid = str(uuid.uuid4())
        rec = await pinv.record_payment(
            pp,
            p["paypal_invoice_id"],
            allocation_uid=alloc_uid,
            amount_cents=p["amount_cents"],
            method="PAYPAL",
            payment_date=today_ny(),
            note=f"[MTC:{alloc_uid}] Statement payment · PayPal capture {cap['id']}",
        )
        got = await pinv.get(pp, p["paypal_invoice_id"])
        pp_due = to_cents(got["due_amount"]["value"])
        ours = p["ours_due_before"] - p["amount_cents"]
        flag = "OK" if pp_due == ours else "MISMATCH"
        print(
            f"{p['invoice_no']}: recorded {p['amount_cents'] / 100:,.2f} (txn id "
            f"{'yes' if rec.get('payment_id') else 'no'}) -> PayPal {got.get('status')} due "
            f"{pp_due / 100:,.2f} vs ours {ours / 100:,.2f} {flag}"
        )
        fixture(f"get_invoice_after_statement_payment_{p['invoice_no'].lower()}", got)
    after = await orders.get(pp, order_id)
    fixture("get_order_completed", after)
    print(f"order status after = {after.get('status')}")
    return 0


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode == "create":
        sys.exit(asyncio.run(create(sys.argv[2] if len(sys.argv) > 2 else "paypal")))
    if mode == "finish":
        sys.exit(asyncio.run(finish()))
    print(__doc__)
    sys.exit(2)
