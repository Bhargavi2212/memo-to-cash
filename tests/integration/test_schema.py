"""Database constraints from BACKEND_SCHEMA §1 / P1 D2, D21 reject bad data (P1 step 2)."""

import secrets

import pytest
from sqlalchemy.exc import IntegrityError

from backend.core.dates import today_ny
from backend.db.models import Buyer, Invoice, InvoiceLine, Memo, MemoLine

D = today_ny()  # relative, never a hard-coded calendar date (invariant 8)


def _buyer(db, email="buyer@example.com", demo_session=None):
    b = Buyer(
        name="Northside Jewelers",
        email=email,
        statement_token=secrets.token_urlsafe(32),
        is_demo=demo_session is not None,
        demo_session=demo_session,
    )
    db.add(b)
    db.flush()
    return b


def _memo(db, buyer, memo_no="M-201"):
    m = Memo(buyer_id=buyer.id, memo_no=memo_no, sent_on=D)
    db.add(m)
    db.flush()
    return m


def _line(db, memo, sku="S-101", price=235000):
    line = MemoLine(memo_id=memo.id, sku=sku, description="Round 1.01ct", price_cents=price)
    db.add(line)
    db.flush()
    return line


def _invoice(db, buyer, memo, no="INV-1038", total=350000, paid=0):
    inv = Invoice(
        buyer_id=buyer.id,
        erp_invoice_no=no,
        paypal_invoice_number=f"{no}-{secrets.token_hex(3)}",
        memo_id=memo.id,
        issued_on=D,
        due_on=D,
        total_cents=total,
        paid_cents=paid,
    )
    db.add(inv)
    db.flush()
    return inv


def test_defaults_and_generated_uid(db):
    b = _buyer(db)
    inv = _invoice(db, b, _memo(db, b))
    db.refresh(inv)
    db.refresh(b)
    assert inv.uid is not None
    assert inv.ledger_status == "DRAFT" and inv.paid_cents == 0 and inv.mirror_attempts == 0
    assert b.demo_ready is False and b.is_demo is False


def test_duplicate_memo_no_for_same_buyer_rejected(db):
    b = _buyer(db)
    _memo(db, b)
    with pytest.raises(IntegrityError):
        _memo(db, b)


def test_same_memo_no_for_two_buyers_allowed(db):
    _memo(db, _buyer(db, "a@example.com"))
    _memo(db, _buyer(db, "b@example.com"))


def test_paid_above_total_rejected(db):
    b = _buyer(db)
    with pytest.raises(IntegrityError):
        _invoice(db, b, _memo(db, b), total=100, paid=101)


@pytest.mark.parametrize("price", [0, -100])
def test_non_positive_price_rejected(db, price):
    b = _buyer(db)
    with pytest.raises(IntegrityError):
        _line(db, _memo(db, b), price=price)


def test_duplicate_real_buyer_email_rejected(db):
    _buyer(db, "same@example.com")
    with pytest.raises(IntegrityError):
        _buyer(db, "same@example.com")


def test_same_email_in_two_demo_sessions_allowed(db):
    _buyer(db, "same@example.com", demo_session="s1")
    _buyer(db, "same@example.com", demo_session="s2")
    _buyer(db, "same@example.com")  # and one real buyer


def test_memo_line_on_two_invoices_rejected(db):
    b = _buyer(db)
    m = _memo(db, b)
    line = _line(db, m)
    inv1 = _invoice(db, b, m, "INV-1038")
    inv2 = _invoice(db, b, m, "INV-1039")
    db.add(InvoiceLine(invoice_id=inv1.id, memo_line_id=line.id, name="S-101", amount_cents=1))
    db.flush()
    db.add(InvoiceLine(invoice_id=inv2.id, memo_line_id=line.id, name="S-101", amount_cents=1))
    with pytest.raises(IntegrityError):
        db.flush()


def test_adjustment_lines_without_memo_line_allowed(db):
    b = _buyer(db)
    inv = _invoice(db, b, _memo(db, b))
    for _ in range(2):
        db.add(InvoiceLine(invoice_id=inv.id, memo_line_id=None, name="Adj", amount_cents=-2000))
    db.flush()


def test_duplicate_erp_invoice_no_for_same_buyer_rejected(db):
    b = _buyer(db)
    m = _memo(db, b)
    _invoice(db, b, m)
    with pytest.raises(IntegrityError):
        _invoice(db, b, m)
