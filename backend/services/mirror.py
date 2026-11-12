"""Mirror our invoices to real PayPal sandbox invoices (FR-2, FR-15; P1 D4–D6, D22, G21).

load (threadpool) → PayPal calls for all invoices concurrently (async, Semaphore(5)) →
write every result in ONE transaction (threadpool). No DB session is held across PayPal
calls and the event loop never blocks on the DB (D13).

Per invoice (2 PayPal calls when all goes well — open check R6):
- no PayPal id yet: first attempt → create (no search, D4). Retry (`mirror_attempts > 0`)
  → search by our unique number first and adopt. Create says duplicate number (open
  check 11: `details[].issue`) → search and adopt. PayPal ignores a repeated create
  Request-Id (open check 10), so search & adopt is the resume path.
- PayPal status DRAFT → send (D5); the send response is the payer link. A sent invoice is
  UNPAID in PayPal (open check 8 fixture), stored as `paypal_status`.
- adopted invoice already sent → one GET for the payer link + status.
The id is stored as soon as create succeeds, even if send then fails (D5).
"""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from backend.core.config import Settings
from backend.core.dates import today_ny
from backend.core.money import to_cents
from backend.db.models import Buyer, Invoice, InvoiceLine, Memo, MemoLine
from backend.paypal import invoices as pinv
from backend.paypal.client import PayPal, PayPalError
from backend.services import audit

CONCURRENCY = 5


@dataclass
class _Job:
    invoice_id: int
    erp_invoice_no: str
    attempts: int
    paypal_invoice_id: str | None
    paypal_status: str | None
    draft: pinv.InvoiceDraft
    total_cents: int
    paid_cents: int


@dataclass
class _Outcome:
    paypal_invoice_id: str | None = None
    status: str | None = None
    due_cents: int | None = None
    due_date: str | None = None
    invoice_date: str | None = None
    payer_url: str | None = None
    error: PayPalError | None = None


@dataclass
class MirrorResult:
    mirrored: list[str] = field(default_factory=list)  # erp invoice numbers now sent
    errors: list[tuple[str, PayPalError]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def transient(self) -> bool:
        """Any failure worth retrying later (→ 503); otherwise deterministic (→ 502), D16."""
        return any(e.transient for _, e in self.errors)


def _load(session_factory: Callable[[], Session], buyer_id: int) -> list[_Job]:
    with session_factory() as s:
        buyer = s.get(Buyer, buyer_id)
        if buyer is None:
            return []
        invoices = s.scalars(
            select(Invoice)
            .where(Invoice.buyer_id == buyer_id)
            .where((Invoice.paypal_invoice_id.is_(None)) | (Invoice.payer_view_url.is_(None)))
            .order_by(Invoice.due_on)
        ).all()
        ids = [inv.id for inv in invoices]
        memo_no_of = dict(
            s.execute(
                select(Memo.id, Memo.memo_no).where(Memo.id.in_({i.memo_id for i in invoices}))
            ).all()
        )
        lines_of: dict[int, list[Any]] = {}
        for row in s.execute(
            select(
                InvoiceLine.invoice_id, MemoLine.sku, MemoLine.description, InvoiceLine.amount_cents
            )
            .join(MemoLine, InvoiceLine.memo_line_id == MemoLine.id)
            .where(InvoiceLine.invoice_id.in_(ids))
            .order_by(MemoLine.sku)
        ):
            lines_of.setdefault(row.invoice_id, []).append(row)
        jobs = []
        for inv in invoices:
            memo_no = memo_no_of.get(inv.memo_id)
            rows = lines_of.get(inv.id, [])
            draft = pinv.InvoiceDraft(
                uid=str(inv.uid),
                paypal_invoice_number=inv.paypal_invoice_number,
                erp_invoice_no=inv.erp_invoice_no,
                memo_no=memo_no or "",
                issued_on=inv.issued_on,
                due_on=inv.due_on,
                lines=tuple(pinv.DraftLine(r.sku, r.description, r.amount_cents) for r in rows),
                buyer_name=buyer.name,
                buyer_email=buyer.email,
                statement_token=buyer.statement_token,
            )
            jobs.append(
                _Job(
                    inv.id,
                    inv.erp_invoice_no,
                    inv.mirror_attempts,
                    inv.paypal_invoice_id,
                    inv.paypal_status,
                    draft,
                    inv.total_cents,
                    inv.paid_cents,
                )
            )
        return jobs


def _read(body: dict[str, Any], out: _Outcome) -> None:
    out.paypal_invoice_id = body.get("id") or out.paypal_invoice_id
    out.status = body.get("status") or out.status
    due = (body.get("due_amount") or {}).get("value")
    if due is not None:
        out.due_cents = to_cents(due)
    detail = body.get("detail") or {}
    out.invoice_date = detail.get("invoice_date") or out.invoice_date
    out.due_date = (detail.get("payment_term") or {}).get("due_date") or out.due_date
    out.payer_url = (detail.get("metadata") or {}).get("recipient_view_url") or out.payer_url


async def _mirror_one(pp: PayPal, job: _Job, sem: asyncio.Semaphore, today: date) -> _Outcome:
    out = _Outcome(paypal_invoice_id=job.paypal_invoice_id, status=job.paypal_status)
    async with sem:
        try:
            if out.paypal_invoice_id is None:
                found = None
                if job.attempts > 0:  # D4: search only when resuming
                    found = await pinv.search_by_number(pp, job.draft.paypal_invoice_number)
                if found is None:
                    try:
                        _read(await pinv.create(pp, job.draft, today=today), out)
                    except PayPalError as e:
                        if not pinv.is_duplicate_number(e):
                            raise
                        found = await pinv.search_by_number(pp, job.draft.paypal_invoice_number)
                        if found is None:
                            raise
                if found is not None:
                    _read(found, out)
            elif out.status in (None, "DRAFT"):
                _read(await pinv.get(pp, out.paypal_invoice_id), out)

            assert out.paypal_invoice_id
            if out.status == "DRAFT":
                sent = await pinv.send(pp, out.paypal_invoice_id, job.draft.uid)
                out.payer_url = pinv.payer_url_from_send(sent) or out.payer_url
                out.status = "UNPAID"
            if out.payer_url is None:  # adopted an invoice that was already sent
                _read(await pinv.get(pp, out.paypal_invoice_id), out)
        except PayPalError as e:
            out.error = e
    return out


def _mismatch(job: _Job, out: _Outcome, settings: Settings, today: date) -> str | None:
    """Our numbers vs PayPal's (ledger rule): due amount and the dates PayPal should have."""
    problems = []
    ours_due = job.total_cents - job.paid_cents
    if out.due_cents is not None and out.due_cents != ours_due:
        problems.append(f"PayPal due {out.due_cents} ≠ ours {ours_due} (cents)")
    exp_issued, exp_due = pinv.expected_dates(job.draft, settings, today)
    if out.due_date and out.due_date != exp_due.isoformat():
        problems.append(f"PayPal due date {out.due_date} ≠ {exp_due.isoformat()}")
    if out.invoice_date and out.invoice_date != exp_issued.isoformat():
        problems.append(f"PayPal invoice date {out.invoice_date} ≠ {exp_issued.isoformat()}")
    return "; ".join(problems) or None


def _write(
    session_factory: Callable[[], Session],
    jobs: list[_Job],
    outcomes: list[_Outcome],
    settings: Settings,
    today: date,
) -> MirrorResult:
    result = MirrorResult()
    now = datetime.now(UTC)
    with session_factory() as s, s.begin():
        for job, out in zip(jobs, outcomes, strict=True):
            inv = s.get(Invoice, job.invoice_id)
            assert inv is not None
            if out.paypal_invoice_id:
                inv.paypal_invoice_id = out.paypal_invoice_id  # D5: kept even if send failed
            if out.status:
                inv.paypal_status = out.status
            if out.due_cents is not None:
                inv.paypal_due_cents = out.due_cents
            if out.payer_url:
                inv.payer_view_url = out.payer_url
            inv.paypal_synced_at = now
            if out.error is None:
                inv.mirror_error = None
                inv.mismatch = _mismatch(job, out, settings, today)
                if inv.ledger_status == "DRAFT":
                    inv.ledger_status = "OPEN"
                result.mirrored.append(job.erp_invoice_no)
                audit.log(
                    s,
                    "system",
                    "invoice.mirrored",
                    ref=job.erp_invoice_no,
                    detail={
                        "invoice_id": job.invoice_id,
                        "paypal_invoice_id": out.paypal_invoice_id,
                    },
                )
            else:
                e = out.error
                inv.mirror_error = f"{e.name}: {e.message}"[:500] + (
                    f" (debug_id {e.debug_id})" if e.debug_id else ""
                )
                inv.mirror_attempts = job.attempts + 1
                result.errors.append((job.erp_invoice_no, e))
    return result


async def mirror_invoices(
    buyer_id: int,
    pp: PayPal,
    settings: Settings,
    session_factory: Callable[[], Session],
    *,
    today: date | None = None,
) -> MirrorResult:
    """Create + send every not-yet-mirrored invoice of one buyer. Safe to call again."""
    missing = [n for n, v in (("APP_BASE_URL", settings.app_base_url),) if not v]
    if missing:
        raise pinv.NotConfiguredError(missing)
    day = today or today_ny()
    jobs = await run_in_threadpool(_load, session_factory, buyer_id)
    if not jobs:
        return MirrorResult()
    sem = asyncio.Semaphore(CONCURRENCY)
    outcomes = await asyncio.gather(*(_mirror_one(pp, j, sem, day) for j in jobs))
    return await run_in_threadpool(_write, session_factory, jobs, list(outcomes), settings, day)


# ---------------------------------------------------------------------------
# Per-buyer PayPal setup (P3 §M M9): Harbor/Lakeside are mirrored after the demo start
# ---------------------------------------------------------------------------

SETUP_WAIT_S = 10.0  # how long `/setup` waits for the running setup before answering
SETUP_DEADLINE_S = 30.0  # one setup run never hangs longer than this
_setups: dict[int, "asyncio.Task[None]"] = {}
log = logging.getLogger("mirror")


def setup_state(rows: list[tuple[str, str | None]]) -> str:
    """READY (no DRAFT invoice) · FAILED (a DRAFT with a mirror error) · SETTING_UP — from
    (ledger_status, mirror_error) of the buyer's invoices; no migration (M9)."""
    drafts = [err for status, err in rows if status == "DRAFT"]
    if not drafts:
        return "READY"
    return "FAILED" if any(drafts) else "SETTING_UP"


def buyer_setup_state(session: Session, buyer_id: int) -> str:
    rows = session.execute(
        select(Invoice.ledger_status, Invoice.mirror_error).where(
            Invoice.buyer_id == buyer_id, Invoice.ledger_status != "CANCELLED"
        )
    ).all()
    return setup_state([(st, err) for st, err in rows])


def _state(session_factory: Callable[[], Session], buyer_id: int) -> str:
    with session_factory() as s:
        return buyer_setup_state(s, buyer_id)


async def _setup(buyer_id: int, pp: PayPal, settings: Settings,
                 session_factory: Callable[[], Session]) -> None:  # fmt: skip
    try:
        res = await asyncio.wait_for(
            mirror_invoices(buyer_id, pp, settings, session_factory), timeout=SETUP_DEADLINE_S
        )
        if not res.ok:
            _, err = res.errors[0]
            log.warning("setup buyer=%s failed: %s debug_id=%s", buyer_id, err.name, err.debug_id)
    except Exception as e:  # logged; the page offers Retry (M9)
        log.warning("setup buyer=%s failed: %s", buyer_id, type(e).__name__)


def start_setup(
    buyer_id: int, pp: PayPal, settings: Settings, session_factory: Callable[[], Session]
) -> "asyncio.Task[None]":
    """The running setup for this buyer, or a new one: one at a time per buyer, later callers
    join it (M9). Not the money lock — CLAUDE.md §6 keeps PayPal calls out of it; PayPal's
    unique invoice number is the cross-process backstop (duplicate → search and adopt)."""
    loop = asyncio.get_running_loop()
    task = _setups.get(buyer_id)
    if task is not None and not task.done() and task.get_loop() is loop:
        return task
    task = loop.create_task(_setup(buyer_id, pp, settings, session_factory))
    _setups[buyer_id] = task
    task.add_done_callback(lambda t: _setups.pop(buyer_id, None) if _setups.get(buyer_id) is t
                           else None)  # fmt: skip
    return task


async def ensure_setup(
    buyer_id: int,
    pp: PayPal,
    settings: Settings,
    session_factory: Callable[[], Session],
    wait_s: float = SETUP_WAIT_S,
) -> str:
    """READY at once with no PayPal call; otherwise run or join the setup, wait ≤ wait_s, and
    return the state then (M9)."""
    if await run_in_threadpool(_state, session_factory, buyer_id) == "READY":
        return "READY"
    task = start_setup(buyer_id, pp, settings, session_factory)
    await asyncio.wait({task}, timeout=wait_s)
    return await run_in_threadpool(_state, session_factory, buyer_id)


async def setup_buyers(
    buyer_ids: list[int], pp: PayPal, settings: Settings, session_factory: Callable[[], Session]
) -> None:
    """BackgroundTask after the demo start: Harbor and Lakeside in parallel (M2)."""
    tasks = [start_setup(b, pp, settings, session_factory) for b in buyer_ids]
    await asyncio.gather(*tasks)
