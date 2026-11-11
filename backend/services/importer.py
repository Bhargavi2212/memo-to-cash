"""CSV importer (FR-1, BACKEND_SCHEMA §6, P1 D2/D20).

`import_csv` validates every row first; if anything is wrong it writes nothing and returns
all errors as `{row, msg}` (row 1 = header). Otherwise it upserts, in this order, buyers →
memos → lines → returns → invoices → invoice lines, inside the caller's transaction (the
caller commits). Re-importing the same file changes nothing.

Rules: a line on an INVOICE becomes KEPT, a RETURN makes it RETURNED, otherwise OUT.
An invoice that already has a PayPal id is immutable (lines, dates, total).
Every memo/invoice a row refers to must be declared in the same file (an ERP export is a
full snapshot of what it covers).
"""

import csv
import io
import re
import secrets
import string
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from backend.core.money import to_cents
from backend.db.models import Buyer, Invoice, InvoiceLine, Memo, MemoLine
from backend.services import audit

COLUMNS = (
    "record_type",
    "buyer_name",
    "buyer_email",
    "memo_no",
    "sent_on",
    "due_back_on",
    "sku",
    "description",
    "price",
    "returned_on",
    "return_ref",
    "invoice_no",
    "issued_on",
    "due_on",
)
REQUIRED = {
    "BUYER": ("buyer_name", "buyer_email"),
    "MEMO": ("memo_no", "buyer_email", "sent_on"),
    "LINE": ("memo_no", "sku", "description", "price"),
    "RETURN": ("memo_no", "sku", "returned_on", "return_ref"),
    "INVOICE": ("invoice_no", "memo_no", "issued_on", "due_on"),
    "INVOICE_LINE": ("invoice_no", "memo_no", "sku"),
}
DATE_FIELDS = ("sent_on", "due_back_on", "returned_on", "issued_on", "due_on")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SUFFIX_CHARS = string.ascii_lowercase + string.digits
ENTITIES = ("buyers", "memos", "lines", "invoices")


@dataclass
class ImportResult:
    created: dict[str, int] = field(default_factory=lambda: dict.fromkeys(ENTITIES, 0))
    updated: dict[str, int] = field(default_factory=lambda: dict.fromkeys(ENTITIES, 0))
    errors: list[dict[str, Any]] = field(default_factory=list)
    buyer_ids: list[int] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


@dataclass
class _Row:
    n: int
    kind: str
    v: dict[str, str]


def paypal_invoice_number(erp_invoice_no: str) -> str:
    """Sandbox rule (BACKEND_SCHEMA §8): `<erp no>-<6 random [a-z0-9]>`."""
    return f"{erp_invoice_no}-" + "".join(secrets.choice(_SUFFIX_CHARS) for _ in range(6))


def import_csv(
    session: Session,
    csv_text: str,
    *,
    demo_session: str | None = None,
    actor: str = "system",
) -> ImportResult:
    result = ImportResult()
    rows = _parse(csv_text, result)
    if result.errors:
        return result
    plan = _validate(rows, result)
    if result.errors:
        return result
    _apply(session, plan, demo_session, result)
    if result.errors:
        return result
    audit.log(
        session,
        actor,
        "import.completed",
        detail={
            "created": result.created,
            "updated": result.updated,
            "demo": demo_session is not None,
        },
    )
    session.flush()
    return result


# ---------- parse + per-row checks ----------


def _err(result: ImportResult, row: int, msg: str) -> None:
    result.errors.append({"row": row, "msg": msg})


def _parse(csv_text: str, result: ImportResult) -> list[_Row]:
    reader = csv.reader(io.StringIO(csv_text.lstrip("﻿")))
    header = next(reader, None)
    if header is None:
        _err(result, 1, "empty file")
        return []
    header = [h.strip() for h in header]
    missing = [c for c in COLUMNS if c not in header]
    if missing:
        _err(result, 1, f"header is missing columns: {', '.join(missing)}")
        return []
    rows: list[_Row] = []
    for n, cells in enumerate(reader, start=2):
        if not any(c.strip() for c in cells):
            continue
        v = {h: (cells[i].strip() if i < len(cells) else "") for i, h in enumerate(header)}
        kind = v.get("record_type", "").upper()
        if kind not in REQUIRED:
            _err(result, n, f"unknown record_type {v.get('record_type', '')!r}")
            continue
        bad = [c for c in REQUIRED[kind] if not v.get(c)]
        if bad:
            _err(result, n, f"{kind}: missing {', '.join(bad)}")
            continue
        for c in DATE_FIELDS:
            if v.get(c) and not _is_date(v[c]):
                _err(result, n, f"{c} must be a date YYYY-MM-DD, got {v[c]!r}")
        if kind == "LINE":
            try:
                if to_cents(v["price"]) <= 0:
                    _err(result, n, "price must be more than 0")
            except ValueError:
                _err(result, n, f"price must look like 1150 or 1150.50, got {v['price']!r}")
        rows.append(_Row(n, kind, v))
    return rows


def _is_date(s: str) -> bool:
    if not _ISO_DATE.match(s):
        return False
    try:
        date.fromisoformat(s)
    except ValueError:
        return False
    return True


# ---------- cross-row checks ----------


@dataclass
class _Plan:
    buyers: dict[str, _Row] = field(default_factory=dict)  # email -> row
    memos: dict[str, _Row] = field(default_factory=dict)  # memo_no -> row
    lines: dict[tuple[str, str], _Row] = field(default_factory=dict)  # (memo_no, sku)
    returns: dict[tuple[str, str], _Row] = field(default_factory=dict)
    invoices: dict[str, _Row] = field(default_factory=dict)  # invoice_no -> row
    inv_lines: dict[str, list[_Row]] = field(default_factory=dict)  # invoice_no -> rows
    line_invoice: dict[tuple[str, str], str] = field(default_factory=dict)  # (memo, sku) -> inv


def _validate(rows: list[_Row], result: ImportResult) -> _Plan:
    p = _Plan()
    seen_inv_line: set[tuple[str, str]] = set()
    for r in rows:
        v = r.v
        if r.kind == "BUYER":
            key = v["buyer_email"].lower()
            if key in p.buyers:
                _err(result, r.n, f"duplicate BUYER {v['buyer_email']}")
            p.buyers[key] = r
        elif r.kind == "MEMO":
            if v["memo_no"] in p.memos:
                _err(result, r.n, f"duplicate MEMO {v['memo_no']}")
            p.memos[v["memo_no"]] = r
        elif r.kind == "LINE":
            key = (v["memo_no"], v["sku"])
            if key in p.lines:
                _err(result, r.n, f"duplicate LINE {v['memo_no']} {v['sku']}")
            p.lines[key] = r
        elif r.kind == "RETURN":
            key = (v["memo_no"], v["sku"])
            if key in p.returns:
                _err(result, r.n, f"duplicate RETURN {v['memo_no']} {v['sku']}")
            p.returns[key] = r
        elif r.kind == "INVOICE":
            if v["invoice_no"] in p.invoices:
                _err(result, r.n, f"duplicate INVOICE {v['invoice_no']}")
            p.invoices[v["invoice_no"]] = r
        elif r.kind == "INVOICE_LINE":
            key = (v["invoice_no"], v["sku"])
            if key in seen_inv_line:
                _err(result, r.n, f"duplicate INVOICE_LINE {v['invoice_no']} {v['sku']}")
                continue
            seen_inv_line.add(key)
            p.inv_lines.setdefault(v["invoice_no"], []).append(r)

    for r in p.memos.values():
        if r.v["buyer_email"].lower() not in p.buyers:
            _err(result, r.n, f"MEMO {r.v['memo_no']}: no BUYER row for {r.v['buyer_email']}")
    for (memo_no, _), r in [*p.lines.items(), *p.returns.items()]:
        if memo_no not in p.memos:
            _err(result, r.n, f"{r.kind}: unknown memo {memo_no}")
    for key, r in p.returns.items():
        if key not in p.lines and key[0] in p.memos:
            _err(result, r.n, f"RETURN {key[0]} {key[1]}: no LINE for this SKU")
    for inv_no, r in p.invoices.items():
        v = r.v
        if v["memo_no"] not in p.memos:
            _err(result, r.n, f"INVOICE {inv_no}: unknown memo {v['memo_no']}")
        if _is_date(v["issued_on"]) and _is_date(v["due_on"]) and v["issued_on"] > v["due_on"]:
            _err(result, r.n, f"INVOICE {inv_no}: issued_on is after due_on")
        if not p.inv_lines.get(inv_no):
            _err(result, r.n, f"INVOICE {inv_no}: has no INVOICE_LINE rows")
    for inv_no, lines in p.inv_lines.items():
        inv = p.invoices.get(inv_no)
        for r in lines:
            key = (r.v["memo_no"], r.v["sku"])
            if inv is None:
                _err(result, r.n, f"INVOICE_LINE: unknown invoice {inv_no}")
                continue
            if r.v["memo_no"] != inv.v["memo_no"]:
                _err(
                    result,
                    r.n,
                    f"INVOICE_LINE {inv_no}: memo {r.v['memo_no']} is not the invoice's memo",
                )
                continue
            if key not in p.lines:
                _err(result, r.n, f"INVOICE_LINE {inv_no}: SKU {key[1]} has no LINE on {key[0]}")
                continue
            if key in p.line_invoice:
                _err(result, r.n, f"INVOICE_LINE: {key[1]} is already on {p.line_invoice[key]}")
                continue
            if key in p.returns:
                _err(
                    result,
                    r.n,
                    f"INVOICE_LINE {inv_no}: {key[1]} is RETURNED, it can't be invoiced",
                )
                continue
            p.line_invoice[key] = inv_no
    return p


# ---------- write ----------


def _d(s: str) -> date | None:
    return date.fromisoformat(s) if s else None


def _set(obj: Any, values: dict[str, Any]) -> bool:
    changed = False
    for k, val in values.items():
        if getattr(obj, k) != val:
            setattr(obj, k, val)
            changed = True
    return changed


def _apply(session: Session, p: _Plan, demo_session: str | None, result: ImportResult) -> None:
    """Few round-trips (review R6): existing rows are prefetched per level in one query each,
    new rows are written with one flush per level. A demo copy (always a new buyer) does no
    lookups at all."""
    # 1. Existing real buyers (demo copies are always new, D2), locked (invariant 9).
    found: dict[str, Buyer | None] = dict.fromkeys(p.buyers)
    if demo_session is None and p.buyers:
        emails = {r.v["buyer_email"]: key for key, r in p.buyers.items()}
        for b in session.scalars(
            select(Buyer).where(Buyer.email.in_(emails), Buyer.demo_session.is_(None))
        ):
            found[emails[b.email]] = b
    known_ids = sorted(b.id for b in found.values() if b is not None)
    for buyer_id in known_ids:
        session.execute(text("SELECT pg_advisory_xact_lock(:id)"), {"id": buyer_id})

    # Prefetch what the known buyers already have (nothing to fetch for new buyers).
    memo_rows: dict[tuple[int, str], Memo] = {}
    line_rows: dict[tuple[int, str], MemoLine] = {}  # (memo id, sku)
    inv_rows: dict[tuple[int, str], Invoice] = {}
    inv_lines: dict[int, list[InvoiceLine]] = {}
    if known_ids:
        for m in session.scalars(select(Memo).where(Memo.buyer_id.in_(known_ids))):
            memo_rows[(m.buyer_id, m.memo_no)] = m
        for line in session.scalars(
            select(MemoLine)
            .join(Memo, MemoLine.memo_id == Memo.id)
            .where(Memo.buyer_id.in_(known_ids))
        ):
            line_rows[(line.memo_id, line.sku)] = line
        for i in session.scalars(select(Invoice).where(Invoice.buyer_id.in_(known_ids))):
            inv_rows[(i.buyer_id, i.erp_invoice_no)] = i
        for il in session.scalars(
            select(InvoiceLine)
            .join(Invoice, InvoiceLine.invoice_id == Invoice.id)
            .where(Invoice.buyer_id.in_(known_ids))
        ):
            inv_lines.setdefault(il.invoice_id, []).append(il)
    sku_of = {line.id: line.sku for line in line_rows.values()}

    def item_set(invoice_id: int) -> list[tuple[str, int]]:
        return sorted(
            (sku_of[il.memo_line_id], il.amount_cents)
            for il in inv_lines.get(invoice_id, [])
            if il.memo_line_id is not None
        )

    # 2. Before any write: an invoice already on PayPal must not change (dates or item lines).
    existing_inv: dict[str, Invoice] = {}
    for inv_no, r in p.invoices.items():
        b = found[p.memos[r.v["memo_no"]].v["buyer_email"].lower()]
        inv = inv_rows.get((b.id, inv_no)) if b is not None else None
        if inv is None:
            continue
        existing_inv[inv_no] = inv
        if inv.paypal_invoice_id:
            new = _invoice_values(p, inv_no)
            if (inv.issued_on, inv.due_on, item_set(inv.id)) != (
                new["issued_on"],
                new["due_on"],
                new["items"],
            ):
                _err(result, r.n, f"INVOICE {inv_no} is already on PayPal and can't be changed")
    if result.errors:
        return

    # 3. Writes, one flush per level.
    buyers: dict[str, Buyer] = {}
    for email, r in p.buyers.items():
        b = found[email]
        if b is None:
            b = Buyer(
                name=r.v["buyer_name"],
                email=r.v["buyer_email"],
                statement_token=secrets.token_urlsafe(32),
                is_demo=demo_session is not None,
                demo_session=demo_session,
            )
            session.add(b)
            result.created["buyers"] += 1
        elif _set(b, {"name": r.v["buyer_name"]}):
            result.updated["buyers"] += 1
        buyers[email] = b
    session.flush()
    result.buyer_ids = [b.id for b in buyers.values()]

    memos: dict[str, Memo] = {}
    for memo_no, r in p.memos.items():
        b = buyers[r.v["buyer_email"].lower()]
        vals = {"sent_on": _d(r.v["sent_on"]), "due_back_on": _d(r.v["due_back_on"])}
        m = memo_rows.get((b.id, memo_no))
        if m is None:
            m = Memo(buyer_id=b.id, memo_no=memo_no, **vals)
            session.add(m)
            result.created["memos"] += 1
        elif _set(m, vals):
            result.updated["memos"] += 1
        memos[memo_no] = m
    session.flush()

    lines: dict[tuple[str, str], MemoLine] = {}
    for key, r in p.lines.items():
        memo_no, sku = key
        ret = p.returns.get(key)
        status = line_status(key, p)
        vals = {
            "description": r.v["description"],
            "price_cents": to_cents(r.v["price"]),
            "status": status,
            "returned_on": _d(ret.v["returned_on"]) if ret else None,
            "return_ref": ret.v["return_ref"] if ret else None,
        }
        m = memos[memo_no]
        line = line_rows.get((m.id, sku))
        if line is None:
            line = MemoLine(memo_id=m.id, sku=sku, **vals)
            session.add(line)
            result.created["lines"] += 1
        elif _set(line, vals):
            result.updated["lines"] += 1
        lines[key] = line

    invoices: dict[str, Invoice] = {}
    replace_lines: set[str] = set()
    for inv_no, r in p.invoices.items():
        memo = memos[r.v["memo_no"]]
        new = _invoice_values(p, inv_no)
        inv = existing_inv.get(inv_no)
        if inv is None:
            inv = Invoice(
                buyer_id=memo.buyer_id,
                erp_invoice_no=inv_no,
                paypal_invoice_number=paypal_invoice_number(inv_no),
                memo_id=memo.id,
                issued_on=new["issued_on"],
                due_on=new["due_on"],
                total_cents=new["total_cents"],
            )
            session.add(inv)
            result.created["invoices"] += 1
            replace_lines.add(inv_no)
        else:
            current = inv_lines.get(inv.id, [])
            adjustments = sum(il.amount_cents for il in current if il.memo_line_id is None)
            changed = _set(
                inv,
                {
                    "issued_on": new["issued_on"],
                    "due_on": new["due_on"],
                    "total_cents": new["total_cents"] + adjustments,
                },
            )
            if item_set(inv.id) != new["items"]:
                # Not on PayPal (checked above): replace its item lines.
                for il in current:
                    if il.memo_line_id is not None:
                        session.delete(il)
                replace_lines.add(inv_no)
            if changed or inv_no in replace_lines:
                result.updated["invoices"] += 1
        invoices[inv_no] = inv
    session.flush()  # memo lines + invoices (and any deletes) together

    for inv_no in replace_lines:
        r = p.invoices[inv_no]
        for sku in _invoice_values(p, inv_no)["skus"]:
            line = lines[(r.v["memo_no"], sku)]
            session.add(
                InvoiceLine(
                    invoice_id=invoices[inv_no].id,
                    memo_line_id=line.id,
                    name=f"{sku} {line.description} (memo {r.v['memo_no']})",
                    amount_cents=line.price_cents,
                )
            )
    session.flush()


def line_status(key: tuple[str, str], p: _Plan) -> str:
    """The one rule (shared with the agent's Facts, P4 A2): on an INVOICE → KEPT, a RETURN →
    RETURNED, otherwise OUT."""
    if key in p.line_invoice:
        return "KEPT"
    return "RETURNED" if key in p.returns else "OUT"


def _invoice_values(p: _Plan, inv_no: str) -> dict[str, Any]:
    r = p.invoices[inv_no]
    skus = [x.v["sku"] for x in p.inv_lines[inv_no]]
    items = sorted((s, to_cents(p.lines[(r.v["memo_no"], s)].v["price"])) for s in skus)
    return {
        "issued_on": _d(r.v["issued_on"]),
        "due_on": _d(r.v["due_on"]),
        "total_cents": sum(c for _, c in items),  # item lines; adjustments are added by P3
        "items": items,
        "skus": skus,
    }
