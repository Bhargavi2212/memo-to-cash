"""P4 step 2 — the answer validator (D4, R7, Z8): money, day counts, refs, citations, length."""

import pytest

from backend.agent.facts import facts_from_seed
from backend.agent.schemas import AnswerResult
from backend.agent.validate import allowed_days, allowed_money, check_answer, money_figures
from backend.core.dates import today_ny

D = today_ny()
SEED = facts_from_seed(D)
PAID = facts_from_seed(D, 500000)


def ans(text, cited=()):
    # model_construct skips Pydantic's own 600-char check, so the validator's (e) is tested too
    return AnswerResult.model_construct(
        answer=text, cited_records=list(cited), confidence="high", offer_escalation=False
    )


@pytest.mark.parametrize(
    "text",
    [
        "You owe $8,420.00 in total.",
        "The solitaire is $1,420 and due in 25 days.",
        "S-102 isn't on your bill — return R-311 was received 38 days ago.",
        "Send the pendant back in 11 days.",
        "INV-1038 is 18 days overdue.",
        "Your $20 request on S-120 is still waiting for Demo Diamond Co.",  # Z8
        "That is 20 dollars off, still pending.",
        "USD 20 is pending; you owe 8,420.00 in total.",
        "Memo M-207: kept items total $3,500.00.",
    ],
)
def test_grounded_answers_pass(text):
    assert check_answer(ans(text, ["invoice:INV-1051", "proposal:seed"]), SEED) == []


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        ("You owe $8,000.", "money figure 800000"),
        ("You owe $1,420.5 on INV-1051.", "ambiguous money figure"),
        ("You owe 8,000.00 now.", "money figure 800000"),
        ("We'll take 50 bucks off.", "money figure 5000"),
        ("The pendant is due back in 12 days.", "day count 12"),
        ("S-199 was returned.", "unknown memo_line S-199"),
        ("INV-2001 is paid.", "unknown invoice INV-2001"),
        ("Return R-999 arrived.", "unknown return R-999"),
        ("Memo M-999 is open.", "unknown memo M-999"),
        ("x" * 601, "longer than 600"),
    ],
)
def test_ungrounded_answers_fail(text, problem):
    problems = check_answer(ans(text), SEED)
    assert any(problem in p for p in problems), problems


def test_citations_must_be_this_buyers_records():
    assert check_answer(ans("ok", ["invoice:INV-2001"]), SEED) == [
        "cites unknown records ['invoice:INV-2001']"
    ]
    assert check_answer(ans("ok", ["payment:1"]), SEED) != []
    assert check_answer(ans("ok", ["payment:1"]), PAID) == []


def test_after_a_payment_the_new_figures_are_allowed():
    text = "Payment 1 of $5,000.00 paid INV-1038 and $1,500.00 of INV-1042; $2,000.00 is left."
    assert check_answer(ans(text, ["payment:1", "invoice:INV-1042"]), PAID) == []
    assert 342000 in allowed_money(PAID) and 342000 not in allowed_money(SEED)


def test_money_reading_formats():
    assert money_figures("$20, then $1,420, and 20 dollars; USD 5") == [2000, 142000, 2000, 500]
    assert money_figures("due 2026-11-10, sku S-102, memo 207") == []
    assert money_figures("sent 3 items, 1,420.00 due") == [142000]


def test_allowed_days_cover_returns_due_backs_and_due_dates():
    assert {38, 11, 25, 18, 21, 6} <= allowed_days(SEED)
