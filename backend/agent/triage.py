"""Job B — triage a buyer's message (FR-9; P4 D1, D5, D7, D10, R12).

The model labels and extracts; `decide()` picks the action from the records. Summaries the
buyer and supplier see are written here by code, money with 2 decimals — never model text.
No database here.
"""

from dataclasses import dataclass
from datetime import date
from typing import Any

from backend.agent import llm
from backend.agent.actions import Action, decide
from backend.agent.facts import Facts
from backend.agent.tools import usd

SENT = "Sent to Demo Diamond Co. — they'll write back"


@dataclass
class TriageOutcome:
    label: str | None
    fields: dict[str, Any]
    action: Action | None  # None = fallback (G1 REPLY)
    fallback_reason: str | None
    model: str = ""
    prompt_version: str = ""
    ms: int = 0
    tokens: tuple[int, int] = (0, 0)

    def ai_result(self) -> dict[str, Any]:
        return {
            "job": "triage",
            "model": self.model,
            "prompt_version": self.prompt_version,
            "ms": self.ms,
            "tokens": list(self.tokens),
            "label": self.label,
            "fields": self.fields,
            "action": self.action.code if self.action else None,
            "fallback_reason": self.fallback_reason,
        }


async def run_triage(
    facts: Facts, text: str, budget: llm.Budget, *, client: Any = None, model: str | None = None
) -> TriageOutcome:
    res = await llm.acall("triage", facts, text, budget, client=client, model=model)
    meta = {"model": res.model, "prompt_version": res.prompt_version, "ms": res.ms,
            "tokens": res.tokens}  # fmt: skip
    if not res.ok:
        return TriageOutcome(None, {}, None, res.fallback or "error", **meta)
    fields = res.data.fields.model_dump()
    return TriageOutcome(res.data.label, fields, decide(res.data.label, fields, text, facts),
                         None, **meta)  # fmt: skip


def _day(d: date) -> str:
    return f"{d:%a %b} {d.day}"


def summary(
    kind: str,
    *,
    invoice_no: str | None = None,
    sku: str | None = None,
    amount_cents: int | None = None,
    promise_date: str | None = None,
    reason: str | None = None,
    fallback: bool = False,
    status: str | None = None,
    mode: str | None = None,
    credit_cents: int | None = None,
) -> str:
    """One proposal's code-written summary (also computed at read time for `requests`, D17).
    A decided deduction reads the way the buyer should understand it (P3 A11, G24)."""
    if kind == "ADJUSTMENT" and amount_cents is not None:
        money = usd(abs(amount_cents))
        if status == "DONE" and mode == "CREDIT":
            return f"Approved as {usd(credit_cents or abs(amount_cents))} credit on your account"
        if status in ("APPROVED", "DONE"):
            return f"Approved — {money} off {invoice_no or 'your invoice'}"
        return f"−{money} on {invoice_no or 'your invoice'} for {sku or 'an item'}"
    if kind == "RETURN_CHECK":
        return f"Return of {sku or 'an item'} — Demo Diamond Co. will check their records"
    if kind == "PROMISE_NOTE" and promise_date:
        when = _day(date.fromisoformat(promise_date))
        extra = f" ({usd(amount_cents)})" if amount_cents else ""
        return f"Payment planned for {when}{extra} — noted for Demo Diamond Co."
    if kind == "REPLY" and reason and not fallback:
        return f"{SENT} ({reason})"
    return SENT


def answer_text(action: Action, facts: Facts) -> str | None:
    """The code-written answer for `ANSWER:ALREADY_CREDITED:<sku>` (R9)."""
    sku = action.payload.get("sku")
    line = facts.line(sku)
    if not line or not line.returned_on:
        return None
    days = (facts.today - line.returned_on).days
    ago = "today" if days == 0 else f"{days} day{'s' if days != 1 else ''} ago"
    return f"{line.sku} isn't on your bill — return {line.return_ref} was received {ago}."
