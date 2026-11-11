"""P4 step 1 — Facts lookups, record ids, prompt content and output schemas (pure, no DB)."""

import dataclasses

import pytest
from pydantic import ValidationError

from backend.agent import facts as facts_mod
from backend.agent.facts import facts_from_seed
from backend.agent.schemas import ANSWER_SCHEMA, TRIAGE_SCHEMA, AnswerResult, TriageResult
from backend.agent.tools import facts_prompt, record_ids, unknown_records
from backend.core.dates import today_ny

D = today_ny()
SEED = facts_from_seed(D)
PAID = facts_from_seed(D, 500000)


def test_lookups_return_records_and_none_for_unknown():
    assert SEED.line("s-102").return_ref == "R-311"
    assert SEED.line("S-999") is None and SEED.line(None) is None
    assert SEED.invoice("1042").invoice_no == "INV-1042"
    assert SEED.invoice("INV-9999") is None


def test_record_ids_cover_every_kind_including_the_pending_proposal():
    ids = record_ids(PAID)
    for ref in ("memo:M-201", "memo_line:S-120", "return:R-325", "invoice:INV-1051",
                "payment:1", "proposal:seed"):  # fmt: skip
        assert ref in ids
    assert "payment:1" not in record_ids(SEED)


def test_decided_proposals_are_not_citable():
    done = dataclasses.replace(SEED, proposals=(dataclasses.replace(
        facts_mod.SEED_PROPOSAL, status="APPROVED"),))  # fmt: skip
    assert "proposal:seed" not in record_ids(done)


def test_unknown_records_flags_other_buyers_and_made_up_ids():
    assert unknown_records(SEED, ["memo_line:S-102", "Invoice:inv-1042", "proposal:seed"]) == []
    assert unknown_records(SEED, ["invoice:INV-2001", "memo_line:S-999"]) == [
        "invoice:INV-2001",
        "memo_line:S-999",
    ]


def test_prompt_has_record_ids_and_nothing_but_records():
    text = facts_prompt(PAID)
    for ref in ("memo_line:S-102", "return:R-311", "invoice:INV-1042", "memo:M-207",
                "payment:1", "proposal:seed"):  # fmt: skip
        assert ref in text
    low = text.lower()
    for banned in ("@", "password", "token", "demo_login", "secret", "example.com"):
        assert banned not in low
    # Facts has no field that could carry an email, token or setting (R1, Y3)
    names = {f.name for cls in (facts_mod.Facts, facts_mod.Line, facts_mod.Invoice,
             facts_mod.Payment, facts_mod.Proposal, facts_mod.Memo)
             for f in dataclasses.fields(cls)}  # fmt: skip
    assert not names & {"email", "statement_token", "token", "password", "settings"}


def test_triage_schema_parses_and_rejects():
    ok = TriageResult.model_validate(
        {"label": "RETURN_CLAIM", "fields": {"sku": None, "skus": ["S-102", "S-123"],
         "amount_cents": None, "date": None, "invoice_no": None, "reason": None}}
    )  # fmt: skip
    assert ok.fields.skus == ["S-102", "S-123"]
    for bad in (
        {"label": "REFUND", "fields": {}},
        {"label": "OTHER", "fields": {"amount_cents": 12.5}},
        {"label": "OTHER", "fields": {"approved": True}},
    ):
        with pytest.raises(ValidationError):
            TriageResult.model_validate(bad)
    props = TRIAGE_SCHEMA["schema"]["properties"]["fields"]
    assert set(props["required"]) == set(props["properties"])  # strict mode: all required


def test_answer_schema_limits():
    a = {"answer": "x", "cited_records": [], "confidence": "high", "offer_escalation": False}
    assert AnswerResult.model_validate(a).answer == "x"
    for bad in ({**a, "answer": "y" * 601}, {**a, "answer": ""}, {**a, "confidence": "sure"}):
        with pytest.raises(ValidationError):
            AnswerResult.model_validate(bad)
    assert set(ANSWER_SCHEMA["schema"]["required"]) == set(a)
