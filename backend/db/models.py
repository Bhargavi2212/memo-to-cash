"""SQLAlchemy models for every table in BACKEND_SCHEMA.md §1 (P1 step 2).

Money is integer cents (BIGINT). `uid` columns get a Python uuid4 and a server
`gen_random_uuid()` default, so rows have one whether they're made here or in SQL.
"""

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

LINE_STATUS = ("OUT", "RETURNED", "KEPT")
LEDGER_STATUS = ("DRAFT", "OPEN", "PARTIALLY_PAID", "PAID", "CANCELLED")
PAYMENT_SOURCE = ("STATEMENT", "PAYPAL_INVOICE_PAGE", "OFF_PAYPAL")
PROPOSAL_KIND = ("ADJUSTMENT", "RETURN_CHECK", "REMINDER", "REPLY", "PROMISE_NOTE")
PROPOSAL_STATUS = ("PENDING", "APPROVED", "REJECTED", "DONE", "FAILED")
ACTOR = ("system", "supplier", "buyer", "agent", "paypal")

line_status = Enum(*LINE_STATUS, name="line_status")
ledger_status = Enum(*LEDGER_STATUS, name="ledger_status")
payment_source = Enum(*PAYMENT_SOURCE, name="payment_source")
proposal_kind = Enum(*PROPOSAL_KIND, name="proposal_kind")
proposal_status = Enum(*PROPOSAL_STATUS, name="proposal_status")
actor = Enum(*ACTOR, name="actor")

ENUMS = (line_status, ledger_status, payment_source, proposal_kind, proposal_status, actor)


class Base(DeclarativeBase):
    type_annotation_map = {int: BigInteger, dict[str, Any]: JSONB}


def _uid() -> Mapped[uuid.UUID]:
    return mapped_column(
        Uuid,
        unique=True,
        nullable=False,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )


def _now() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class Buyer(Base):
    __tablename__ = "buyers"
    __table_args__ = (
        Index(
            "buyers_real_email", "email", unique=True, postgresql_where=text("demo_session IS NULL")
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    email: Mapped[str] = mapped_column(Text)
    statement_token: Mapped[str] = mapped_column(Text, unique=True)
    is_demo: Mapped[bool] = mapped_column(Boolean, server_default=text("false"), default=False)
    demo_session: Mapped[str | None] = mapped_column(Text)
    demo_ready: Mapped[bool] = mapped_column(Boolean, server_default=text("false"), default=False)
    created_at: Mapped[datetime] = _now()


class Memo(Base):
    __tablename__ = "memos"
    __table_args__ = (UniqueConstraint("buyer_id", "memo_no"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    buyer_id: Mapped[int] = mapped_column(ForeignKey("buyers.id"))
    memo_no: Mapped[str] = mapped_column(Text)
    sent_on: Mapped[date] = mapped_column(Date)
    due_back_on: Mapped[date | None] = mapped_column(Date)


class MemoLine(Base):
    __tablename__ = "memo_lines"
    __table_args__ = (
        UniqueConstraint("memo_id", "sku"),
        CheckConstraint("price_cents > 0", name="memo_lines_price_positive"),
        Index("ix_memo_lines_sku", "sku"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    memo_id: Mapped[int] = mapped_column(ForeignKey("memos.id"))
    sku: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text)
    price_cents: Mapped[int]
    status: Mapped[str] = mapped_column(line_status, server_default="OUT", default="OUT")
    returned_on: Mapped[date | None] = mapped_column(Date)
    return_ref: Mapped[str | None] = mapped_column(Text)


class Invoice(Base):
    __tablename__ = "invoices"
    __table_args__ = (
        UniqueConstraint("buyer_id", "erp_invoice_no"),
        CheckConstraint("total_cents >= 0", name="invoices_total_nonneg"),
        CheckConstraint("paid_cents >= 0", name="invoices_paid_nonneg"),
        CheckConstraint("paid_cents <= total_cents", name="invoices_paid_le_total"),
        Index("ix_invoices_buyer_status_due", "buyer_id", "ledger_status", "due_on"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    buyer_id: Mapped[int] = mapped_column(ForeignKey("buyers.id"))
    erp_invoice_no: Mapped[str] = mapped_column(Text)
    uid: Mapped[uuid.UUID] = _uid()
    paypal_invoice_number: Mapped[str] = mapped_column(Text, unique=True)
    memo_id: Mapped[int] = mapped_column(ForeignKey("memos.id"))
    paypal_invoice_id: Mapped[str | None] = mapped_column(Text, unique=True)
    payer_view_url: Mapped[str | None] = mapped_column(Text)
    issued_on: Mapped[date] = mapped_column(Date)
    due_on: Mapped[date] = mapped_column(Date)
    total_cents: Mapped[int]
    paid_cents: Mapped[int] = mapped_column(server_default="0", default=0)
    ledger_status: Mapped[str] = mapped_column(
        ledger_status, server_default="DRAFT", default="DRAFT"
    )
    paypal_status: Mapped[str | None] = mapped_column(Text)
    paypal_due_cents: Mapped[int | None]
    paypal_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    mismatch: Mapped[str | None] = mapped_column(Text)
    mirror_error: Mapped[str | None] = mapped_column(Text)
    mirror_attempts: Mapped[int] = mapped_column(Integer, server_default="0", default=0)


class InvoiceLine(Base):
    __tablename__ = "invoice_lines"
    __table_args__ = (
        Index(
            "invoice_lines_inv_line",
            "invoice_id",
            "memo_line_id",
            unique=True,
            postgresql_where=text("memo_line_id IS NOT NULL"),
        ),
        Index(
            "invoice_lines_one_invoice",
            "memo_line_id",
            unique=True,
            postgresql_where=text("memo_line_id IS NOT NULL"),
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"))
    memo_line_id: Mapped[int | None] = mapped_column(ForeignKey("memo_lines.id"))
    name: Mapped[str] = mapped_column(Text)
    amount_cents: Mapped[int]  # negative for adjustments


class Payment(Base):
    __tablename__ = "payments"
    __table_args__ = (
        CheckConstraint("amount_cents > 0", name="payments_amount_positive"),
        CheckConstraint("unapplied_cents >= 0", name="payments_unapplied_nonneg"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    uid: Mapped[uuid.UUID] = _uid()
    buyer_id: Mapped[int] = mapped_column(ForeignKey("buyers.id"))
    source: Mapped[str] = mapped_column(payment_source)
    paypal_order_id: Mapped[str | None] = mapped_column(Text, unique=True)
    paypal_capture_id: Mapped[str | None] = mapped_column(Text, unique=True)
    amount_cents: Mapped[int]
    status: Mapped[str] = mapped_column(Text)
    planned_split: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    method: Mapped[str | None] = mapped_column(Text)
    reference: Mapped[str | None] = mapped_column(Text)
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    unapplied_cents: Mapped[int] = mapped_column(server_default="0", default=0)


class Allocation(Base):
    __tablename__ = "allocations"
    __table_args__ = (
        UniqueConstraint("payment_id", "invoice_id"),
        CheckConstraint("amount_cents > 0", name="allocations_amount_positive"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    uid: Mapped[uuid.UUID] = _uid()
    payment_id: Mapped[int] = mapped_column(ForeignKey("payments.id"))
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"))
    amount_cents: Mapped[int]
    record_state: Mapped[str] = mapped_column(Text, server_default="PENDING", default="PENDING")
    paypal_recorded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paypal_transaction_id: Mapped[str | None] = mapped_column(Text, unique=True)
    record_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paypal_record_error: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, server_default="0", default=0)


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (CheckConstraint("length(text) <= 2000", name="messages_text_len"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    buyer_id: Mapped[int] = mapped_column(ForeignKey("buyers.id"))
    text: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text)  # QUESTION_UI | MESSAGE
    ai_result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _now()


class Proposal(Base):
    __tablename__ = "proposals"
    id: Mapped[int] = mapped_column(primary_key=True)
    uid: Mapped[uuid.UUID] = _uid()
    buyer_id: Mapped[int] = mapped_column(ForeignKey("buyers.id"))
    kind: Mapped[str] = mapped_column(proposal_kind)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB)
    source_message_id: Mapped[int | None] = mapped_column(ForeignKey("messages.id"))
    status: Mapped[str] = mapped_column(
        proposal_status, server_default="PENDING", default="PENDING"
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class ReminderSent(Base):
    __tablename__ = "reminders_sent"
    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"))
    sent_at: Mapped[datetime] = _now()


class WebhookEvent(Base):
    __tablename__ = "webhook_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    paypal_event_id: Mapped[str] = mapped_column(Text, unique=True)
    event_type: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    received_at: Mapped[datetime] = _now()
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[datetime] = _now()
    actor: Mapped[str] = mapped_column(actor)
    action: Mapped[str] = mapped_column(Text)
    ref: Mapped[str | None] = mapped_column(Text)
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class DemoRate(Base):
    __tablename__ = "demo_rate"
    ip_hash: Mapped[str] = mapped_column(Text, primary_key=True)  # sha256(ip + secret) or 'global'
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    count: Mapped[int] = mapped_column(Integer, server_default="0", default=0)


class LlmUsage(Base):
    __tablename__ = "llm_usage"
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    scope: Mapped[str] = mapped_column(Text, primary_key=True)  # 'global' or token hash
    calls: Mapped[int] = mapped_column(Integer, server_default="0", default=0)
