"""Ask + message (FR-8, FR-9; P4 D7, D9, D10, D18, D19, G1).

Order per request: (1) one short transaction — the token's buyer, their Facts, one cap unit;
(2) the model, holding no lock and no session (D19); (3) one transaction under
`pg_advisory_xact_lock(buyer_id)` — the message row, `decide()` re-run on fresh Facts (so a
duplicate sent meanwhile becomes a REPLY), proposals, audit. Logs carry the label, ms, model
and `token_ref` — never the buyer's text. The agent never changes money (invariant 5).
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified
from starlette.concurrency import run_in_threadpool

from backend.agent import caps, llm
from backend.agent.actions import Action, decide
from backend.agent.answer import FALLBACK_TEXT, AnswerOutcome, run_answer
from backend.agent.answer import fallback as answer_fallback
from backend.agent.facts import Facts, facts_for_buyer
from backend.agent.triage import SENT, TriageOutcome, answer_text, run_triage, summary
from backend.core.dates import today_ny
from backend.db.models import Invoice, Message, Proposal
from backend.services import audit
from backend.services.statement import find_buyer, token_ref

log = logging.getLogger(__name__)


@dataclass
class AskResult:
    answer: str
    cited_records: list[str]
    looked_up: list[str]
    confidence: str
    fallback: bool
    proposal_id: int | None = None


@dataclass
class MessageResult:
    received: bool
    label: str | None
    summary: str
    checks: list[str] = field(default_factory=list)
    proposal_id: int | None = None
    proposal_ids: list[int] = field(default_factory=list)
    answer: str | None = None
    fallback: bool = False


def _prepare(sf: Callable[[], Session], token: str, today: date) -> tuple[int, Facts, bool]:
    with sf() as s, s.begin():
        buyer = find_buyer(s, token)  # StatementNotFoundError → 404 in the route
        facts = facts_for_buyer(s, buyer.id, today)
        allowed = caps.take_unit(s, today, token)
        return buyer.id, facts, allowed


def _lock(s: Session, buyer_id: int) -> None:
    s.execute(text("SELECT pg_advisory_xact_lock(:id)"), {"id": buyer_id})


def _file_fallback(s: Session, buyer_id: int, msg: Message, today: date, reason: str) -> int:
    """G1: one pending fallback REPLY per buyer per day; later ones append their message."""
    item = {"message_id": msg.id, "kind": msg.kind, "text": msg.text, "reason": reason}
    existing = s.scalars(
        select(Proposal).where(
            Proposal.buyer_id == buyer_id,
            Proposal.kind == "REPLY",
            Proposal.status == "PENDING",
            Proposal.payload["fallback_day"].astext == today.isoformat(),
        )
    ).first()
    if existing:
        existing.payload = {**existing.payload, "items": [*existing.payload["items"], item]}
        return existing.id
    p = Proposal(
        buyer_id=buyer_id,
        kind="REPLY",
        payload={"fallback_day": today.isoformat(), "items": [item]},
        evidence={"source": "agent", "fallback": reason},
        source_message_id=msg.id,
    )
    s.add(p)
    s.flush()
    audit.log(s, "agent", "proposal.created", ref=str(p.id), detail={"kind": "REPLY",
              "fallback": reason})  # fmt: skip
    return p.id


def _store_ask(
    sf: Callable[[], Session], buyer_id: int, question: str, out: AnswerOutcome, today: date
) -> int | None:
    with sf() as s, s.begin():
        _lock(s, buyer_id)
        msg = Message(buyer_id=buyer_id, text=question, kind="QUESTION_UI",
                      ai_result=out.ai_result())  # fmt: skip
        s.add(msg)
        s.flush()
        pid = _file_fallback(s, buyer_id, msg, today, out.fallback_reason) if out.fallback else None
        audit.log(s, "agent", "agent.answered", ref=str(msg.id),
                  detail={"fallback": out.fallback_reason, "looked_up": out.looked_up})  # fmt: skip
        return pid


async def ask(
    sf: Callable[[], Session],
    token: str,
    question: str,
    *,
    client: Any = None,
    today: date | None = None,
) -> AskResult:
    budget = llm.Budget()
    today = today or today_ny()
    buyer_id, facts, allowed = await run_in_threadpool(_prepare, sf, token, today)
    out = await run_answer(facts, question, budget, client=client) if allowed else (
        answer_fallback("cap"))  # fmt: skip
    pid = await run_in_threadpool(_store_ask, sf, buyer_id, question, out, today)
    log.info("agent ask token=%s ms=%s model=%s fallback=%s", token_ref(token), out.ms,
             out.model, out.fallback_reason)  # fmt: skip
    return AskResult(out.answer, out.cited_records, out.looked_up, out.confidence,
                     out.fallback, pid)  # fmt: skip


def _proposal_for(s: Session, buyer_id: int, part: Action, msg: Message, label: str) -> Proposal:
    if part.kind == "ADJUSTMENT":
        inv_id = s.scalars(
            select(Invoice.id).where(
                Invoice.buyer_id == buyer_id, Invoice.erp_invoice_no == part.payload["invoice_no"]
            )
        ).one()
        payload = {
            "invoice_id": inv_id,
            "amount_cents": part.payload["amount_cents"],
            "reason": part.payload.get("reason") or "",
            "sku": part.payload["sku"],
        }
    elif part.kind == "REPLY":
        payload = {"reason": part.reason, **({"sku": part.payload["sku"]} if part.payload else {})}
    else:  # RETURN_CHECK, PROMISE_NOTE
        payload = dict(part.payload)
    evidence = {"source": "agent", **part.evidence, "label": label, "action": part.code}
    return Proposal(buyer_id=buyer_id, kind=part.kind, payload=payload, evidence=evidence,
                    source_message_id=msg.id)  # fmt: skip


def _summary_of(part: Action) -> str:
    p = part.payload
    if part.kind == "ADJUSTMENT":
        return summary("ADJUSTMENT", invoice_no=p["invoice_no"], sku=p["sku"],
                       amount_cents=p["amount_cents"]) + " — waiting for approval"  # fmt: skip
    if part.kind == "REPLY":
        return summary("REPLY", reason=part.reason)
    return summary(part.kind, sku=p.get("sku"), amount_cents=p.get("amount_cents"),
                   promise_date=p.get("promise_date"))  # fmt: skip


def _store_message(
    sf: Callable[[], Session],
    buyer_id: int,
    body: str,
    tri: TriageOutcome,
    ans: AnswerOutcome | None,
    today: date,
) -> MessageResult:
    with sf() as s, s.begin():
        _lock(s, buyer_id)
        ai: dict[str, Any] = {"triage": tri.ai_result()}
        if ans:
            ai["answer"] = ans.ai_result()
        msg = Message(buyer_id=buyer_id, text=body, kind="MESSAGE", ai_result=ai)
        s.add(msg)
        s.flush()
        res = MessageResult(received=True, label=tri.label, summary=SENT)

        if tri.action is None or tri.label is None:  # triage fallback (incl. cap)
            pid = _file_fallback(s, buyer_id, msg, today, tri.fallback_reason or "error")
            res.proposal_id, res.proposal_ids, res.fallback = pid, [pid], True
        elif tri.label == "QUESTION" and ans is not None:
            if ans.fallback:
                pid = _file_fallback(s, buyer_id, msg, today, ans.fallback_reason or "error")
                res.proposal_id, res.proposal_ids, res.fallback = pid, [pid], True
                res.answer = FALLBACK_TEXT
            else:
                res.answer = res.summary = ans.answer
        else:
            # D19: decide again on fresh records inside the lock (duplicates, new statuses)
            fresh = facts_for_buyer(s, buyer_id, today)
            action = decide(tri.label, tri.fields, body, fresh)
            ai["action"] = action.code
            flag_modified(msg, "ai_result")  # JSONB isn't mutation-tracked
            summaries, answers = [], []
            for part in action.each():
                if part.kind == "ANSWER":
                    if txt := answer_text(part, fresh):
                        answers.append(txt)
                    continue
                p = _proposal_for(s, buyer_id, part, msg, tri.label)
                s.add(p)
                s.flush()
                audit.log(s, "agent", "proposal.created", ref=str(p.id),
                          detail={"kind": p.kind, "action": part.code})  # fmt: skip
                res.proposal_ids.append(p.id)
                summaries.append(_summary_of(part))
                res.checks += part.evidence.get("checks", [])
            res.proposal_id = res.proposal_ids[0] if res.proposal_ids else None
            res.answer = " ".join(answers) or None
            res.summary = "; ".join(summaries + answers) or SENT
        audit.log(s, "agent", "agent.triaged", ref=str(msg.id),
                  detail={"label": tri.label, "action": ai.get("action") or (
                      tri.action.code if tri.action else None),
                          "fallback": tri.fallback_reason})  # fmt: skip
        return res


async def message(
    sf: Callable[[], Session],
    token: str,
    body: str,
    *,
    client: Any = None,
    today: date | None = None,
) -> MessageResult:
    """One 7.5 s budget and one cap unit, even when a QUESTION needs a second call (Y4)."""
    budget = llm.Budget()
    today = today or today_ny()
    buyer_id, facts, allowed = await run_in_threadpool(_prepare, sf, token, today)
    if allowed:
        tri = await run_triage(facts, body, budget, client=client)
    else:
        tri = TriageOutcome(None, {}, None, "cap")
    ans = None
    if tri.label == "QUESTION" and tri.action is not None:
        ans = await run_answer(facts, body, budget, client=client)
    res = await run_in_threadpool(_store_message, sf, buyer_id, body, tri, ans, today)
    log.info("agent message token=%s label=%s ms=%s model=%s fallback=%s", token_ref(token),
             tri.label, tri.ms + (ans.ms if ans else 0), tri.model,
             tri.fallback_reason)  # fmt: skip
    return res
