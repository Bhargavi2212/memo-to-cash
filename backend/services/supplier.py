"""Supplier read side (P3a A1–A3, A5; FR-11): who may see what, the overview, a buyer's page and
the proposal cards. Ledger only — no PayPal calls (fast; A2).

Scope (A1, invariant 7): the admin token sees and acts on any buyer, but its **lists** leave
demo copies out (R2); a demo cookie sees only its own session's ready copies. Anything out of
scope is "not found" (404), never "forbidden" — no existence leak.
"""

import secrets
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any

from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from backend.agent.facts import proposal_sku
from backend.agent.triage import summary
from backend.core.config import Settings
from backend.core.dates import NY, ny_date
from backend.db.models import (
    Allocation,
    AuditLog,
    Buyer,
    Invoice,
    InvoiceLine,
    Message,
    Payment,
    Proposal,
)
from backend.services import demo
from backend.services import statement as stmt


class ScopeError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


class NotFoundError(Exception):
    pass


@dataclass(frozen=True)
class Scope:
    kind: str  # "admin" | "demo"
    buyer_ids: frozenset[int] | None = None  # demo: its copies; admin: None (acts on any id)

    def may_see(self, buyer_id: int) -> bool:
        return self.buyer_ids is None or buyer_id in self.buyer_ids


def resolve_scope(
    session: Session, settings: Settings, authorization: str | None, cookie: str | None
) -> Scope:
    """Admin = `Authorization: Bearer ADMIN_TOKEN`; else, in DEMO_MODE, the `mtc_demo` cookie."""
    if authorization and settings.admin_token is not None:
        expected = f"Bearer {settings.admin_token.get_secret_value()}"
        if secrets.compare_digest(authorization.encode(), expected.encode()):
            return Scope("admin")
    if settings.demo_mode:
        sess = demo.read_demo_cookie(cookie, settings)
        if sess:
            ids = session.scalars(
                select(Buyer.id).where(Buyer.demo_session == sess, Buyer.demo_ready.is_(True))
            ).all()
            return Scope("demo", frozenset(ids))
    if authorization and settings.admin_token is None:  # an admin call with no token configured
        raise ScopeError(503, "ADMIN_TOKEN_UNSET", "Supplier API unavailable: ADMIN_TOKEN not set")
    raise ScopeError(401, "UNAUTHORIZED", "Supplier access needs the admin token or a demo copy")


def listed_buyers(session: Session, scope: Scope) -> list[Buyer]:
    """Buyers a list shows: the demo copy for a demo cookie; real (non-demo) buyers for admin."""
    q = select(Buyer).order_by(Buyer.name, Buyer.id)
    if scope.buyer_ids is None:
        q = q.where(Buyer.is_demo.is_(False))
    else:
        q = q.where(Buyer.id.in_(scope.buyer_ids or {-1}))
    return list(session.scalars(q))


def buyer_in_scope(session: Session, scope: Scope, buyer_id: int) -> Buyer:
    b = session.get(Buyer, buyer_id)
    if b is None or not scope.may_see(b.id) or (b.is_demo and not b.demo_ready):
        raise NotFoundError
    return b


# ---------------------------------------------------------------- proposal cards (A5)


class ProposalCard(BaseModel):
    id: int
    kind: str
    status: str
    source: str  # agent | seed | fallback
    buyer: dict[str, Any]  # {id, name}
    summary: str
    checks: list[str]
    message_text: str | None
    payload: dict[str, Any]
    due_cents: int | None  # the invoice's current due (ADJUSTMENT)
    suggested: str | None  # APPLY | CREDIT — a label only; the mode is decided at approve (A5)
    result: dict[str, Any] | None
    invoice_no: str | None
    paypal_url: str | None


def _source(p: Proposal) -> str:
    if p.evidence.get("fallback") or "fallback_day" in p.payload:
        return "fallback"
    src = p.evidence.get("source")
    if src in ("system", "return_check"):  # made by our code (D23 note, P3b return check)
        return "system"
    return "seed" if src == "seed" else "agent"


def _reminder_state(session: Session, inv: Invoice, today: date) -> str | None:
    """P3b F10 for the buyer page: AVAILABLE (show "Draft a reminder"), PENDING (one is
    waiting), or None (not past due / a promise stands → no button)."""
    from backend.services import reminders

    why = reminders.eligible(session, inv, today)
    return "AVAILABLE" if why is None else ("PENDING" if why == "REMINDER_PENDING" else None)


def _card_summary(p: Proposal, inv: Invoice | None) -> str | None:
    """Supplier-only wording where the buyer-facing `summary()` doesn't fit: a reminder, and a
    system note (P3b X6 — "internal" hides it from the buyer, not from the supplier)."""
    if p.kind == "REMINDER":
        return f"Payment reminder for {inv.erp_invoice_no if inv else 'an invoice'}"
    if p.kind == "REPLY" and p.evidence.get("source") == "system" and p.payload.get("reason"):
        return str(p.payload["reason"])
    return None


def cards(session: Session, proposals: list[Proposal]) -> list[ProposalCard]:
    if not proposals:
        return []
    buyers = {b.id: b for b in session.scalars(
        select(Buyer).where(Buyer.id.in_({p.buyer_id for p in proposals})))}  # fmt: skip
    inv_ids = {p.payload.get("invoice_id") for p in proposals if p.payload.get("invoice_id")}
    invoices = (
        {i.id: i for i in session.scalars(select(Invoice).where(Invoice.id.in_(inv_ids)))}
        if inv_ids
        else {}
    )
    msg_ids = {p.source_message_id for p in proposals if p.source_message_id}
    texts = (
        dict(session.execute(select(Message.id, Message.text).where(Message.id.in_(msg_ids))).all())
        if msg_ids
        else {}
    )
    out = []
    for p in proposals:
        pl = p.payload
        inv = invoices.get(pl.get("invoice_id"))
        due = inv.total_cents - inv.paid_cents if inv else None
        amount = abs(pl["amount_cents"]) if isinstance(pl.get("amount_cents"), int) else None
        suggested = None
        if p.kind == "ADJUSTMENT" and due is not None and amount is not None:
            suggested = "APPLY" if due >= amount else "CREDIT"
        out.append(
            ProposalCard(
                id=p.id,
                kind=p.kind,
                status=p.status,
                source=_source(p),
                buyer={"id": p.buyer_id, "name": buyers[p.buyer_id].name},
                summary=_card_summary(p, inv)
                or summary(
                    p.kind,
                    invoice_no=inv.erp_invoice_no if inv else None,
                    sku=proposal_sku(p),
                    amount_cents=pl.get("amount_cents"),
                    promise_date=pl.get("promise_date"),
                    reason=None if p.evidence.get("internal") else pl.get("reason"),
                    fallback="fallback_day" in pl,
                ),
                checks=[str(c) for c in p.evidence.get("checks", [])],
                message_text=texts.get(p.source_message_id),
                payload=pl,
                due_cents=due,
                suggested=suggested,
                result=p.result,
                invoice_no=inv.erp_invoice_no if inv else None,
                paypal_url=inv.payer_view_url if inv else None,
            )
        )
    return out


def proposal_cards(session: Session, scope: Scope, statuses: list[str]) -> list[ProposalCard]:
    ids = [b.id for b in listed_buyers(session, scope)]
    rows = list(
        session.scalars(
            select(Proposal)
            .where(Proposal.buyer_id.in_(ids or [-1]), Proposal.status.in_(statuses))
            .order_by(Proposal.id.desc())
        )
    )
    return cards(session, rows)


# ---------------------------------------------------------------- overview (A2)


def week_start(today: date) -> datetime:
    """Monday 00:00 New York of today's ISO week (G10)."""
    monday = today - timedelta(days=today.weekday())
    return datetime.combine(monday, time(0), tzinfo=NY)


def _recorded_all(session: Session, payment_ids: list[int]) -> dict[int, bool]:
    states: dict[int, bool] = {pid: True for pid in payment_ids}
    for pid, state in session.execute(
        select(Allocation.payment_id, Allocation.record_state).where(
            Allocation.payment_id.in_(payment_ids or [-1])
        )
    ):
        states[pid] = states[pid] and state == "RECORDED"
    return states


def overview(session: Session, scope: Scope, today: date, settings: Settings) -> dict[str, Any]:
    buyers, recent = [], []
    totals = {"open_cents": 0, "past_due_cents": 0, "paid_this_week_cents": 0}
    since = week_start(today)
    for b in listed_buyers(session, scope):
        s = stmt.get_statement(session, b.statement_token, today, settings)
        lines = [ln for m in s.memos for ln in m.lines]
        promise = session.scalars(
            select(Proposal)
            .where(
                Proposal.buyer_id == b.id,
                Proposal.kind == "PROMISE_NOTE",
                Proposal.status.in_(("PENDING", "APPROVED", "DONE")),
            )
            .order_by(Proposal.id.desc())
        ).first()
        promised = promise.payload.get("promise_date") if promise else None
        pays = [p for p in s.payments if p.status in stmt.LISTED]
        last = max(pays, key=lambda p: (p.received_at or date.min, p.id), default=None)
        buyers.append({
            "id": b.id,
            "name": b.name,
            "out": sum(ln.status == "OUT" for ln in lines),
            "kept": sum(ln.status == "KEPT" for ln in lines),
            "returned": sum(ln.status == "RETURNED" for ln in lines),
            "owed_cents": s.owed_cents,
            "oldest_age_days": s.oldest_age_days,
            "past_due_cents": s.past_due_cents,
            "paypal_setup": s.paypal_setup,
            "credit_cents": s.credit_cents,
            "last_payment": (
                {"amount_cents": last.amount_cents, "received_at": last.received_at}
                if last else None
            ),
            "pending_proposals": s.pending_proposals,
            "promise": promised if promised and promised >= today.isoformat() else None,
        })  # fmt: skip
        totals["open_cents"] += s.owed_cents
        totals["past_due_cents"] += s.past_due_cents
        rows = list(
            session.scalars(
                select(Payment).where(Payment.buyer_id == b.id, Payment.status.in_(stmt.LISTED))
            )
        )
        totals["paid_this_week_cents"] += sum(
            p.amount_cents - p.unapplied_cents
            for p in rows
            if p.received_at and p.received_at >= since
        )
        recorded = _recorded_all(session, [p.id for p in pays])
        for p in pays:
            recent.append({
                "id": p.id,
                "buyer": {"id": b.id, "name": b.name},
                "amount_cents": p.amount_cents,
                "source": p.source,
                "method": p.method,
                "received_at": p.received_at,
                "split": [a.model_dump() for a in p.applied],
                "recorded_all": recorded.get(p.id, True),
            })  # fmt: skip
    recent.sort(key=lambda r: (r["received_at"] or date.min, r["id"]), reverse=True)
    return {
        "totals": totals,
        # largest owed first, then name: Northside, Lakeside, Harbor in a demo copy (PM-plan G8)
        "buyers": sorted(buyers, key=lambda r: (-r["owed_cents"], r["name"])),
        "pending": [c.model_dump() for c in proposal_cards(session, scope, ["PENDING", "FAILED"])],
        "recent_payments": recent[:20],
    }


# ---------------------------------------------------------------- buyer detail (A3)


def buyer_detail(
    session: Session, scope: Scope, buyer_id: int, today: date, settings: Settings
) -> dict[str, Any]:
    b = buyer_in_scope(session, scope, buyer_id)
    s = stmt.get_statement(session, b.statement_token, today, settings)
    invs = {i.id: i for i in session.scalars(select(Invoice).where(Invoice.buyer_id == b.id))}
    adj: dict[int, list[dict[str, Any]]] = {}
    for ln in session.scalars(
        select(InvoiceLine).where(
            InvoiceLine.invoice_id.in_(list(invs) or [-1]), InvoiceLine.memo_line_id.is_(None)
        )
    ):
        adj.setdefault(ln.invoice_id, []).append({"name": ln.name, "amount_cents": ln.amount_cents})
    invoices = []
    for i in s.invoices:
        row = invs[i.id]
        invoices.append(i.model_dump() | {
            "paypal_status": row.paypal_status,
            "paypal_due_cents": row.paypal_due_cents,
            "paypal_synced_at": row.paypal_synced_at,
            "mismatch": row.mismatch,
            "reminder": _reminder_state(session, invs[i.id], today),
            "adjustments": adj.get(i.id, []),
        })  # fmt: skip
    msgs = session.scalars(
        select(Message).where(Message.buyer_id == b.id).order_by(Message.id.desc())
    )
    messages = [
        {
            "id": m.id,
            "kind": m.kind,
            "text": m.text,
            "label": ((m.ai_result or {}).get("triage") or {}).get("label"),
            "created_at": ny_date(m.created_at),
        }
        for m in msgs
    ]
    props = list(
        session.scalars(
            select(Proposal).where(Proposal.buyer_id == b.id).order_by(Proposal.id.desc())
        )
    )
    pay_rows = list(
        session.scalars(select(Payment).where(Payment.buyer_id == b.id).order_by(Payment.id.desc()))
    )
    allocs: dict[int, list[dict[str, Any]]] = {}
    for a in session.scalars(
        select(Allocation).where(Allocation.payment_id.in_([p.id for p in pay_rows] or [-1]))
    ):
        allocs.setdefault(a.payment_id, []).append({
            "invoice_no": invs[a.invoice_id].erp_invoice_no if a.invoice_id in invs else "",
            "amount_cents": a.amount_cents,
            "record_state": a.record_state,
            "error": a.paypal_record_error,
        })  # fmt: skip
    payments = [
        {
            "id": p.id,
            "source": p.source,
            "status": p.status,
            "method": p.method,
            "reference": p.reference,
            "amount_cents": p.amount_cents,
            "unapplied_cents": p.unapplied_cents,
            "received_at": ny_date(p.received_at) if p.received_at else None,
            "allocations": allocs.get(p.id, []),
        }
        for p in pay_rows
        if p.status in stmt.LISTED
    ]
    return {
        "buyer": {"id": b.id, "name": b.name, "is_demo": b.is_demo},
        "statement_url": f"{settings.app_base_url or ''}/s/{b.statement_token}",
        "statement_token": b.statement_token,
        "owed_cents": s.owed_cents,
        "past_due_cents": s.past_due_cents,
        "oldest_age_days": s.oldest_age_days,
        "credit_cents": s.credit_cents,
        "paypal_setup": s.paypal_setup,
        "memos": [m.model_dump() for m in s.memos],
        "invoices": invoices,
        "messages": messages,
        "proposals": [c.model_dump() for c in cards(session, props)],
        "payments": payments,
    }


def whoami(session: Session, settings: Settings, cookie: str | None) -> dict[str, Any]:
    """The cookie's own ready copy (A10, R10): `statement_token` = its Northside buyer (landing /
    back link, PM-plan G3), `buyers` = all three with their tokens (P3 §M M4). Else empty."""
    none: dict[str, Any] = {"statement_token": None, "buyers": []}
    if not settings.demo_mode:
        return none
    sess = demo.read_demo_cookie(cookie, settings)
    if not sess:
        return none
    rows = session.execute(
        select(Buyer.id, Buyer.name, Buyer.statement_token)
        .where(Buyer.demo_session == sess, Buyer.demo_ready.is_(True))
        .order_by(Buyer.name != demo.BUYER_NAME, Buyer.name)  # Northside first
    ).all()
    buyers = [{"id": i, "name": n, "statement_token": t} for i, n, t in rows]
    north = next((b["statement_token"] for b in buyers if b["name"] == demo.BUYER_NAME), None)
    return {"statement_token": north, "buyers": buyers}


def payment_summary(session: Session, payment_id: int) -> dict[str, Any]:
    """A payment and its split, as the record modal's confirmation shows it."""
    p = session.get(Payment, payment_id)
    assert p is not None
    rows = session.execute(
        select(Allocation, Invoice.erp_invoice_no)
        .join(Invoice, Allocation.invoice_id == Invoice.id)
        .where(Allocation.payment_id == p.id)
        .order_by(Allocation.id)
    ).all()
    return {
        "payment_id": p.id,
        "source": p.source,
        "status": p.status,
        "method": p.method,
        "reference": p.reference,
        "amount_cents": p.amount_cents,
        "received_at": ny_date(p.received_at) if p.received_at else None,
        "allocations": [
            {"invoice_no": no, "amount_cents": a.amount_cents, "record_state": a.record_state}
            for a, no in rows
        ],
    }


# ---------------------------------------------------------------- payment detail (A4)


def payment_detail(session: Session, scope: Scope, payment_id: int) -> dict[str, Any]:
    """A payment, its split with the stored PayPal values and the audit timeline (A4, P3a).
    No PayPal call here — the live fetch is P3b; our amounts are never overwritten."""
    p = session.get(Payment, payment_id)
    if p is None:
        raise NotFoundError
    b = buyer_in_scope(session, scope, p.buyer_id)
    rows = session.execute(
        select(Allocation, Invoice)
        .join(Invoice, Allocation.invoice_id == Invoice.id)
        .where(Allocation.payment_id == p.id)
        .order_by(Allocation.id)
    ).all()
    alloc_uids = [str(a.uid) for a, _ in rows]
    events = session.scalars(
        select(AuditLog)
        .where(
            or_(
                AuditLog.ref.in_([str(p.uid), *alloc_uids]),
                AuditLog.detail["payment_id"].as_integer() == p.id,
            )
        )
        .order_by(AuditLog.at, AuditLog.id)
    )
    no_of = {str(a.uid): inv.erp_invoice_no for a, inv in rows}
    timeline = [
        {
            "at": e.at,
            "actor": e.actor,
            "action": e.action,
            "invoice_no": no_of.get(e.ref or ""),
        }
        for e in events
    ]
    return {
        "payment": {
            "id": p.id,
            "source": p.source,
            "status": p.status,
            "method": p.method,
            "reference": p.reference,
            "amount_cents": p.amount_cents,
            "unapplied_cents": p.unapplied_cents,
            "received_at": ny_date(p.received_at) if p.received_at else None,
            "paypal_order_id": p.paypal_order_id,
            "paypal_capture_id": p.paypal_capture_id,
        },
        "buyer": {"id": b.id, "name": b.name},
        "allocations": [
            {
                "invoice_id": inv.id,
                "invoice_no": inv.erp_invoice_no,
                "amount_cents": a.amount_cents,
                "record_state": a.record_state,
                "paypal_transaction_id": a.paypal_transaction_id,
                "error": a.paypal_record_error,
                "paypal_status": inv.paypal_status,
                "paypal_due_cents": inv.paypal_due_cents,
                "paypal_synced_at": inv.paypal_synced_at,
                "mismatch": inv.mismatch,
                "paypal_url": inv.payer_view_url,
            }
            for a, inv in rows
        ],
        "timeline": timeline,
    }
