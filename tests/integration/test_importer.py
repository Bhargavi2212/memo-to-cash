"""CSV importer (FR-1, BACKEND_SCHEMA §6, P1 D20) on the real seed file."""

import csv
import io
import re

import pytest
from sqlalchemy import func, select

from backend.core.dates import offset, today_ny
from backend.db.models import AuditLog, Buyer, Invoice, InvoiceLine, Memo, MemoLine
from backend.services.demo import COLUMNS, build_csv
from backend.services.importer import import_csv

D = today_ny()
EMAIL = "buyer@example.com"


def seed_rows() -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(build_csv(D, EMAIL))))


def to_csv(rows: list[dict[str, str]]) -> str:
    out = io.StringIO()
    w = csv.DictWriter(out, fieldnames=COLUMNS, lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    return out.getvalue()


def counts(db) -> tuple[int, ...]:
    return tuple(
        db.scalar(select(func.count()).select_from(m))
        for m in (Buyer, Memo, MemoLine, Invoice, InvoiceLine)
    )


def find(rows, **match) -> dict[str, str]:
    return next(r for r in rows if all(r[k] == v for k, v in match.items()))


def test_seed_file_imports_exactly(db):
    res = import_csv(db, build_csv(D, EMAIL))
    assert res.ok, res.errors
    assert res.created == {"buyers": 1, "memos": 3, "lines": 12, "invoices": 3}
    assert res.updated == {"buyers": 0, "memos": 0, "lines": 0, "invoices": 0}
    assert counts(db) == (1, 3, 12, 3, 5)

    status = dict(db.execute(select(MemoLine.sku, MemoLine.status)).all())
    assert sorted(status.values()).count("KEPT") == 5
    assert sorted(status.values()).count("RETURNED") == 5
    assert sorted(status.values()).count("OUT") == 2
    assert status["S-102"] == "RETURNED" and status["S-103"] == "OUT" and status["S-120"] == "KEPT"
    s102 = db.scalars(select(MemoLine).where(MemoLine.sku == "S-102")).one()
    assert (s102.returned_on, s102.return_ref) == (offset(D, -38), "R-311")

    invs = {i.erp_invoice_no: i for i in db.scalars(select(Invoice))}
    assert {k: v.total_cents for k, v in invs.items()} == {
        "INV-1038": 350000,
        "INV-1042": 350000,
        "INV-1051": 142000,
    }
    assert sum(i.total_cents for i in invs.values()) == 842000
    assert (invs["INV-1038"].issued_on, invs["INV-1038"].due_on) == (offset(D, -48), offset(D, -18))
    for i in invs.values():
        assert re.fullmatch(rf"{i.erp_invoice_no}-[a-z0-9]{{6}}", i.paypal_invoice_number)
        assert i.ledger_status == "DRAFT" and i.paypal_invoice_id is None

    b = db.scalars(select(Buyer)).one()
    assert len(b.statement_token) >= 43 and b.demo_session is None and not b.is_demo
    assert db.scalar(select(func.count()).where(AuditLog.action == "import.completed")) == 1


def test_same_file_again_changes_nothing(db):
    text = build_csv(D, EMAIL)
    assert import_csv(db, text).ok
    numbers = sorted(db.scalars(select(Invoice.paypal_invoice_number)))
    res = import_csv(db, text)
    assert res.ok, res.errors
    assert res.created == dict.fromkeys(res.created, 0)
    assert res.updated == dict.fromkeys(res.updated, 0)
    assert counts(db) == (1, 3, 12, 3, 5)
    assert sorted(db.scalars(select(Invoice.paypal_invoice_number))) == numbers


def test_each_demo_copy_is_a_new_buyer(db):
    text = build_csv(D, EMAIL)
    a = import_csv(db, text, demo_session="s1")
    b = import_csv(db, text, demo_session="s2")
    assert a.ok and b.ok and a.buyer_ids != b.buyer_ids
    assert counts(db) == (2, 6, 24, 6, 10)
    assert {x.demo_session for x in db.scalars(select(Buyer))} == {"s1", "s2"}


def test_real_import_updates_existing_buyer(db):
    import_csv(db, build_csv(D, EMAIL))
    rows = seed_rows()
    find(rows, record_type="BUYER")["buyer_name"] = "Northside Jewelers LLC"
    find(rows, record_type="LINE", sku="S-103")["price"] = "850"
    res = import_csv(db, to_csv(rows))
    assert res.ok, res.errors
    assert res.updated["buyers"] == 1 and res.updated["lines"] == 1 and res.created["buyers"] == 0
    assert db.scalars(select(Buyer.name)).one() == "Northside Jewelers LLC"


def test_unmirrored_invoice_dates_can_change(db):
    import_csv(db, build_csv(D, EMAIL))
    rows = seed_rows()
    find(rows, record_type="INVOICE", invoice_no="INV-1042")["due_on"] = offset(D, 12).isoformat()
    res = import_csv(db, to_csv(rows))
    assert res.ok and res.updated["invoices"] == 1


def test_mirrored_invoice_is_immutable(db):
    import_csv(db, build_csv(D, EMAIL))
    inv = db.scalars(select(Invoice).where(Invoice.erp_invoice_no == "INV-1038")).one()
    inv.paypal_invoice_id = "INV2-TEST-1"
    db.flush()
    before = counts(db)

    assert import_csv(db, build_csv(D, EMAIL)).ok  # unchanged file is fine

    for change in (
        lambda rows: find(rows, record_type="INVOICE", invoice_no="INV-1038").update(
            due_on=offset(D, -10).isoformat()
        ),
        lambda rows: find(rows, record_type="LINE", sku="S-101").update(price="2400"),
    ):
        rows = seed_rows()
        change(rows)
        res = import_csv(db, to_csv(rows))
        assert not res.ok
        assert any("INV-1038" in e["msg"] and "PayPal" in e["msg"] for e in res.errors)
        assert counts(db) == before


def _with(rows, *, drop=None, add=None, edit=None):
    rows = [dict(r) for r in rows]
    if edit:
        match, values = edit
        find(rows, **match).update(values)
    if drop:
        rows.remove(find(rows, **drop))
    if add:
        rows.append({c: "" for c in COLUMNS} | add)
    return rows


def _row_of(rows, **match) -> int:
    return rows.index(find(rows, **match)) + 2  # header is row 1


BAD_CASES = {
    "unknown record_type": lambda r: _with(r, add={"record_type": "PAYMENT"}),
    "missing value": lambda r: _with(
        r, edit=({"record_type": "LINE", "sku": "S-101"}, {"description": ""})
    ),
    "bad date": lambda r: _with(
        r, edit=({"record_type": "MEMO", "memo_no": "M-201"}, {"sent_on": "03/01/2026"})
    ),
    "impossible date": lambda r: _with(
        r, edit=({"record_type": "MEMO", "memo_no": "M-201"}, {"sent_on": "2026-02-30"})
    ),
    "bad price": lambda r: _with(
        r, edit=({"record_type": "LINE", "sku": "S-101"}, {"price": "2,350"})
    ),
    "three decimals": lambda r: _with(
        r, edit=({"record_type": "LINE", "sku": "S-101"}, {"price": "2350.001"})
    ),
    "zero price": lambda r: _with(
        r, edit=({"record_type": "LINE", "sku": "S-101"}, {"price": "0"})
    ),
    "line on unknown memo": lambda r: _with(
        r,
        add={
            "record_type": "LINE",
            "memo_no": "M-999",
            "sku": "S-190",
            "description": "X",
            "price": "10",
        },
    ),
    "return on unknown memo": lambda r: _with(
        r,
        add={
            "record_type": "RETURN",
            "memo_no": "M-999",
            "sku": "S-190",
            "returned_on": D.isoformat(),
            "return_ref": "R-1",
        },
    ),
    "invoice on unknown memo": lambda r: _with(
        r, edit=({"record_type": "INVOICE", "invoice_no": "INV-1051"}, {"memo_no": "M-999"})
    ),
    "return sku without LINE": lambda r: _with(
        r,
        add={
            "record_type": "RETURN",
            "memo_no": "M-201",
            "sku": "S-199",
            "returned_on": D.isoformat(),
            "return_ref": "R-1",
        },
    ),
    "invoice line on unknown invoice": lambda r: _with(
        r,
        add={
            "record_type": "INVOICE_LINE",
            "invoice_no": "INV-1999",
            "memo_no": "M-212",
            "sku": "S-123",
        },
    ),
    "memo for unknown buyer": lambda r: _with(
        r, edit=({"record_type": "MEMO", "memo_no": "M-212"}, {"buyer_email": "nobody@example.com"})
    ),
    "duplicate memo": lambda r: _with(
        r,
        add={
            "record_type": "MEMO",
            "memo_no": "M-201",
            "buyer_email": EMAIL,
            "sent_on": D.isoformat(),
        },
    ),
    "duplicate line": lambda r: _with(
        r,
        add={
            "record_type": "LINE",
            "memo_no": "M-201",
            "sku": "S-101",
            "description": "X",
            "price": "10",
        },
    ),
    "duplicate invoice": lambda r: _with(
        r,
        add={
            "record_type": "INVOICE",
            "invoice_no": "INV-1038",
            "memo_no": "M-201",
            "issued_on": D.isoformat(),
            "due_on": D.isoformat(),
        },
    ),
    "duplicate invoice line": lambda r: _with(
        r,
        add={
            "record_type": "INVOICE_LINE",
            "invoice_no": "INV-1038",
            "memo_no": "M-201",
            "sku": "S-101",
        },
    ),
    "invoice line sku without LINE": lambda r: _with(
        r,
        add={
            "record_type": "INVOICE_LINE",
            "invoice_no": "INV-1051",
            "memo_no": "M-212",
            "sku": "S-199",
        },
    ),
    "line on two invoices": lambda r: (
        _with(
            r,
            add={
                "record_type": "INVOICE_LINE",
                "invoice_no": "INV-1050",
                "memo_no": "M-212",
                "sku": "S-120",
            },
        )
        + [
            {c: "" for c in COLUMNS}
            | {
                "record_type": "INVOICE",
                "invoice_no": "INV-1050",
                "memo_no": "M-212",
                "issued_on": D.isoformat(),
                "due_on": D.isoformat(),
            }
        ]
    ),
    "sku invoiced and returned": lambda r: _with(
        r,
        add={
            "record_type": "INVOICE_LINE",
            "invoice_no": "INV-1051",
            "memo_no": "M-212",
            "sku": "S-121",
        },
    ),
    "invoice with zero lines": lambda r: _with(
        r,
        add={
            "record_type": "INVOICE",
            "invoice_no": "INV-1060",
            "memo_no": "M-212",
            "issued_on": D.isoformat(),
            "due_on": D.isoformat(),
        },
    ),
    "issued after due": lambda r: _with(
        r,
        edit=(
            {"record_type": "INVOICE", "invoice_no": "INV-1042"},
            {"issued_on": offset(D, 11).isoformat()},
        ),
    ),
    "invoice line from another memo": lambda r: _with(
        r,
        add={
            "record_type": "INVOICE_LINE",
            "invoice_no": "INV-1051",
            "memo_no": "M-201",
            "sku": "S-103",
        },
    ),
}


@pytest.mark.parametrize("case", list(BAD_CASES))
def test_validation_error_writes_nothing(db, case):
    rows = BAD_CASES[case](seed_rows())
    res = import_csv(db, to_csv(rows))
    assert not res.ok
    assert all(isinstance(e["row"], int) and e["row"] >= 1 and e["msg"] for e in res.errors)
    assert counts(db) == (0, 0, 0, 0, 0)


def test_error_reports_the_right_row(db):
    rows = seed_rows()
    rows = _with(rows, edit=({"record_type": "LINE", "sku": "S-110"}, {"price": "abc"}))
    res = import_csv(db, to_csv(rows))
    assert res.errors == [
        {"row": _row_of(rows, record_type="LINE", sku="S-110"), "msg": res.errors[0]["msg"]}
    ]
    assert "price" in res.errors[0]["msg"]


def test_all_errors_returned_at_once(db):
    rows = seed_rows()
    rows = _with(rows, edit=({"record_type": "LINE", "sku": "S-110"}, {"price": "abc"}))
    rows = _with(rows, edit=({"record_type": "MEMO", "memo_no": "M-201"}, {"sent_on": "x"}))
    assert len(import_csv(db, to_csv(rows)).errors) == 2


def test_header_missing_column(db):
    res = import_csv(db, "record_type,buyer_name\nBUYER,X\n")
    assert res.errors[0]["row"] == 1 and "missing columns" in res.errors[0]["msg"]


# --- Phase M (P3 §M, M1): three buyers per demo copy ---------------------------------------

# sha256 of build_csv(date(2026, 1, 15), EMAIL) before phase M: the agent eval's Facts are built
# from build_csv, so it must stay Northside-only and byte-identical (PM-plan G2). Fixed input
# date on purpose — this is a fingerprint, not demo data.
NORTHSIDE_CSV_SHA = "c3adffe4790e105a6d98f1439ac7e05bf4962ffd3076d51f805c5aab7a51f392"


def test_build_csv_is_unchanged_northside_only():
    import hashlib
    from datetime import date

    text = build_csv(date(2026, 1, 15), EMAIL)
    assert hashlib.sha256(text.encode()).hexdigest() == NORTHSIDE_CSV_SHA


def test_demo_csv_has_three_real_buyers(db):
    from backend.services.demo import build_demo_csv

    res = import_csv(db, build_demo_csv(D, "judge@example.com"))
    assert res.ok, res.errors
    assert res.created == {"buyers": 3, "memos": 7, "lines": 23, "invoices": 7}
    buyers = {b.name: b for b in db.scalars(select(Buyer))}
    assert {n: b.email for n, b in buyers.items()} == {
        "Northside Jewelers": "judge@example.com",
        "Harbor Gems": "judge+harbor@example.com",
        "Lakeside Fine Jewelry": "judge+lakeside@example.com",
    }
    assert len({b.statement_token for b in buyers.values()}) == 3

    def summary(b):
        invs = list(db.scalars(select(Invoice).where(Invoice.buyer_id == b.id)))
        lines = list(db.scalars(select(MemoLine).join(Memo).where(Memo.buyer_id == b.id)))
        st = [ln.status for ln in lines]
        return (
            sum(i.total_cents for i in invs),
            max((D - i.issued_on).days for i in invs),
            sum(i.total_cents for i in invs if i.due_on < D),
            (st.count("OUT"), st.count("KEPT"), st.count("RETURNED")),
        )

    assert summary(buyers["Northside Jewelers"]) == (842000, 48, 350000, (2, 5, 5))
    assert summary(buyers["Harbor Gems"]) == (230000, 12, 0, (1, 2, 2))
    assert summary(buyers["Lakeside Fine Jewelry"]) == (596000, 31, 350000, (1, 4, 1))
    harbor = {i.erp_invoice_no: i.total_cents for i in db.scalars(
        select(Invoice).where(Invoice.buyer_id == buyers["Harbor Gems"].id))}  # fmt: skip
    assert harbor == {"INV-1055": 145000, "INV-1058": 85000}
    # memo, SKU and invoice numbers are unique across the copy
    assert db.scalar(select(func.count(func.distinct(Invoice.erp_invoice_no)))) == 7
    assert db.scalar(select(func.count(func.distinct(MemoLine.sku)))) == 23
