"""Job A — answer a buyer's question from their records (FR-8; P4 D3, D4, D7).

One json_schema call, then code checks every answer (validate.py). A failure of any kind gives
the plain fallback text; the caller files it for the supplier (G1). No database here.
"""

from dataclasses import dataclass, field
from typing import Any

from backend.agent import llm
from backend.agent.facts import Facts
from backend.agent.tools import record_ids
from backend.agent.validate import check_answer

FALLBACK_TEXT = "I'm not sure, so I've asked Demo Diamond Co. to reply."


@dataclass
class AnswerOutcome:
    answer: str
    cited_records: list[str] = field(default_factory=list)
    looked_up: list[str] = field(default_factory=list)  # cited ids that exist (V3 trace)
    confidence: str = "low"
    offer_escalation: bool = True
    fallback_reason: str | None = None  # llm fallback, "validator" or "cap"
    problems: list[str] = field(default_factory=list)  # validator findings (eval + ai_result)
    model: str = ""
    prompt_version: str = ""
    ms: int = 0
    tokens: tuple[int, int] = (0, 0)

    @property
    def fallback(self) -> bool:
        return self.fallback_reason is not None

    def ai_result(self) -> dict[str, Any]:
        return {
            "job": "answer",
            "model": self.model,
            "prompt_version": self.prompt_version,
            "ms": self.ms,
            "tokens": list(self.tokens),
            "looked_up": self.looked_up,
            "fallback_reason": self.fallback_reason,
            "validator": self.problems,
        }


def fallback(reason: str, res: llm.JobResult | None = None) -> AnswerOutcome:
    out = AnswerOutcome(FALLBACK_TEXT, fallback_reason=reason)
    if res:
        out.model, out.prompt_version, out.ms, out.tokens = (
            res.model, res.prompt_version, res.ms, res.tokens,
        )  # fmt: skip
    return out


async def run_answer(
    facts: Facts, question: str, budget: llm.Budget, *, client: Any = None, model: str | None = None
) -> AnswerOutcome:
    res = await llm.acall("answer", facts, question, budget, client=client, model=model)
    if not res.ok:
        return fallback(res.fallback or "error", res)
    data = res.data
    problems = check_answer(data, facts)
    if problems:
        out = fallback("validator", res)
        out.problems = problems
        return out
    known = {r.lower() for r in record_ids(facts)}
    return AnswerOutcome(
        answer=data.answer,
        cited_records=data.cited_records,
        looked_up=[c for c in data.cited_records if c.strip().lower() in known],
        confidence=data.confidence,
        offer_escalation=data.offer_escalation,
        model=res.model,
        prompt_version=res.prompt_version,
        ms=res.ms,
        tokens=res.tokens,
    )
