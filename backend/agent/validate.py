"""Answer validator (P4 D4, R7, Z8): code checks every answer against the buyer's records.

`check_answer(result, facts) -> list[str]` returns the problems found; empty = the answer may
be shown. Any problem → the caller's safe fallback (D7). Checks:
(a) every cited record id is this buyer's (incl. pending `proposal:<id>`, Z8);
(b) every money figure is in the allowed set — record values ∪ code-computed totals. Read as
    money: `$1,420`, `$1,420.00`, `$20`, `20 dollars`, `USD 20`, and a bare `1,420.00` / `1,420`
    near owe/paid/due/price/total/balance/credit. `$1,420.5` is ambiguous → rejected. Signs
    are ignored;
(c) every day count ("38 days ago", "in 11 days", "18 days overdue") is in the allowed set;
(d) every SKU / invoice / memo / return ref mentioned exists for this buyer;
(e) ≤ 600 characters;
(f) no record id in the text (`memo_line:S-102`, `payment:1`, …): ids belong in cited_records,
    never in what the buyer reads.
"""

import re

from backend.agent.facts import Facts
from backend.agent.schemas import AnswerResult
from backend.agent.tools import unknown_records
from backend.core.dates import age_days

MAX_CHARS = 600
_NUM = r"\d[\d,]*(?:\.\d+)?"
MONEY_RE = re.compile(
    rf"(?:\$|\bUSD\s?)\s?-?({_NUM})|\b({_NUM})\s?(?:dollars?|bucks)\b", re.I
)  # fmt: skip
BARE_RE = re.compile(r"(?<![\w$.,-])(\d{1,3}(?:,\d{3})+(?:\.\d{2})?|\d+\.\d{2})(?![\w.,]*\d)")
MONEY_WORDS = re.compile(r"\b(owe[sd]?|owing|paid|pay|due|price[sd]?|total|balance|credit)\b", re.I)
DAYS_RE = re.compile(r"\b(\d+)\s+days?\b", re.I)
RECORD_ID_RE = re.compile(r"\b(?:memo_line|memo|return|invoice|payment|proposal):[A-Za-z0-9]", re.I)
REF_RES = {
    "memo_line": re.compile(r"\bS-\d{3}\b", re.I),
    "invoice": re.compile(r"\bINV-\d+\b", re.I),
    "memo": re.compile(r"\bM-\d+\b", re.I),
    "return": re.compile(r"\bR-\d+\b", re.I),
}
WELL_FORMED = re.compile(r"^\d{1,3}(?:,\d{3})*(?:\.\d{2})?$|^\d+(?:\.\d{2})?$")


def allowed_money(f: Facts) -> set[int]:
    """Cents values an answer may state (D4 b, Z8)."""
    vals = {f.owed_cents, f.credit_cents}
    for i in f.invoices:
        vals |= {i.total_cents, i.paid_cents, i.due_cents}
    by_memo: dict[tuple[str, str], int] = {}
    for ln in f.lines:
        vals.add(ln.price_cents)
        key = (ln.memo_no, ln.status)
        by_memo[key] = by_memo.get(key, 0) + ln.price_cents
    vals |= set(by_memo.values())
    for p in f.payments:
        vals.add(p.amount_cents)
        vals |= {c for _no, c in p.applied}
    vals |= {abs(p.amount_cents) for p in f.proposals if p.status == "PENDING" and p.amount_cents}
    return {abs(v) for v in vals}


def allowed_days(f: Facts) -> set[int]:
    """Day counts an answer may state (D4 c, R7)."""
    t = f.today
    days = {f.oldest_age_days()}
    for ln in f.lines:
        if ln.returned_on:
            days.add((t - ln.returned_on).days)
        if ln.due_back_on:
            days.add(abs((ln.due_back_on - t).days))
    for i in f.invoices:
        days |= {abs((i.due_on - t).days), f.days_overdue(i), age_days(i.issued_on, t)}
    return days


def _cents(text: str) -> int | None:
    """'1,420.00' → 142000; None when the figure is ambiguous ('1,420.5', '1,42')."""
    if not WELL_FORMED.match(text):
        return None
    whole, _, frac = text.replace(",", "").partition(".")
    return int(whole) * 100 + int(frac or 0)


def money_figures(answer: str) -> list[str | int]:
    """Every money figure as cents, or the raw text when it can't be read unambiguously."""
    found: list[str | int] = []
    spans = []
    for m in MONEY_RE.finditer(answer):
        raw = (m.group(1) or m.group(2)).rstrip(",")  # '$1,420, then' is fine
        c = _cents(raw)
        found.append(raw if c is None else c)
        spans.append(m.span())
    for m in BARE_RE.finditer(answer):
        if any(a <= m.start() < b for a, b in spans):
            continue
        window = answer[max(0, m.start() - 30) : m.end() + 30]
        if MONEY_WORDS.search(window):
            c = _cents(m.group(1))
            found.append(m.group(1) if c is None else c)
    return found


def check_answer(result: AnswerResult, f: Facts) -> list[str]:
    problems: list[str] = []
    text = result.answer
    if len(text) > MAX_CHARS:
        problems.append(f"answer longer than {MAX_CHARS} chars")
    if ids := RECORD_ID_RE.findall(text):
        problems.append(f"record id in the answer text ({len(ids)})")
    if bad := unknown_records(f, result.cited_records):
        problems.append(f"cites unknown records {bad}")
    money = allowed_money(f)
    for v in money_figures(text):
        if isinstance(v, str):
            problems.append(f"ambiguous money figure {v!r}")
        elif v not in money:
            problems.append(f"money figure {v} not in the records")
    days = allowed_days(f)
    for m in DAYS_RE.finditer(text):
        if int(m.group(1)) not in days:
            problems.append(f"day count {m.group(1)} not in the records")
    known = {
        "memo_line": {ln.sku.upper() for ln in f.lines},
        "invoice": {i.invoice_no.upper() for i in f.invoices},
        "memo": {x.memo_no.upper() for x in f.memos},
        "return": {ln.return_ref.upper() for ln in f.lines if ln.return_ref},
    }
    for kind, rx in REF_RES.items():
        for ref in rx.findall(text):
            if ref.upper() not in known[kind]:
                problems.append(f"mentions unknown {kind} {ref}")
    return problems
