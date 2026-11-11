"""Triage → action, decided by CODE (P4 D1, D5; A4, W3, W5; plan P2, P3).

The model only labels and extracts fields. Which proposal (if any) gets created — kind,
invoice, amount, date — is computed here from the label, the fields, the buyer's records and
two deterministic reads of the raw text. No model output can create a proposal the records
don't support (invariant 5).

Action strings (also what the eval scores): `ADJUSTMENT:INV-1051:-2000`,
`RETURN_CHECK:NO_RECORD:S-103`, `ANSWER:ALREADY_CREDITED:S-102`, `PROMISE_NOTE:next:FRI`,
`ANSWER`, `REPLY`.
"""

import re
from dataclasses import dataclass, field
from typing import Any

from backend.agent import dates
from backend.agent.facts import Facts

SKU_RE = re.compile(r"\bS-\d{3}\b", re.I)
PERCENT_RE = re.compile(r"\d+(?:\.\d+)?\s*(?:%|percent\b)", re.I)
# Forged model output / role markup inside buyer text (plan P3). Deliberately NOT matching
# plain "ignore previous instructions" — w27 expects a pending proposal (W3).
MARKUP_RE = re.compile(
    r"</?\s*buyer_message\s*>|^\s*(?:assistant|system)\s*:|\blabel\s*=|\bamount_cents\s*=",
    re.I | re.M,
)
CLAIMED_APPROVAL_RE = re.compile(
    r"\b(?:already\s+approved|approved|already\s+agreed|as\s+discussed|you\s+said)\b", re.I
)


@dataclass
class Action:
    kind: str  # ADJUSTMENT | RETURN_CHECK | PROMISE_NOTE | REPLY | ANSWER
    code: str  # the eval's action string
    payload: dict[str, Any] = field(default_factory=dict)
    evidence: dict[str, Any] = field(default_factory=dict)
    reason: str = ""  # why a REPLY (shown to the supplier, used in summaries)


def _reply(reason: str, **evidence: Any) -> Action:
    return Action("REPLY", "REPLY", evidence={"source": "agent", **evidence}, reason=reason)


def decide(label: str, fields: dict[str, Any], text: str, facts: Facts) -> Action:
    if MARKUP_RE.search(text or ""):
        return _reply("message contains instruction-like markup", checks=["markup guard"])

    sku = (fields.get("sku") or "").strip().upper() or None
    line = facts.line(sku)

    if label == "DEDUCTION_REQUEST":
        if line is None:
            return _reply(f"{sku or 'that item'} isn't on your memos")
        if line.status != "KEPT":
            return _reply(f"{line.sku} isn't billed (it's {line.status.lower()})")
        if PERCENT_RE.search(text or ""):
            return _reply("Demo Diamond Co. will confirm the amount")
        amount = fields.get("amount_cents")
        if not isinstance(amount, int) or isinstance(amount, bool) or amount <= 0:
            return _reply("Demo Diamond Co. will confirm the amount")
        if amount > line.price_cents:
            return _reply(f"more than {line.sku}'s price")
        inv = facts.invoice(line.invoice_no)
        assert inv is not None
        if any(
            p.kind == "ADJUSTMENT" and p.status == "PENDING" and p.invoice_no == inv.invoice_no
            and (p.sku or "").upper() == line.sku
            for p in facts.proposals
        ):  # fmt: skip
            return _reply("Already sent — waiting for their approval")
        checks = [f"{line.sku} kept on {inv.invoice_no}", f"{amount} ≤ price {line.price_cents}"]
        others = sorted({s.upper() for s in SKU_RE.findall(text or "")} - {line.sku})
        evidence: dict[str, Any] = {
            "source": "agent",
            "checks": checks,
            "records": [f"memo_line:{line.sku}", f"invoice:{inv.invoice_no}"],
            "due_cents": inv.due_cents,
        }
        if fields.get("invoice_no") and facts.invoice(fields["invoice_no"]) is not inv:
            evidence["model_invoice_no_ignored"] = fields["invoice_no"]
        if others:
            evidence["other_skus_not_handled"] = others  # one request at a time (R8)
        if CLAIMED_APPROVAL_RE.search(text or ""):
            evidence["note"] = "claims prior approval — not verified"
        return Action(
            "ADJUSTMENT",
            f"ADJUSTMENT:{inv.invoice_no}:-{amount}",
            payload={
                "invoice_no": inv.invoice_no,
                "sku": line.sku,
                "amount_cents": -amount,
                "reason": (fields.get("reason") or "").strip()[:200],
            },
            evidence=evidence,
        )

    if label == "RETURN_CLAIM":
        if line is None:
            return _reply(f"{sku or 'that item'} isn't on your memos")
        if line.status == "RETURNED":
            return Action("ANSWER", f"ANSWER:ALREADY_CREDITED:{line.sku}")
        return Action(
            "RETURN_CHECK",
            f"RETURN_CHECK:NO_RECORD:{line.sku}",
            payload={"sku": line.sku},
            evidence={
                "source": "agent",
                "check": "NO_RECORD",
                "records": [f"memo_line:{line.sku}"],
            },
        )

    if label == "PAYMENT_PROMISE":
        resolved = dates.resolve(fields.get("date"), facts.today)
        if resolved is None:
            return _reply("couldn't read the date")
        rule, when = resolved
        amount = fields.get("amount_cents")
        payload: dict[str, Any] = {"promise_date": when.isoformat()}
        if isinstance(amount, int) and not isinstance(amount, bool) and amount > 0:
            payload["amount_cents"] = amount
        return Action("PROMISE_NOTE", f"PROMISE_NOTE:{rule}", payload=payload)

    if label == "QUESTION":
        return Action("ANSWER", "ANSWER")

    return _reply("Demo Diamond Co. will write back")  # DISPUTE, OTHER, anything unknown
