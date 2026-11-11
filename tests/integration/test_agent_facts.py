"""P4 step 1 — Facts from the database equal Facts from the seed CSV (R1, Y3, Z8)."""

from datetime import UTC, datetime

import pytest
from pydantic import SecretStr

from backend.agent.facts import facts_for_buyer, facts_from_seed
from backend.agent.tools import facts_prompt
from backend.core.config import load_settings
from backend.core.dates import today_ny
from backend.core.split import split_payment
from backend.db.models import Allocation, Buyer, Payment
from backend.services.statement import open_invoices
from tests.demo_copy import demo_copy

D = today_ny()
BUYER_EMAIL = "sb-buyer@personal.example.com"


def settings():
    return load_settings(
        _env_file=None,
        app_env="test",
        demo_mode=True,
        sandbox_buyer_email=BUYER_EMAIL,
        sandbox_buyer_password=SecretStr("sandbox-pass"),
    )


@pytest.fixture
def buyer_id(db):
    """A demo copy exactly as /demo/start writes it: seed import + seeded message + proposal."""
    bid, _token = demo_copy(settings(), "sess-facts", D)
    return bid


def test_db_facts_equal_seed_facts(db, buyer_id):
    assert facts_for_buyer(db, buyer_id, D) == facts_from_seed(D)


def test_db_facts_equal_seed_facts_after_a_payment(db, buyer_id):
    rows, open_ = open_invoices(db, buyer_id)
    pay = Payment(
        buyer_id=buyer_id, source="STATEMENT", amount_cents=500000, status="CAPTURED",
        received_at=datetime.now(UTC),
    )  # fmt: skip
    db.add(pay)
    db.flush()
    by_id = {i.id: i for i in rows}
    for inv_id, cents in split_payment(500000, open_, [o.id for o in open_]):
        db.add(Allocation(payment_id=pay.id, invoice_id=inv_id, amount_cents=cents))
        by_id[inv_id].paid_cents += cents
    db.commit()
    assert pay.id == 1  # identity restarts per test; the seed variant calls it payment 1
    assert facts_for_buyer(db, buyer_id, D) == facts_from_seed(D, 500000)


def test_prompt_from_db_has_no_email_token_or_password(db, buyer_id):
    buyer = db.get(Buyer, buyer_id)
    text = facts_prompt(facts_for_buyer(db, buyer_id, D))
    assert BUYER_EMAIL not in text and buyer.email not in text
    assert buyer.statement_token not in text and "sandbox-pass" not in text
    assert "@" not in text and "proposal:seed" in text


def test_other_buyers_records_never_leak(db, buyer_id):
    other, _ = demo_copy(settings(), "sess-other", D)
    mine = facts_for_buyer(db, buyer_id, D)
    assert facts_for_buyer(db, other, D) == mine  # same seed, separate rows
    assert len(mine.invoices) == 3 and len(mine.proposals) == 1


def test_unknown_buyer(db):
    with pytest.raises(LookupError):
        facts_for_buyer(db, 999, D)
