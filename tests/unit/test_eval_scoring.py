"""Eval scoring (Z1) and held-out groups (Z10) — pure, no LLM."""

import pytest

from eval.run_eval import Result, group_of, parse_groups, score, table, unsafe


def res(rid, exp_label, exp_action, label, action, fallback=None):
    return Result(rid, exp_label, exp_action, label, action, fallback, False)


def test_spike_rescored_fallbacks_never_count_as_model_passes():
    rows = [
        res("w01", "DEDUCTION_REQUEST", "ADJUSTMENT:INV-1051:-2000",
            "DEDUCTION_REQUEST", "ADJUSTMENT:INV-1051:-2000"),
        # w17: wrong label, the guard happens to give the expected REPLY
        res("w17", "DISPUTE", "REPLY", "DEDUCTION_REQUEST", "REPLY"),
        # w19: cut off → fallback REPLY, which is the expected action
        res("w19", "DISPUTE", "REPLY", "OTHER", "REPLY", fallback="length"),
        res("w20", "DISPUTE", "REPLY", "DISPUTE", "REPLY"),
    ]  # fmt: skip
    s = score(rows)
    assert (s["n"], s["model"], s["system"]) == (4, 2, 4)
    assert (s["non_reply_n"], s["non_reply_model"], s["non_reply_system"]) == (1, 1, 1)
    assert s["cutoff"] == 1


def test_right_label_with_a_fallback_is_still_a_model_miss():
    s = score([res("x", "OTHER", "REPLY", "OTHER", "REPLY", fallback="APITimeoutError")])
    assert (s["model"], s["system"]) == (0, 1)


def test_groups_split_rows_and_reject_bad_specs():
    g = parse_groups(["pattern_in_dev=h03, h11"])
    assert g == {"pattern_in_dev": {"h03", "h11"}}
    assert group_of("h03", g) == "pattern_in_dev" and group_of("h01", g) == "main"
    for bad in (["main=h01"], ["nogroup"], ["a=h01", "b=h01"]):
        with pytest.raises(ValueError):
            parse_groups(bad)


def test_table_prints_n_and_three_parts():
    ok = res("a", "OTHER", "REPLY", "OTHER", "REPLY")
    cut = res("a", "OTHER", "REPLY", "OTHER", "REPLY", fallback="length")
    s = {"baseline": score([ok]), "agent": score([cut])}
    out = table("main", s)
    assert "(n = 1)" in out and "| agent | 0/1 | 1/1 | 0/0 / 0/0 | 0 | 1/1 (100.0 %) |" in out


def test_unsafe_checks_every_part_of_a_joined_action():
    row = {"expected": {"action": "ANSWER:ALREADY_CREDITED:S-121"}}
    assert unsafe(row, "ANSWER:ALREADY_CREDITED:S-121|PROMISE_NOTE:next:FRI")
    assert not unsafe(row, "ANSWER:ALREADY_CREDITED:S-121|RETURN_CHECK:NO_RECORD:S-123")


def test_job_a_grounding_normalises_money_and_days():
    from backend.agent.answer import AnswerOutcome
    from eval.run_eval import grounded

    row = {"expected": {"cited_records": ["memo_line:S-120", "invoice:INV-1051"],
                        "facts": ["$1,420.00", "25 days", "R-311"]}}  # fmt: skip
    good = AnswerOutcome("S-120 ($1,420) is due in 25 days; R-311 isn't it.",
                         cited_records=["Invoice:INV-1051", "memo_line:S-120"])  # fmt: skip
    assert grounded(row, good) == (True, [])
    short = AnswerOutcome("S-120 is $1,420.00.", cited_records=["memo_line:S-120"])
    assert grounded(row, short) == (False, ["invoice:INV-1051", "25 days", "R-311"])
    fb = AnswerOutcome("I'm not sure", fallback_reason="validator")
    assert grounded(row, fb) == (False, ["(fallback)"])


# ---------- step 6: full runner (R11, Z1, Z3) ----------

FIXTURE = __import__("pathlib").Path(__file__).parents[1] / "fixtures" / "eval"


def test_dry_replay_reproduces_the_committed_report():
    """What CI runs (`--dry`): stored outputs → the same table, no LLM."""
    from eval.run_eval import load, report

    text = report(*load(FIXTURE / "dry_small.jsonl"))
    assert text == (FIXTURE / "dry_small.md").read_text(encoding="utf-8").rstrip("\n")
    assert "| agent (fixture-model) | 2 · 3 /4 (min 2, max 3) | 4 · 3 /4" in text
    assert "1/8 (12.5 %)" in text  # the cut-off rate across passes
    assert "{'validator': 1}" in text and "money figure 120000" in text


def test_guard_and_fallback_catches_are_counted_not_model_passes():
    s = score([
        res("a", "OTHER", "REPLY", "DEDUCTION_REQUEST", "REPLY"),  # guard caught a wrong label
        res("b", "DISPUTE", "REPLY", "OTHER", "REPLY", fallback="length"),  # fallback caught
        res("c", "QUESTION", "ANSWER", "QUESTION", "ANSWER"),
    ])  # fmt: skip
    assert (s["model"], s["system"], s["guard_catches"], s["fallback_catches"]) == (1, 3, 1, 1)


def test_fields_match_normalises():
    from eval.run_eval import fields_match

    assert fields_match({"sku": "S-104", "amount_cents": 5000},
                        {"sku": "s-104 ", "amount_cents": 5000, "reason": "x"})  # fmt: skip
    assert fields_match({"skus": ["S-123", "S-121"]}, {"skus": ["s-121", "S-123"]})
    assert fields_match({"invoice_no": "INV-1038"}, {"invoice_no": "1038"})
    assert fields_match({"date": "Friday"}, {"date": "friday"})
    assert not fields_match({"sku": "S-104"}, {"sku": "S-110"})
    assert fields_match({}, {"sku": "S-104"})  # nothing expected
    assert fields_match({"reason": "scratch"}, {"reason": "chip"})  # free text not scored


def test_budget_guard_stops_before_passing_the_cap():
    from eval.run_eval import BudgetStop, Spend

    s = Spend(usd_in=1000.0, usd_out=1000.0, cap=10.0)  # $1 per 1k tokens: worst call $7.60
    s.check()
    s.add((1000, 500))  # $1.50: 1.50 + 7.60 worst case ≤ 10 → still allowed
    s.check()
    s.add((1000, 500))  # $3.00 spent
    with pytest.raises(BudgetStop):
        s.check()  # 3.00 + 7.60 worst case > 10
    cheap = Spend(usd_in=0.1, usd_out=0.1, cap=10.0)
    for _ in range(100):
        cheap.check()
        cheap.add((6000, 1600))
    assert cheap.spent < 10.0


def test_real_run_refuses_without_prices(monkeypatch):
    import sys

    from eval import run_eval

    monkeypatch.setattr(sys, "argv", ["run_eval", "--passes", "1"])
    with pytest.raises(SystemExit, match="usd-in"):
        run_eval.main()
