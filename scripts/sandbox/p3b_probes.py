"""P3b probes against the REAL PayPal sandbox (P3b plan §4; CLAUDE.md §4 gets the results).

    python -m scripts.sandbox.p3b_probes remind   # step 2: remind body, response, 3rd in a day
    python -m scripts.sandbox.p3b_probes split    # step 5: PUT that brings a part-paid due to $0

Creates its own invoices (numbers INV-9xxx-…), prints a results table only — never tokens,
secrets or emails — and saves redacted responses to tests/fixtures/paypal/.
"""

import asyncio
import sys
import uuid

from backend.core.config import settings
from backend.paypal import invoices as inv
from backend.paypal.client import PayPal, PayPalError, request_id
from scripts.sandbox.p1_open_checks import draft, err_body, note, results, save, timed

REMIND_NOTE = "A friendly reminder from Demo Diamond Co.: INV-9001 ($3,500.00) is 18 days past due."


def remind_body(note_text: str) -> dict:
    """Notification body (PayPal Invoicing v2 docs): reminder to the recipient only."""
    return {
        "subject": "Payment reminder",
        "note": note_text,
        "send_to_invoicer": False,
        "send_to_recipient": True,
    }


async def remind(pp: PayPal) -> int:
    d = draft()
    body = await inv.create(pp, d)
    inv_id = body["id"]
    await inv.send(pp, inv_id, d.uid)
    note("setup", "created + sent a past-due invoice", inv_id[:9] + "…")
    for n in (1, 2, 3):
        rid = request_id(pp.settings, "rem", d.uid, n)
        try:
            out, ms = await timed(
                pp.request(
                    "POST",
                    f"/v2/invoicing/invoices/{inv_id}/remind",
                    json=remind_body(REMIND_NOTE),
                    request_id=rid,
                )
            )
            save(f"remind_ok_{n}", out or {"empty": True})
            note(
                f"remind {n}", "accepted", f"{ms} ms; body keys={sorted(out) if out else '(empty)'}"
            )
        except PayPalError as e:
            save(f"remind_error_{n}", err_body(e))
            issues = [d.get("issue") for d in e.details]
            note(f"remind {n}", f"REFUSED {e.status} {e.name}", f"issues={issues}")
    got = await inv.get(pp, inv_id)
    note("after", f"status {got.get('status')}", "")
    return 0


async def split(pp: PayPal) -> int:
    """G15: on a part-paid invoice, does a PUT that brings PayPal's due to exactly $0.00 work?"""
    from backend.core.dates import today_ny

    d = draft()  # $3,500.00: S-101 $2,350 + S-104 $1,150
    body = await inv.create(pp, d)
    inv_id = body["id"]
    await inv.send(pp, inv_id, d.uid)
    uid = str(uuid.uuid4())
    await inv.record_payment(
        pp,
        inv_id,
        allocation_uid=uid,
        amount_cents=100000,
        method="OTHER",
        payment_date=today_ny(),
        note=f"[MTC:{uid}] probe part payment",
    )
    got = await inv.get(pp, inv_id)
    note("setup", f"part-paid: status {got.get('status')}", f"due {got['due_amount']['value']}")
    try:
        after, ms = await timed(
            inv.put_adjustment(
                pp,
                inv_id,
                proposal_uid=str(uuid.uuid4()),
                sku="S-101",
                amount_cents=-250000,
                reason="probe: deduct the whole due",
            )
        )
        save("split_put_to_zero", after)
        note(
            "PUT to $0.00",
            f"ACCEPTED status {after.get('status')}",
            f"due {after['due_amount']['value']}, total {after['amount']['value']}; {ms} ms",
        )
    except PayPalError as e:
        save("split_put_to_zero_error", err_body(e))
        note(
            "PUT to $0.00",
            f"REFUSED {e.status} {e.name}",
            f"issues={[x.get('issue') for x in e.details]}",
        )
    return 0


async def main() -> int:
    if not settings.sandbox_buyer_email:
        print("SANDBOX_BUYER_EMAIL is not set in .env — stop.")
        return 2
    pp = PayPal(settings)
    which = sys.argv[1] if len(sys.argv) > 1 else ""
    if which == "remind":
        code = await remind(pp)
    elif which == "split":
        code = await split(pp)
    else:
        print(__doc__)
        return 2
    print("\n| check | outcome | detail |\n|---|---|---|")
    for c, o, d in results:
        print(f"| {c} | {o} | {d} |")
    return code


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
