# ruff: noqa: F811 — the fixtures are shared from test_supplier_api
"""Supplier import (FR-1; P3b B7, F6–F8, G6, G7, G20; plan step 3). Admin only; the importer
validates first; new invoices go on PayPal through phase M's setup path within 20 s."""

from sqlalchemy import func, select

from backend.core.dates import today_ny
from backend.db.models import Buyer, Invoice
from backend.services import imports
from tests.integration.test_supplier_api import (  # noqa: F401 — fixtures
    AUTH,
    clients,
    fake,
    no_backoff,
    settings,
    start,
)


def upload(c, text: str | bytes, headers=AUTH):
    data = text.encode() if isinstance(text, str) else text
    return c.post(
        "/api/supplier/import", files={"file": ("export.csv", data, "text/csv")}, headers=headers
    )


def sample(c) -> str:
    r = c.get("/api/supplier/import/sample.csv")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    return r.text


def test_import_is_admin_only(clients, db):
    a, _ = clients
    text = sample(a)  # the sample itself is public demo data
    assert upload(a, text, headers={}).status_code == 401
    start(a, "7.1.1.1")  # a demo cookie
    r = upload(a, text, headers={})
    assert r.status_code == 404  # demo visitors can't import (F7)
    assert db.scalar(select(func.count()).where(Buyer.is_demo.is_(False))) == 0


def test_the_sample_file_imports_three_buyers_on_paypal(clients, db, fake):
    a, _ = clients
    r = upload(a, sample(a))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["errors"] == [] and body["created"]["buyers"] == 3
    assert body["created"]["invoices"] == 7 and body["mirrored"] == 7
    assert body["mirror_errors"] == [] and body["setting_up"] == 0
    assert [b["name"] for b in body["buyers"]] == [
        "Harbor Gems",
        "Lakeside Fine Jewelry",
        "Northside Jewelers",
    ]
    for b in body["buyers"]:
        assert b["paypal_setup"] == "READY"
        token = b["statement_url"].rsplit("/s/", 1)[1]
        s = a.get(f"/api/statement/{token}").json()
        assert s["buyer"]["name"] == b["name"] and s["paypal_setup"] == "READY"
    assert fake.ops().count("create") == 7


def test_the_same_file_again_changes_nothing(clients, db, fake):
    a, _ = clients
    text = sample(a)
    upload(a, text)
    creates = fake.ops().count("create")
    body = upload(a, text).json()
    assert sum(body["created"].values()) == 0 and sum(body["updated"].values()) == 0
    assert body["mirrored"] == 0 and fake.ops().count("create") == creates


def test_a_bad_row_writes_nothing_and_says_which_row(clients, db, fake):
    a, _ = clients
    lines = sample(a).splitlines()
    lines[3] = lines[3].replace(lines[3].split(",")[4], "not-a-date", 1)  # row 4 = a MEMO row
    body = upload(a, "\n".join(lines) + "\n").json()
    assert body["errors"] and body["errors"][0]["row"] == 4
    assert body["buyers"] == [] and body["mirrored"] == 0
    assert db.scalar(select(func.count()).select_from(Buyer)) == 0 and fake.calls == []


def test_a_file_over_one_mb_is_refused(clients, db):
    a, _ = clients
    r = upload(a, b"x" * (imports.MAX_BYTES + 1))
    assert r.status_code == 413 and r.json()["error"]["code"] == "FILE_TOO_LARGE"


def test_slow_paypal_returns_in_time_and_finishes_later(clients, db, fake, monkeypatch):
    a, _ = clients
    monkeypatch.setattr(imports, "MIRROR_WAIT_S", 0.2)
    fake.delay["send"] = 0.6
    body = upload(a, sample(a)).json()
    assert body["setting_up"] > 0 and {b["paypal_setup"] for b in body["buyers"]} == {"SETTING_UP"}
    fake.delay.clear()
    for b in body["buyers"]:  # opening each statement finishes or joins the setup (M9)
        token = b["statement_url"].rsplit("/s/", 1)[1]
        assert a.post(f"/api/statement/{token}/setup").json() == {"paypal_setup": "READY"}
    db.expire_all()
    assert db.scalar(select(func.count()).where(Invoice.ledger_status == "DRAFT")) == 0
    assert len(fake.invoices) == 7  # no duplicates


def test_the_sample_dates_move_with_today(clients, db):
    a, _ = clients
    assert sample(a) == imports.sample_csv(today_ny())
    assert str(today_ny().year) in sample(a)
