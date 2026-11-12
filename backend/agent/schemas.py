"""Agent output schemas (BACKEND_SCHEMA §5): Pydantic models that parse the reply, and the
strict JSON schemas sent as `response_format`. Invalid → the caller's safe fallback (CLAUDE §5).

Length limits are enforced by Pydantic, not sent on the wire (`maxLength` under strict mode on
the Meta Model API is unproven): an over-long answer fails validation → fallback.
"""

from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, Field

Label = Literal[
    "QUESTION", "RETURN_CLAIM", "DEDUCTION_REQUEST", "PAYMENT_PROMISE", "DISPUTE", "OTHER"
]
LABELS: list[str] = list(get_args(Label))
_STR = {"type": ["string", "null"]}


class TriageFields(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sku: str | None = None  # deductions and questions
    skus: list[str] = []  # every item a RETURN_CLAIM names (Z7)
    amount_cents: int | None = None
    date: str | None = None
    invoice_no: str | None = None
    reason: str | None = None


class TriageResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: Label
    fields: TriageFields


class AnswerResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answer: str = Field(min_length=1, max_length=600)
    cited_records: list[str]
    confidence: Literal["low", "med", "high"]
    offer_escalation: bool


TRIAGE_SCHEMA = {
    "name": "triage",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "label": {"type": "string", "enum": LABELS},
            "fields": {
                "type": "object",
                "properties": {
                    "sku": _STR,
                    "skus": {"type": "array", "items": {"type": "string"}},
                    "amount_cents": {"type": ["integer", "null"]},
                    "date": _STR,
                    "invoice_no": _STR,
                    "reason": _STR,
                },
                "required": ["sku", "skus", "amount_cents", "date", "invoice_no", "reason"],
                "additionalProperties": False,
            },
        },
        "required": ["label", "fields"],
        "additionalProperties": False,
    },
}

ANSWER_SCHEMA = {
    "name": "answer",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "answer": {"type": "string"},
            "cited_records": {"type": "array", "items": {"type": "string"}},
            "confidence": {"type": "string", "enum": ["low", "med", "high"]},
            "offer_escalation": {"type": "boolean"},
        },
        "required": ["answer", "cited_records", "confidence", "offer_escalation"],
        "additionalProperties": False,
    },
}


class ReminderDraft(BaseModel):
    """Job C (P3b B6; BACKEND_SCHEMA §5): a payment reminder the supplier approves before PayPal
    sends it. `send_after` is shown as a suggested date only (plan G11)."""

    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=500)
    send_after: str  # YYYY-MM-DD, checked by code (≥ today)


REMINDER_SCHEMA = {
    "name": "reminder",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {"text": {"type": "string"}, "send_after": {"type": "string"}},
        "required": ["text", "send_after"],
        "additionalProperties": False,
    },
}
