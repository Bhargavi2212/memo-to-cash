"""A buyer's credit, one definition (P3 A6, G5): Σ unapplied payment money + Σ approved CREDIT
proposals. Used by the statement, the supplier overview and the agent's Facts, so the agent
can never quote a different credit than the page shows. Informational in v1: it doesn't lower
"You owe"; Demo Diamond Co. applies it to a future invoice or refunds it in PayPal.

`credit_from(payments, proposals)` is the rule (pure, on rows a caller already loaded — the
statement keeps its one-query-per-table budget); `buyer_credit_cents(session, id)` loads them.
"""

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.models import Payment, Proposal

LISTED = ("CAPTURED", "CAPTURE_PENDING", "RECORDED")


def credit_from(payments: Iterable[Payment], proposals: Iterable[Proposal]) -> int:
    unapplied = sum(p.unapplied_cents for p in payments if p.status in LISTED)
    credited = sum(
        int(p.payload.get("credit_cents") or 0)
        for p in proposals
        if p.kind == "ADJUSTMENT" and p.status == "DONE" and p.payload.get("mode") == "CREDIT"
    )
    return unapplied + credited


def buyer_credit_cents(session: Session, buyer_id: int) -> int:
    payments = session.scalars(select(Payment).where(Payment.buyer_id == buyer_id))
    proposals = session.scalars(select(Proposal).where(Proposal.buyer_id == buyer_id))
    return credit_from(list(payments), list(proposals))
