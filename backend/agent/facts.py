"""Facts: a plain, immutable snapshot of ONE buyer's records (P4 D2, R1, W1, Y3).

Records only — never settings, secrets, `demo_login`, tokens or emails. Two builders give the
same Facts for the same seed (tested): `facts_from_seed` (the seed CSV, no database — the eval)
and `facts_for_buyer` (the database — the live agent). Every query filters by buyer_id
(invariant 7).
"""

from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.core.dates import age_days, days_overdue, ny_date, offset
from backend.core.money import to_cents
from backend.core.split import OpenInvoice, split_payment
from backend.db import models as m
from backend.services import importer
from backend.services.demo import BUYER_NAME, build_csv


@dataclass(frozen=True)
class Line:
    sku: str
    description: str
    price_cents: int
    status: str  # OUT | RETURNED | KEPT
    memo_no: str
    returned_on: date | None
    return_ref: str | None
    due_back_on: date | None  # the memo's due-back date (OUT lines)
    invoice_no: str | None  # KEPT lines


@dataclass(frozen=True)
class Memo:
    memo_no: str
    sent_on: date
    due_back_on: date | None


@dataclass(frozen=True)
class Invoice:
    invoice_no: str
    memo_no: str
    total_cents: int
    paid_cents: int
    issued_on: date
    due_on: date

    @property
    def due_cents(self) -> int:
        return self.total_cents - self.paid_cents


@dataclass(frozen=True)
class Payment:
    id: int
    amount_cents: int
    received_on: date
    applied: tuple[tuple[str, int], ...]  # (invoice_no, cents)


@dataclass(frozen=True)
class Proposal:
    id: str  # record id `proposal:<id>`; `seed` for the seeded S-120 request (Z8)
    kind: str  # ADJUSTMENT | RETURN_CHECK | REPLY | PROMISE_NOTE
    invoice_no: str | None
    sku: str | None
    amount_cents: int | None
    status: str  # PENDING | APPROVED | ...


@dataclass(frozen=True)
class Facts:
    buyer_name: str
    today: date
    memos: tuple[Memo, ...]
    lines: tuple[Line, ...]
    invoices: tuple[Invoice, ...]
    payments: tuple[Payment, ...] = ()
    proposals: tuple[Proposal, ...] = ()
    credit_cents: int = 0

    def line(self, sku: str | None) -> Line | None:
        if not sku:
            return None
        return next((ln for ln in self.lines if ln.sku.upper() == sku.strip().upper()), None)

    def invoice(self, no: str | None) -> Invoice | None:
        if not no:
            return None
        n = no.strip().upper()
        if not n.startswith("INV-"):
            n = f"INV-{n}"
        return next((i for i in self.invoices if i.invoice_no == n), None)

    @property
    def owed_cents(self) -> int:
        return sum(i.due_cents for i in self.invoices if i.due_cents > 0)

    def days_overdue(self, inv: Invoice) -> int:
        return days_overdue(inv.due_on, self.today) if inv.due_cents > 0 else 0

    def oldest_age_days(self) -> int:
        open_ = [i for i in self.invoices if i.due_cents > 0]
        return max((age_days(i.issued_on, self.today) for i in open_), default=0)


SEED_PROPOSAL = Proposal("seed", "ADJUSTMENT", "INV-1051", "S-120", -2000, "PENDING")


def facts_from_seed(today: date, paid_cents: int = 0) -> Facts:
    """The §Seed dataset as Facts, no database. `paid_cents=500000` applies the real
    `split_payment` as payment 1 (G2). Includes the seeded S-120 pending request (W1)."""
    result = importer.ImportResult()
    rows = importer._parse(build_csv(today, "buyer@example.com"), result)
    plan = importer._validate(rows, result)
    assert not result.errors, result.errors

    memos = tuple(
        Memo(
            r.v["memo_no"],
            date.fromisoformat(r.v["sent_on"]),
            date.fromisoformat(r.v["due_back_on"]) if r.v["due_back_on"] else None,
        )
        for r in plan.memos.values()
    )
    due_back = {m.memo_no: m.due_back_on for m in memos}
    lines = []
    for key, r in plan.lines.items():
        memo_no, sku = key
        ret = plan.returns.get(key)
        status = importer.line_status(key, plan)
        lines.append(
            Line(
                sku=sku,
                description=r.v["description"],
                price_cents=to_cents(r.v["price"]),
                status=status,
                memo_no=memo_no,
                returned_on=date.fromisoformat(ret.v["returned_on"]) if ret else None,
                return_ref=ret.v["return_ref"] if ret else None,
                due_back_on=due_back[memo_no] if status == "OUT" else None,
                invoice_no=plan.line_invoice.get(key),
            )
        )
    invoices = [
        Invoice(
            invoice_no=no,
            memo_no=r.v["memo_no"],
            total_cents=importer._invoice_values(plan, no)["total_cents"],
            paid_cents=0,
            issued_on=date.fromisoformat(r.v["issued_on"]),
            due_on=date.fromisoformat(r.v["due_on"]),
        )
        for no, r in plan.invoices.items()
    ]
    payments: tuple[Payment, ...] = ()
    if paid_cents:
        open_ = [
            OpenInvoice(i, inv.due_on, inv.issued_on, inv.due_cents)
            for i, inv in enumerate(invoices)
        ]
        split = split_payment(paid_cents, open_, [o.id for o in open_])
        for idx, cents in split:
            inv = invoices[idx]
            invoices[idx] = Invoice(
                inv.invoice_no, inv.memo_no, inv.total_cents, inv.paid_cents + cents,
                inv.issued_on, inv.due_on,
            )  # fmt: skip
        payments = (
            Payment(
                1,
                paid_cents,
                offset(today, 0),
                tuple((invoices[i].invoice_no, c) for i, c in split),
            ),
        )
    return Facts(
        buyer_name=BUYER_NAME,
        today=today,
        memos=tuple(sorted(memos, key=lambda x: (x.sent_on, x.memo_no))),
        lines=tuple(sorted(lines, key=lambda ln: ln.sku)),
        invoices=tuple(sorted(invoices, key=lambda i: (i.due_on, i.invoice_no))),
        payments=payments,
        proposals=(SEED_PROPOSAL,),
    )


LISTED = {"CAPTURED", "CAPTURE_PENDING", "RECORDED"}  # the same set the statement lists


def _proposal_sku(p: m.Proposal) -> str | None:
    """The SKU a proposal is about: `payload.sku` (agent) or its `memo_line:` record (seed)."""
    if p.payload.get("sku"):
        return str(p.payload["sku"])
    for ref in p.evidence.get("records", []):
        if isinstance(ref, str) and ref.startswith("memo_line:"):
            return ref.split(":", 1)[1]
    return None


def facts_for_buyer(session: Session, buyer_id: int, today: date) -> Facts:
    """One buyer's records from the database. The buyer id comes from the statement token,
    never from the model (invariant 7)."""
    buyer = session.get(m.Buyer, buyer_id)
    if buyer is None:
        raise LookupError("buyer not found")
    memo_rows = list(session.scalars(select(m.Memo).where(m.Memo.buyer_id == buyer_id)))
    memo_by_id = {r.id: r for r in memo_rows}
    invoice_rows = list(
        session.scalars(
            select(m.Invoice).where(
                m.Invoice.buyer_id == buyer_id, m.Invoice.ledger_status != "CANCELLED"
            )
        )
    )
    inv_no = {i.id: i.erp_invoice_no for i in invoice_rows}
    line_invoice = dict(
        session.execute(
            select(m.InvoiceLine.memo_line_id, m.InvoiceLine.invoice_id)
            .join(m.Invoice, m.InvoiceLine.invoice_id == m.Invoice.id)
            .where(m.Invoice.buyer_id == buyer_id, m.InvoiceLine.memo_line_id.is_not(None))
        ).all()
    )
    line_rows = session.scalars(
        select(m.MemoLine)
        .join(m.Memo, m.MemoLine.memo_id == m.Memo.id)
        .where(m.Memo.buyer_id == buyer_id)
    )
    lines = []
    for ln in line_rows:
        memo = memo_by_id[ln.memo_id]
        kept = ln.status == "KEPT"
        lines.append(
            Line(
                sku=ln.sku,
                description=ln.description,
                price_cents=ln.price_cents,
                status=ln.status,
                memo_no=memo.memo_no,
                returned_on=ln.returned_on,
                return_ref=ln.return_ref,
                due_back_on=memo.due_back_on if ln.status == "OUT" else None,
                invoice_no=inv_no.get(line_invoice.get(ln.id, -1)) if kept else None,
            )
        )
    pay_rows = list(
        session.scalars(
            select(m.Payment)
            .where(m.Payment.buyer_id == buyer_id, m.Payment.status.in_(LISTED))
            .order_by(m.Payment.id)
        )
    )
    allocs: dict[int, list[tuple[str, int]]] = {}
    for a in session.scalars(
        select(m.Allocation)
        .join(m.Payment, m.Allocation.payment_id == m.Payment.id)
        .where(m.Payment.buyer_id == buyer_id)
        .order_by(m.Allocation.id)
    ):
        allocs.setdefault(a.payment_id, []).append((inv_no.get(a.invoice_id, ""), a.amount_cents))
    payments = tuple(
        Payment(
            p.id,
            p.amount_cents,
            ny_date(p.received_at or p.created_at),
            tuple(allocs.get(p.id, [])),
        )
        for p in pay_rows
    )
    proposals = tuple(
        Proposal(
            id="seed" if p.evidence.get("source") == "seed" else str(p.id),
            kind=p.kind,
            invoice_no=inv_no.get(p.payload.get("invoice_id", -1)),
            sku=_proposal_sku(p),
            amount_cents=p.payload.get("amount_cents"),
            status=p.status,
        )
        for p in session.scalars(
            select(m.Proposal).where(m.Proposal.buyer_id == buyer_id).order_by(m.Proposal.id)
        )
    )
    memos = (Memo(r.memo_no, r.sent_on, r.due_back_on) for r in memo_rows)
    invoices = (
        Invoice(
            i.erp_invoice_no,
            memo_by_id[i.memo_id].memo_no,
            i.total_cents,
            i.paid_cents,
            i.issued_on,
            i.due_on,
        )
        for i in invoice_rows
    )
    return Facts(
        buyer_name=buyer.name,
        today=today,
        memos=tuple(sorted(memos, key=lambda x: (x.sent_on, x.memo_no))),
        lines=tuple(sorted(lines, key=lambda ln: ln.sku)),
        invoices=tuple(sorted(invoices, key=lambda i: (i.due_on, i.invoice_no))),
        payments=payments,
        proposals=proposals,
        credit_cents=sum(p.unapplied_cents for p in pay_rows),
    )
