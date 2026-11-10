"""Invoice body builder and pure helpers (P1 step 3, D7, D22, R1, R7).

Response handling (create/send/get/search/record) is tested on real, redacted sandbox
fixtures recorded by the open-checks script (step 4), not on hand-written ones.
"""

import pytest
from pydantic import SecretStr

from backend.core.config import load_settings
from backend.core.dates import offset, today_ny
from backend.paypal import invoices as inv

D = today_ny()


@pytest.fixture
def settings():
    return load_settings(
        _env_file=None,
        app_env="test",
        app_base_url="https://memo-to-cash-web.onrender.com/",
        paypal_business_email="supplier@example.com",
        paypal_client_id="id",
        paypal_client_secret=SecretStr("s"),
    )


def draft(**kw) -> inv.InvoiceDraft:
    base = dict(
        uid="11111111-2222-3333-4444-555555555555",
        paypal_invoice_number="INV-1038-k3x9q2",
        erp_invoice_no="INV-1038",
        memo_no="M-201",
        issued_on=offset(D, -48),
        due_on=offset(D, -18),
        lines=(
            inv.DraftLine("S-101", "Round 1.01ct", 235000),
            inv.DraftLine("S-104", "Stud pair", 115000),
        ),
        buyer_name="Northside Jewelers",
        buyer_email="buyer@example.com",
        statement_token="tok123",
    )
    return inv.InvoiceDraft(**(base | kw))


def test_body_dates_and_terms(settings):
    b = inv.build_body(draft(), settings, today=D)
    d = b["detail"]
    assert d["invoice_date"] == offset(D, -48).isoformat()
    assert d["payment_term"] == {
        "term_type": "DUE_ON_DATE_SPECIFIED",
        "due_date": offset(D, -18).isoformat(),
    }
    assert d["invoice_number"] == "INV-1038-k3x9q2" and d["reference"] == "M-201"
    assert d["currency_code"] == "USD"


def test_body_note_memo_names(settings):
    b = inv.build_body(draft(), settings, today=D)
    assert b["detail"]["note"].endswith("https://memo-to-cash-web.onrender.com/s/tok123")
    assert b["detail"]["memo"] == "Kept from memo M-201 · ERP INV-1038"
    assert b["invoicer"] == {
        "email_address": "supplier@example.com",
        "business_name": "Demo Diamond Co.",
    }
    assert b["primary_recipients"] == [
        {
            "billing_info": {
                "business_name": "Northside Jewelers",
                "email_address": "buyer@example.com",
            }
        }
    ]


def test_body_items_are_strings_in_dollars(settings):
    items = inv.build_body(draft(), settings, today=D)["items"]
    assert items[1] == {
        "name": "S-104 Stud pair (memo M-201)",
        "description": "SKU S-104 · memo M-201",
        "quantity": "1",
        "unit_amount": {"currency_code": "USD", "value": "1150.00"},
    }
    assert [i["unit_amount"]["value"] for i in items] == ["2350.00", "1150.00"]


def test_body_partial_payment_on(settings):
    cfg = inv.build_body(draft(), settings, today=D)["configuration"]
    assert cfg == {
        "partial_payment": {
            "allow_partial_payment": True,
            "minimum_amount_due": {"currency_code": "USD", "value": "1.00"},
        }
    }


def test_past_dates_fallback_d22(settings):
    s = settings.model_copy(update={"paypal_past_dates_ok": False})
    b = inv.build_body(draft(), s, today=D)
    assert b["detail"]["invoice_date"] == D.isoformat()
    assert b["detail"]["payment_term"]["due_date"] == D.isoformat()  # max(today, D-18)
    later = draft(issued_on=offset(D, -5), due_on=offset(D, 25))
    assert inv.expected_dates(later, s, D) == (D, offset(D, 25))
    assert inv.expected_dates(later, settings, D) == (offset(D, -5), offset(D, 25))


@pytest.mark.parametrize("field", ["app_base_url", "paypal_business_email"])
def test_body_refuses_missing_settings(settings, field):
    with pytest.raises(inv.NotConfiguredError) as e:
        inv.build_body(draft(), settings.model_copy(update={field: None}), today=D)
    assert e.value.missing == [field.upper()]


def test_invoice_id_from_href():
    href = "https://api-m.sandbox.paypal.com/v2/invoicing/invoices/INV2-ABCD-1234"
    assert inv.invoice_id_from_href(href) == "INV2-ABCD-1234"
    assert inv.invoice_id_from_href(href + "/") == "INV2-ABCD-1234"


def test_record_payment_body_has_no_payment_id():
    b = inv.record_payment_body(amount_cents=5, method="PAYPAL", payment_date=D, note="x")
    assert b == {
        "method": "PAYPAL",
        "payment_date": D.isoformat(),
        "amount": {"currency_code": "USD", "value": "0.05"},
        "note": "x",
    }
    assert "payment_id" not in b


@pytest.mark.parametrize(
    ("kw", "msg"),
    [
        ({"method": "BITCOIN"}, "method"),
        ({"amount_cents": 0}, "positive"),
        ({"amount_cents": 1.5}, "positive"),
    ],
)
def test_record_payment_body_rejects(kw, msg):
    args = dict(amount_cents=100, method="CHECK", payment_date=D, note="n") | kw
    with pytest.raises(ValueError, match=msg):
        inv.record_payment_body(**args)


@pytest.mark.anyio
async def test_record_payment_requires_mtc_token(settings):
    with pytest.raises(ValueError, match=r"\[MTC:"):
        await inv.record_payment(
            None,  # type: ignore[arg-type]  # refused before any PayPal call
            "INV2-1",
            allocation_uid="abc",
            amount_cents=100,
            method="PAYPAL",
            payment_date=D,
            note="no token here",
        )
