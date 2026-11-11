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
