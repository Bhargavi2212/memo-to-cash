"""Job C reminders (FR-10; P3b B6, F9, F10, G11, G12, G14; APP_FLOW J7).

Drafting: the agent writes ONE reminder for ONE past-due invoice (one `json_schema` call, 7.5 s,
no retries); code validates it against the buyer's records like an answer (money and day counts
from the records only, no record ids, no unknown references) and that it names the invoice; a
failure creates nothing ("Couldn't draft — try again"). The draft becomes a PENDING REMINDER
proposal — AI output goes to proposals only (invariant 5). Approval (in `proposals.decide`)
checks PayPal's 2-a-day limit ourselves — the sandbox doesn't enforce it (CLAUDE §4) — and then
`POST /invoices/{id}/remind`.

Rules: invoice past due with money owed; no active promise to pay (a PROMISE_NOTE that isn't
rejected and whose date hasn't passed); at most one PENDING/APPROVED REMINDER per invoice.
"""

import logging
from collections.abc import Callable
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from backend.agent import caps, llm
from backend.agent.facts import facts_for_buyer
from backend.agent.schemas import AnswerResult, ReminderDraft
from backend.agent.tools import usd
from backend.agent.validate import check_answer
from backend.core.dates import days_overdue, ny_date, today_ny
from backend.db.models import Buyer, Invoice, Proposal, ReminderSent
from backend.services import audit

log = logging.getLogger("reminders")

PER_DAY = 2  # PayPal's limit per invoice (TRD §5), counted in America/New_York


class ReminderError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def promise_active(s: Session, buyer_id: int, today: date) -> bool:
    for p in s.scalars(
        select(Proposal).where(
            Proposal.buyer_id == buyer_id,
            Proposal.kind == "PROMISE_NOTE",
            Proposal.status.in_(("PENDING", "APPROVED", "DONE")),
        )
    ):
        when = str((p.payload or {}).get("promise_date") or "")
        if not when or when >= today.isoformat():
            return True
    return False


def reminder_open(s: Session, invoice_id: int) -> bool:
    return any(
        (p.payload or {}).get("invoice_id") == invoice_id
        for p in s.scalars(
            select(Proposal).where(
                Proposal.kind == "REMINDER", Proposal.status.in_(("PENDING", "APPROVED"))
            )
        )
    )


def eligible(s: Session, inv: Invoice, today: date) -> str | None:
    """None if a reminder may be drafted for this invoice, else the reason code (F10)."""
    if inv.total_cents - inv.paid_cents <= 0 or inv.due_on >= today:
        return "NOT_PAST_DUE"
    if promise_active(s, inv.buyer_id, today):
        return "PROMISE_ACTIVE"
    if reminder_open(s, inv.id):
        return "REMINDER_PENDING"
    return None


def sent_today(s: Session, invoice_id: int, today: date) -> int:
    return sum(
        1
        for t in s.scalars(
            select(ReminderSent.sent_at).where(ReminderSent.invoice_id == invoice_id)
        )
        if ny_date(t) == today
    )


def check_draft(d: ReminderDraft, facts: Any, invoice_no: str, today: date) -> list[str]:
    """The answer validator (money, days, references, record ids) + reminder rules."""
    problems = check_answer(
        AnswerResult(answer=d.text, cited_records=[], confidence="high", offer_escalation=False),
        facts,
    )
    if invoice_no.upper() not in d.text.upper():
        problems.append(f"doesn't name {invoice_no}")
    try:
        when = date.fromisoformat(d.send_after)
    except ValueError:
        problems.append("send_after isn't a date")
    else:
        if when < today:
            problems.append("send_after is in the past")
    return problems


_MESSAGES = {
    "NOT_PAST_DUE": (422, "This invoice isn't past due."),
    "PROMISE_ACTIVE": (422, "The buyer has promised to pay — no reminder while that stands."),
    "REMINDER_PENDING": (409, "A reminder for this invoice is already waiting for your decision."),
}


def _prepare(
    session_factory: Callable[[], Session], invoice_id: int, today: date
) -> dict[str, Any]:
    with session_factory() as s, s.begin():
        inv = s.get(Invoice, invoice_id)
        assert inv is not None
        if (why := eligible(s, inv, today)) is not None:
            status, msg = _MESSAGES[why]
            raise ReminderError(status, why, msg)
        buyer = s.get(Buyer, inv.buyer_id)
        assert buyer is not None
        if not caps.take_unit(s, today, buyer.statement_token):  # P4 limits (F9)
            raise ReminderError(
                429, "LIMIT_REACHED", "Drafting is paused for today — try tomorrow."
            )
        return {
            "invoice_no": inv.erp_invoice_no,
            "buyer_id": inv.buyer_id,
            "facts": facts_for_buyer(s, inv.buyer_id, today),
            "due_cents": inv.total_cents - inv.paid_cents,
            "days": days_overdue(inv.due_on, today),
        }


def _save(
    session_factory: Callable[[], Session],
    invoice_id: int,
    prep: dict[str, Any],
    d: ReminderDraft,
    today: date,
    actor: str,
) -> int:
    with session_factory() as s, s.begin():
        inv = s.get(Invoice, invoice_id)
        assert inv is not None
        s.execute(_lock(), {"id": inv.buyer_id})
        if reminder_open(s, inv.id):  # re-check under the lock: never two at once
            raise ReminderError(409, "REMINDER_PENDING", _MESSAGES["REMINDER_PENDING"][1])
        p = Proposal(
            buyer_id=inv.buyer_id,
            kind="REMINDER",
            payload={"invoice_id": inv.id, "text": d.text, "send_after": d.send_after},
            evidence={
                "source": "agent",
                "checks": [
                    f"{prep['invoice_no']} is {prep['days']} days past due",
                    f"{usd(prep['due_cents'])} due",
                    "every amount and day count is from the records",
                ],
                "records": [f"invoice:{prep['invoice_no']}"],
            },
        )
        s.add(p)
        s.flush()
        audit.log(s, actor, "reminder.drafted", ref=str(p.id), detail={"invoice_id": inv.id})
        return p.id


def _lock() -> Any:
    from sqlalchemy import text

    return text("SELECT pg_advisory_xact_lock(:id)")


async def draft(
    session_factory: Callable[[], Session],
    invoice_id: int,
    *,
    client: Any = None,
    today: date | None = None,
    actor: str = "agent",
) -> int:
    """Draft a reminder for one invoice → the new PENDING REMINDER proposal's id."""
    day = today or today_ny()
    prep = await run_in_threadpool(_prepare, session_factory, invoice_id, day)
    res = await llm.acall(
        "reminder",
        prep["facts"],
        f"Draft a reminder for {prep['invoice_no']}. Today is {day.isoformat()}.",
        llm.Budget(),
        client=client,
    )
    if not res.ok:
        log.info("reminder draft fallback invoice=%s reason=%s", invoice_id, res.fallback)
        raise ReminderError(503, "DRAFT_FAILED", "Couldn't draft a reminder — try again.")
    problems = check_draft(res.data, prep["facts"], prep["invoice_no"], day)
    if problems:
        log.info("reminder draft rejected invoice=%s problems=%s", invoice_id, problems)
        raise ReminderError(503, "DRAFT_FAILED", "Couldn't draft a reminder — try again.")
    return await run_in_threadpool(_save, session_factory, invoice_id, prep, res.data, day, actor)


def _due_invoices(session_factory: Callable[[], Session], today: date) -> list[int]:
    """Real (non-demo) buyers' invoices eligible today (B6: demo copies have no daily job)."""
    with session_factory() as s:
        rows = s.scalars(
            select(Invoice)
            .join(Buyer, Invoice.buyer_id == Buyer.id)
            .where(
                Buyer.is_demo.is_(False),
                Invoice.due_on < today,
                Invoice.ledger_status.in_(("OPEN", "PARTIALLY_PAID")),
            )
        ).all()
        return [i.id for i in rows if eligible(s, i, today) is None]


async def daily_run(
    session_factory: Callable[[], Session], *, client: Any = None, today: date | None = None
) -> int:
    """Job C's once-a-day pass (the worker calls it, step 4). Returns drafts created."""
    day = today or today_ny()
    made = 0
    for invoice_id in await run_in_threadpool(_due_invoices, session_factory, day):
        try:
            await draft(session_factory, invoice_id, client=client, today=day)
            made += 1
        except ReminderError as e:
            log.info("daily reminder skipped invoice=%s: %s", invoice_id, e.code)
    return made
