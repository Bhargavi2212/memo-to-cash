"""Facts: a plain, immutable snapshot of ONE buyer's records (P4 D2, R1, W1, Y3).

Records only — never settings, secrets, `demo_login` or emails. Built from the seed CSV with
no database (`facts_from_seed`, used by the eval) — step 1 adds `facts_for_buyer` (DB) and a
test that both give the same Facts for the same seed.
"""

from dataclasses import dataclass
from datetime import date

from backend.core.dates import age_days, days_overdue, offset
from backend.core.money import to_cents
from backend.core.split import OpenInvoice, split_payment
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


SEED_PROPOSAL = Proposal("ADJUSTMENT", "INV-1051", "S-120", -2000, "PENDING")


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
        memos=memos,
        lines=tuple(sorted(lines, key=lambda ln: ln.sku)),
        invoices=tuple(sorted(invoices, key=lambda i: (i.due_on, i.invoice_no))),
        payments=payments,
        proposals=(SEED_PROPOSAL,),
    )
