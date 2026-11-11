"""Keyword + regex baseline (P4 D11): the honest "rules" competitor.

It reads the same Facts (item descriptions) and feeds the SAME `decide()` as the agent, so the
comparison isolates understanding, not plumbing. Tuned only on the written dev rows (V2).
"""

import re
from typing import Any

from backend.agent.facts import Facts

SKU_RE = re.compile(r"\bS-\d{3}\b", re.I)
AMOUNT_RE = re.compile(r"\$?\s?(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d{1,2}))?\s*(k\b)?", re.I)
DATE_RE = re.compile(
    r"\b(next\s+week|tomorrow|today|in\s+\d{1,2}\s+days?|(?:the\s+)?\d{1,2}(?:st|nd|rd|th)|"
    r"mon(?:day)?|tue(?:s|sday)?|wed(?:nesday)?|thu(?:rs|rsday)?|fri(?:day)?|sat(?:urday)?|sun(?:day)?)\b",
    re.I,
)
WORDS = {
    "DEDUCTION_REQUEST": r"\b(off|discount|knock|credit|reduce|deduct)\b",
    "RETURN_CLAIM": r"\b(returned|sent (?:it )?back|gave (?:it )?back|send(?:ing)? back)\b",
    "PAYMENT_PROMISE": r"\b(pay|payment|send (?:a )?check|wire)\b",
    "DISPUTE": r"\b(wrong|agreed|call me|i paid|already paid|mistake|error)\b",
    "QUESTION": r"\?|^\s*(why|how|what|when|is|did|do|does|can)\b",
}
ORDER = ("DISPUTE", "RETURN_CLAIM", "DEDUCTION_REQUEST", "PAYMENT_PROMISE", "QUESTION")
STOP = {"pair", "ring", "with", "the"}


def _sku(text: str, facts: Facts) -> str | None:
    if m := SKU_RE.search(text):
        return m.group(0).upper()
    low = text.lower()
    for ln in facts.lines:
        for word in re.findall(r"[a-z]{4,}", ln.description.lower()):
            if word not in STOP and re.search(rf"\b{word}\b", low):
                return ln.sku
    return None


def _amount(text: str) -> int | None:
    for m in AMOUNT_RE.finditer(text):
        whole = int(m.group(1).replace(",", ""))
        cents = int((m.group(2) or "0").ljust(2, "0"))
        if m.group(3):
            whole *= 1000
        value = whole * 100 + cents
        if value > 0:
            return value
    return None


def triage(text: str, facts: Facts) -> tuple[str, dict[str, Any]]:
    label = "OTHER"
    for name in ORDER:
        if re.search(WORDS[name], text, re.I | re.M):
            label = name
            break
    fields: dict[str, Any] = {"sku": _sku(text, facts)}
    if label in ("DEDUCTION_REQUEST", "PAYMENT_PROMISE"):
        fields["amount_cents"] = _amount(SKU_RE.sub(" ", text))
    if label == "PAYMENT_PROMISE":
        m = DATE_RE.search(text)
        fields["date"] = m.group(0) if m else None
    return label, fields
