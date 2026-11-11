"""P4 step 5b done-when: one real approve against the PayPal sandbox, on the dev database.

    python -m scripts.sandbox.p4_approve_check

Starts a demo copy (real sandbox invoices, real triage of the seed message — one LLM call),
approves its −$20.00 S-120 proposal through `services.proposals.decide` (PayPal invoice PUT),
then GETs the invoice and compares PayPal's due with ours. Then approves again (must be a
no-op). Prints ids and amounts only — never tokens, secrets or emails.
"""

import asyncio

from sqlalchemy import select

from backend.core.config import settings
from backend.core.money import to_cents
from backend.db.models import Invoice, Proposal
from backend.db.session import SessionLocal
from backend.paypal import invoices as pinv
from backend.paypal.client import PayPal
from backend.services import demo, proposals


async def main() -> None:
    assert settings.paypal_base_url == "https://api-m.sandbox.paypal.com"
    pp = PayPal(settings)
    started = await demo.start_demo(settings, pp, SessionLocal, ip="p4-approve-check")
    print(f"demo copy: buyer {started.buyer_id}, started in {started.ms} ms")
    with SessionLocal() as s:
        prop = s.scalars(select(Proposal).where(Proposal.buyer_id == started.buyer_id)).one()
        inv = s.get(Invoice, prop.payload["invoice_id"])
        assert inv is not None
        print(f"proposal {prop.id}: {prop.kind} {prop.payload['amount_cents']} cents on "
              f"{inv.erp_invoice_no}, source={prop.evidence['source']}")  # fmt: skip
        print(f"before: ours total {inv.total_cents} paid {inv.paid_cents}; "
              f"PayPal {inv.paypal_invoice_id}")  # fmt: skip
        prop_id, inv_id, paypal_id = prop.id, inv.id, inv.paypal_invoice_id
    assert paypal_id

    out = await proposals.decide(SessionLocal, pp, prop_id, "APPROVE")
    print(f"approve → {out['status']} {out['result']}")
    body = await pinv.get(pp, paypal_id)
    tagged = [i for i in body.get("items", []) if "[MTC-ADJ:" in (i.get("description") or "")]
    print(f"PayPal after: status {body.get('status')}, amount {body['amount']['value']}, "
          f"due {body['due_amount']['value']}, adjustment lines {len(tagged)} "
          f"({[i['unit_amount']['value'] for i in tagged]})")  # fmt: skip
    with SessionLocal() as s:
        inv = s.get(Invoice, inv_id)
        assert inv is not None
        ours = inv.total_cents - inv.paid_cents
        print(f"ours after: total {inv.total_cents} due {ours} ledger {inv.ledger_status}; "
              f"paypal_due_cents {inv.paypal_due_cents} mismatch {inv.mismatch!r}")  # fmt: skip
        print("PayPal due == ours:", to_cents(body["due_amount"]["value"]) == ours)

    again = await proposals.decide(SessionLocal, pp, prop_id, "APPROVE")
    body2 = await pinv.get(pp, paypal_id)
    tagged2 = [i for i in body2.get("items", []) if "[MTC-ADJ:" in (i.get("description") or "")]
    print(f"approve again → {again['status']}; adjustment lines still {len(tagged2)}, "
          f"due {body2['due_amount']['value']}")  # fmt: skip


if __name__ == "__main__":
    asyncio.run(main())
