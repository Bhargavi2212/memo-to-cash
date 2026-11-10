"""Mirror service (FR-2; P1 D4–D6, D22) against the fake PayPal (tests/paypal_fake.py)."""

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import func, select

from backend.core.config import load_settings
from backend.core.dates import today_ny
from backend.db.models import AuditLog, Buyer, Invoice
from backend.db.session import SessionLocal
from backend.paypal import client as ppclient
from backend.paypal import invoices as pinv
from backend.paypal.client import PayPal
from backend.services.demo import build_csv
from backend.services.importer import import_csv
from backend.services.mirror import mirror_invoices
from tests.paypal_fake import FakePayPal, error

pytestmark = pytest.mark.anyio
D = today_ny()


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch):
    async def instant(_s):
        return None

    monkeypatch.setattr(ppclient, "_sleep", instant)
    ppclient.clear_token_cache()


@pytest.fixture
def settings():
    return load_settings(
        _env_file=None,
        app_env="test",
        app_base_url="https://memo-to-cash-web.onrender.com",
        paypal_business_email="supplier@example.com",
        paypal_client_id="id",
        paypal_client_secret=SecretStr("s"),
    )


@pytest.fixture
def fake():
    return FakePayPal()


@pytest.fixture
def buyer_id(db):
    res = import_csv(db, build_csv(D, "buyer@example.com"))
    assert res.ok
    db.commit()
    return res.buyer_ids[0]


async def run(buyer_id, fake, settings):
    pp = PayPal(settings, transport=fake.transport())
    return await mirror_invoices(buyer_id, pp, settings, SessionLocal, today=D)


def invoices(db):
    db.expire_all()
    return {i.erp_invoice_no: i for i in db.scalars(select(Invoice))}


async def test_all_invoices_mirrored_with_two_calls_each(db, buyer_id, fake, settings):
    res = await run(buyer_id, fake, settings)
    assert res.ok and sorted(res.mirrored) == ["INV-1038", "INV-1042", "INV-1051"]
    assert sorted(fake.ops()) == ["create"] * 3 + ["send"] * 3  # no search, no GET (D4, R6)
    for i in invoices(db).values():
        assert i.ledger_status == "OPEN" and i.paypal_status == "UNPAID"
        assert i.paypal_invoice_id.startswith("INV2-FAKE-")
        assert i.payer_view_url.endswith(i.paypal_invoice_id)
        assert i.paypal_due_cents == i.total_cents and i.mismatch is None
        assert i.mirror_error is None and i.paypal_synced_at is not None
    sent = fake.by_number(invoices(db)["INV-1038"].paypal_invoice_number)
    token = db.scalar(select(Buyer.statement_token))
    assert sent["detail"]["note"].endswith(f"https://memo-to-cash-web.onrender.com/s/{token}")
    assert db.scalar(select(func.count()).where(AuditLog.action == "invoice.mirrored")) == 3


async def test_rerun_creates_nothing(db, buyer_id, fake, settings):
    await run(buyer_id, fake, settings)
    fake.calls.clear()
    res = await run(buyer_id, fake, settings)
    assert res.ok and res.mirrored == [] and fake.calls == []
    assert len(fake.invoices) == 3


async def test_send_failed_then_next_run_only_sends(db, buyer_id, fake, settings):
    fake.fail("send", error(400, "INVALID_REQUEST"))
    res = await run(buyer_id, fake, settings)
    assert not res.ok and not res.transient
    failed_no, err = res.errors[0]
    inv = invoices(db)[failed_no]
    assert inv.paypal_invoice_id and inv.paypal_status == "DRAFT"  # D5: id kept
    assert inv.ledger_status == "DRAFT" and inv.mirror_attempts == 1
    assert "INVALID_REQUEST" in inv.mirror_error and "fake-debug" in inv.mirror_error

    fake.calls.clear()
    res = await run(buyer_id, fake, settings)
    assert res.ok and res.mirrored == [failed_no]
    assert fake.ops() == ["get", "send"]  # no second create
    assert len(fake.invoices) == 3
    assert invoices(db)[failed_no].ledger_status == "OPEN"


async def test_duplicate_number_on_first_attempt_is_adopted(db, buyer_id, fake, settings):
    """A create whose answer was lost: PayPal has it, we don't (open checks 10/11)."""
    inv = invoices(db)["INV-1042"]
    fake.add_existing({"detail": {"invoice_number": inv.paypal_invoice_number}, "items": []})
    res = await run(buyer_id, fake, settings)
    assert res.ok
    assert fake.ops().count("create") == 3 and fake.ops().count("search") == 1
    assert len(fake.invoices) == 3  # adopted, not duplicated
    assert (
        invoices(db)["INV-1042"].paypal_invoice_id
        == fake.by_number(inv.paypal_invoice_number)["id"]
    )


async def test_resume_searches_before_create(db, buyer_id, fake, settings):
    inv = invoices(db)["INV-1051"]
    inv.mirror_attempts = 1
    db.commit()
    existing = fake.add_existing(
        {"detail": {"invoice_number": inv.paypal_invoice_number}, "items": []}
    )
    await run(buyer_id, fake, settings)
    assert ("search", inv.paypal_invoice_number) in fake.calls
    assert ("create", inv.paypal_invoice_number) not in fake.calls
    assert invoices(db)["INV-1051"].paypal_invoice_id == existing["id"]


async def test_resume_without_match_creates(db, buyer_id, fake, settings):
    inv = invoices(db)["INV-1051"]
    inv.mirror_attempts = 2
    db.commit()
    res = await run(buyer_id, fake, settings)
    assert res.ok and ("create", inv.paypal_invoice_number) in fake.calls


async def test_transient_vs_deterministic(db, buyer_id, fake, settings):
    fake.fail("create", *[error(503)] * 4)  # all retries of the first create fail
    res = await run(buyer_id, fake, settings)
    assert res.errors and res.transient
    assert all(e.status == 503 and e.transient for _, e in res.errors)

    fake2 = FakePayPal()
    fake2.fail("create", error(400, "VALIDATION_ERROR"))
    res2 = await run(buyer_id, fake2, settings)
    assert not res2.ok and not res2.transient


async def test_timeouts_are_transient(db, buyer_id, fake, settings):
    fake.fail("send", *[httpx.ReadTimeout("slow")] * 4)
    res = await run(buyer_id, fake, settings)
    assert res.transient and res.errors[0][1].name == "TIMEOUT"


async def test_due_mismatch_is_flagged_not_overwritten(db, buyer_id, fake, settings):
    inv = invoices(db)["INV-1038"]
    fake.due_override[inv.paypal_invoice_number] = "3400.00"
    res = await run(buyer_id, fake, settings)
    assert res.ok
    got = invoices(db)["INV-1038"]
    assert got.mismatch and "340000" in got.mismatch and "350000" in got.mismatch
    assert got.total_cents == 350000 and got.paid_cents == 0  # ours untouched (ledger rule)


async def test_past_dates_fallback_has_no_false_mismatch(db, buyer_id, fake, settings):
    s = settings.model_copy(update={"paypal_past_dates_ok": False})
    res = await run(buyer_id, fake, s)
    assert res.ok
    sent = fake.by_number(invoices(db)["INV-1038"].paypal_invoice_number)
    assert sent["detail"]["invoice_date"] == D.isoformat()
    assert all(i.mismatch is None for i in invoices(db).values())
    assert invoices(db)["INV-1038"].due_on < D  # our dates stay the truth


async def test_refuses_without_app_base_url(db, buyer_id, fake, settings):
    with pytest.raises(pinv.NotConfiguredError) as e:
        await run(buyer_id, fake, settings.model_copy(update={"app_base_url": None}))
    assert e.value.missing == ["APP_BASE_URL"] and fake.calls == []
