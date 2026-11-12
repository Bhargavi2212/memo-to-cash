"""Supplier decisions on agent proposals (FR-10; P4 X2 / step 5b, P3 A6–A8; invariant 5).

APPROVE on an ADJUSTMENT, under `pg_advisory_xact_lock(buyer_id)`:
- the **mode** is the caller's (`APPLY` / `CREDIT`) or, with none, decided **here under the
  lock** from the invoice's current due (due ≥ amount → APPLY, else CREDIT; P3 A5/R5), so a
  payment between page load and click never surprises the supplier. Explicit APPLY with due <
  amount → 409 NEEDS_CREDIT; SPLIT → 422 NOT_YET (P3b).
- the **effective values are written into the payload before any PayPal call** (R1): `mode`,
  `credit_cents` and the (possibly edited) negative `amount_cents`, with the buyer's request
  kept in `payload.original`. Ledger line, PayPal PUT, retries and the buyer's summary all read
  the payload, so a retry can never send a different amount.
- APPLY: negative invoice line + lower `total_cents` + `ledger_status` → APPROVED → commit →
  PayPal PUT (no lock) → GET → DONE (PayPal's due stored; a difference goes to `mismatch`) or
  FAILED. Approve again on FAILED / a stuck APPROVED retries only the PayPal step (the line's
  tag keeps it to one line); on DONE it's a no-op. `_finish` merges into `result`.
- CREDIT: no invoice or PayPal change; DONE with `payload.credit_cents` (credit.py counts it).
Other kinds (P3a): REPLY → DONE (+ optional reply text); RETURN_CHECK → DONE with a reply text
(the line actions are P3b); PROMISE_NOTE → DONE. REJECT any PENDING → REJECTED (+ optional
reply). Edits and REJECT only on PENDING: a FAILED adjustment already changed our ledger, so it
can only be retried (G7).
"""

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from backend.agent.facts import proposal_sku
from backend.core.dates import today_ny
from backend.core.money import to_cents
from backend.db.models import Invoice, InvoiceLine, Memo, MemoLine, Proposal, ReminderSent
from backend.paypal import invoices as pinv
from backend.paypal.client import PayPal, PayPalError
from backend.services import audit
from backend.services.payments import _ledger_status

log = logging.getLogger(__name__)

NEEDS_CREDIT_MSG = "Invoice already paid — record it as credit instead"
MODES = ("APPLY", "CREDIT", "SPLIT")
MAX_REPLY = 1000


class ProposalError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def snapshot(p: Proposal) -> dict[str, Any]:
    return {
        "id": p.id,
        "kind": p.kind,
        "status": p.status,
        "payload": p.payload,
        "evidence": p.evidence,
        "result": p.result,
        "decided_at": p.decided_at.isoformat() if p.decided_at else None,
    }


def _load(s: Session, proposal_id: int, buyer_ids: frozenset[int] | None = None) -> Proposal:
    """`buyer_ids` = the caller's scope (P3 A1): None = admin (any id), else only those buyers;
    anything else is "not found" — no existence leak."""
    p = s.get(Proposal, proposal_id)
    if p is None or (buyer_ids is not None and p.buyer_id not in buyer_ids):
        raise ProposalError(404, "NOT_FOUND", "No such proposal")
    s.execute(text("SELECT pg_advisory_xact_lock(:id)"), {"id": p.buyer_id})
    s.refresh(p)  # re-read under the lock
    return p


def _reply_text(edits: dict[str, Any]) -> str | None:
    t = edits.get("text")
    if t is None:
        return None
    if not isinstance(t, str) or len(t.strip()) > MAX_REPLY:
        raise ProposalError(422, "BAD_EDIT", f"text must be at most {MAX_REPLY} characters")
    return t.strip() or None


def _line_price(s: Session, inv: Invoice, sku: str) -> int | None:
    return s.scalar(
        select(MemoLine.price_cents).where(MemoLine.memo_id == inv.memo_id, MemoLine.sku == sku)
    )


def _edit_adjustment(s: Session, p: Proposal, inv: Invoice, sku: str, edits: dict[str, Any]):
    """A7: `{amount_cents?, reason?}` (amount positive, as the supplier types it), re-validated
    with decide()'s rules: > 0 and ≤ the line's price. Replaces the effective payload values."""
    unknown = set(edits) - {"amount_cents", "reason"}
    if unknown:
        raise ProposalError(422, "BAD_EDIT", f"can't edit {sorted(unknown)} on a deduction")
    pl = dict(p.payload)
    pl.setdefault("original", {"amount_cents": pl["amount_cents"], "reason": pl.get("reason")})
    if "amount_cents" in edits:
        amount = edits["amount_cents"]
        if not isinstance(amount, int) or isinstance(amount, bool) or amount <= 0:
            raise ProposalError(422, "BAD_EDIT", "amount_cents must be a positive whole number")
        price = _line_price(s, inv, sku)
        if price is not None and amount > price:
            raise ProposalError(422, "BAD_EDIT", f"more than {sku}'s price")
        pl["amount_cents"] = -amount
    if "reason" in edits:
        pl["reason"] = str(edits["reason"] or "")[:200]
    p.payload = pl


def _apply(
    sf: Callable[[], Session],
    proposal_id: int,
    decision: str,
    buyer_ids: frozenset[int] | None = None,
    mode: str | None = None,
    edits: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """The ledger part. Returns (proposal snapshot, the PayPal job or None)."""
    edits = edits or {}
    with sf() as s, s.begin():
        p = _load(s, proposal_id, buyer_ids)
        now = datetime.now(UTC)
        if p.status != "PENDING" and (edits or decision == "REJECT"):
            raise ProposalError(409, "NOT_PENDING", f"Proposal is {p.status}")
        if decision == "REJECT":
            reply = _reply_text({"text": edits.get("text")})  # optional note to the buyer
            p.status, p.decided_at = "REJECTED", now
            if reply:
                p.result = {**(p.result or {}), "reply_text": reply}
            audit.log(s, "supplier", "proposal.rejected", ref=str(p.id))
            return snapshot(p), None

        if p.status == "REJECTED":
            raise ProposalError(409, "NOT_PENDING", "Proposal is REJECTED")
        if p.status == "DONE":
            return snapshot(p), None  # repeat approve: no-op
        if p.kind == "REMINDER":
            return _approve_reminder(s, p, edits, now)
        if p.kind != "ADJUSTMENT":
            return _approve_other(s, p, edits, now), None

        inv = s.get(Invoice, p.payload["invoice_id"])
        if inv is None or inv.buyer_id != p.buyer_id:
            raise ProposalError(409, "INVOICE_GONE", "The invoice for this proposal is gone")
        sku = str(p.payload.get("sku") or proposal_sku(p) or "")
        if p.status == "PENDING":
            if mode is not None and mode not in MODES:
                raise ProposalError(422, "BAD_MODE", "mode must be APPLY, CREDIT or SPLIT")
            if edits:
                _edit_adjustment(s, p, inv, sku, edits)
            amount = -int(p.payload["amount_cents"])  # stored negative (BACKEND_SCHEMA)
            due = inv.total_cents - inv.paid_cents
            if mode is None:  # decided here, under the lock (R5)
                mode = "APPLY" if due >= amount else "CREDIT"
            if mode == "APPLY" and due < amount:
                raise ProposalError(409, "NEEDS_CREDIT", NEEDS_CREDIT_MSG)
            # SPLIT (P3b G15, proved: a PUT to due $0.00 on a part-paid invoice works): take
            # what's still due off the invoice, credit the rest
            deduct = min(due, amount) if mode == "SPLIT" else amount
            if mode == "SPLIT" and deduct <= 0:
                mode, deduct = "CREDIT", amount  # nothing left to deduct: all credit
            elif mode == "SPLIT" and deduct == amount:
                mode = "APPLY"  # it fits after all
            pl = dict(p.payload)
            pl.setdefault(
                "original", {"amount_cents": pl["amount_cents"], "reason": pl.get("reason")}
            )
            pl["mode"] = mode
            pl["credit_cents"] = {"CREDIT": amount, "SPLIT": amount - deduct}.get(mode, 0)
            if mode == "SPLIT":
                pl["amount_cents"] = -deduct  # the effective deduction (R1); original kept
            p.payload = pl  # effective values, before any PayPal call (R1)
            p.decided_at = now
            if mode == "CREDIT":
                p.status = "DONE"
                p.result = {**(p.result or {}), "mode": "CREDIT", "credit_cents": amount}
                audit.log(
                    s,
                    "supplier",
                    "proposal.credited",
                    ref=str(p.id),
                    detail={"invoice": inv.erp_invoice_no, "credit": amount},
                )
                return snapshot(p), None
            # memo_line_id stays NULL: the unique indexes are for kept lines (BACKEND_SCHEMA)
            s.add(
                InvoiceLine(
                    invoice_id=inv.id,
                    memo_line_id=None,
                    name=f"Adjustment {sku}",
                    amount_cents=-deduct,
                )
            )
            inv.total_cents -= deduct
            inv.ledger_status = _ledger_status(inv)
            p.status = "APPROVED"
            audit.log(
                s,
                "supplier",
                "proposal.approved",
                ref=str(p.id),
                detail={
                    "invoice": inv.erp_invoice_no,
                    "amount_cents": -deduct,
                    "mode": mode,
                    "credit_cents": pl["credit_cents"],
                },
            )
        # APPROVED (now, or left by a crash) / FAILED → the PayPal step (again), from the payload
        job = {
            "proposal_id": p.id,
            "proposal_uid": str(p.uid),
            "invoice_id": inv.id,
            "paypal_invoice_id": inv.paypal_invoice_id,
            "sku": sku,
            "amount_cents": int(p.payload["amount_cents"]),
            "reason": str(p.payload.get("reason") or ""),
        }
        return snapshot(p), job


def _approve_reminder(
    s: Session, p: Proposal, edits: dict[str, Any], now: datetime
) -> tuple[dict[str, Any], dict[str, Any]]:
    """P3b B6: PENDING (or FAILED → retry) → APPROVED under the buyer lock, the PayPal call
    after commit. 2 per invoice per NY day (`reminders_sent`) → 409 REMIND_LIMIT. Edits:
    `{text}` (≤ 500 chars). The day's attempt number is kept in the payload so a retry reuses
    the same Request-Id."""
    from backend.services import reminders

    if p.status == "APPROVED":
        raise ProposalError(409, "IN_FLIGHT", "This reminder is being sent")
    unknown = set(edits) - {"text"}
    if unknown:
        raise ProposalError(422, "BAD_EDIT", f"can't edit {sorted(unknown)} here")
    if "text" in edits:
        text = str(edits["text"] or "").strip()
        if not text or len(text) > 500:
            raise ProposalError(422, "BAD_EDIT", "The reminder needs 1–500 characters")
        p.payload = {
            **p.payload,
            "text": text,
            "original": p.payload.get("original") or {"text": p.payload["text"]},
        }
    inv = s.get(Invoice, p.payload["invoice_id"])
    if inv is None or inv.buyer_id != p.buyer_id:
        raise ProposalError(409, "INVOICE_GONE", "The invoice for this reminder is gone")
    day = today_ny()
    sent = reminders.sent_today(s, inv.id, day)
    if sent >= reminders.PER_DAY:
        raise ProposalError(
            409,
            "REMIND_LIMIT",
            "PayPal allows 2 reminders a day for an invoice — try again tomorrow.",
        )
    attempt = dict(p.payload.get("attempt") or {})
    if attempt.get("day") != day.isoformat():
        attempt = {"day": day.isoformat(), "n": sent + 1}
    p.payload = {**p.payload, "attempt": attempt}
    p.status, p.decided_at = "APPROVED", now
    audit.log(s, "supplier", "proposal.approved", ref=str(p.id), detail={"kind": "REMINDER"})
    job = {
        "kind": "REMINDER",
        "proposal_id": p.id,
        "invoice_id": inv.id,
        "paypal_invoice_id": inv.paypal_invoice_id,
        "text": p.payload["text"],
        "request_key": f"{inv.uid}-{attempt['day'].replace('-', '')}-{attempt['n']}",
    }
    return snapshot(p), job


def _finish_reminder(
    sf: Callable[[], Session], job: dict[str, Any], err: PayPalError | None
) -> dict[str, Any]:
    with sf() as s, s.begin():
        p = _load(s, job["proposal_id"])
        old = p.result or {}
        attempts = int(old.get("attempts", 0)) + 1
        if err is not None:
            p.status = "FAILED"
            p.result = {**old, "error": err.name, "debug_id": err.debug_id, "attempts": attempts}
            log.warning("remind failed proposal=%s %s debug_id=%s", p.id, err.name, err.debug_id)
            return snapshot(p)
        now = datetime.now(UTC)
        s.add(ReminderSent(invoice_id=job["invoice_id"], sent_at=now))
        p.status = "DONE"
        p.result = {
            **old,
            "sent_at": now.isoformat(),
            "attempts": attempts,
            "error": None,
            "debug_id": None,
        }
        audit.log(
            s, "paypal", "reminder.sent", ref=str(p.id), detail={"invoice_id": job["invoice_id"]}
        )
        return snapshot(p)


PROMISE_MAX_DAYS = 90


def _promise_date(value: Any) -> str:
    """A supplier-corrected promise date: ISO, after today (NY), at most 90 days out."""
    from datetime import date, timedelta

    try:
        d = date.fromisoformat(str(value))
    except ValueError as e:
        raise ProposalError(422, "BAD_EDIT", "promise_date must be YYYY-MM-DD") from e
    today = today_ny()
    if not today < d <= today + timedelta(days=PROMISE_MAX_DAYS):
        raise ProposalError(422, "BAD_EDIT", "The promised date must be in the next 90 days")
    return d.isoformat()


def _resolve_return_check(s: Session, p: Proposal, edits: dict[str, Any]) -> str:
    """RETURN_CHECK (the buyer says they returned an item we have no record of):
    - CREDIT_LINE: we accept it — a new PENDING ADJUSTMENT for the line's price on its invoice
      (the supplier still approves the money, with the usual APPLY/CREDIT/SPLIT rules);
    - ASK_FOR_SLIP: reply asking for the return slip; nothing else changes."""
    unknown = set(edits) - {"resolution", "text"}
    if unknown:
        raise ProposalError(422, "BAD_EDIT", f"can't edit {sorted(unknown)} here")
    sku = str(p.payload.get("sku") or "")
    resolution = edits["resolution"]
    if resolution == "ASK_FOR_SLIP":
        p.result = {**(p.result or {}), "resolution": resolution}
        return _reply_text(edits) or (
            f"Please send us the return slip for {sku} so we can match it to our records."
        )
    if resolution != "CREDIT_LINE":
        raise ProposalError(422, "BAD_EDIT", "resolution must be CREDIT_LINE or ASK_FOR_SLIP")
    line = s.scalars(
        select(MemoLine)
        .join(Memo, MemoLine.memo_id == Memo.id)
        .where(Memo.buyer_id == p.buyer_id, MemoLine.sku == sku)
    ).first()
    if line is None or line.status != "KEPT":
        raise ProposalError(422, "NOT_KEPT", f"{sku} isn't on an invoice — nothing to credit")
    il = s.scalars(select(InvoiceLine).where(InvoiceLine.memo_line_id == line.id)).first()
    if il is None:
        raise ProposalError(422, "NOT_KEPT", f"{sku} isn't on an invoice — nothing to credit")
    inv = s.get(Invoice, il.invoice_id)
    assert inv is not None
    adj = Proposal(
        buyer_id=p.buyer_id,
        kind="ADJUSTMENT",
        payload={
            "invoice_id": inv.id,
            "amount_cents": -line.price_cents,
            "sku": sku,
            "reason": f"Return of {sku} accepted (from a return check)",
        },
        evidence={
            "source": "return_check",
            "return_check_id": p.id,
            "checks": [f"{sku} is KEPT on {inv.erp_invoice_no}", f"price {line.price_cents} cents"],
            "records": [f"memo_line:{sku}", f"invoice:{inv.erp_invoice_no}"],
        },
    )
    s.add(adj)
    s.flush()
    p.result = {**(p.result or {}), "resolution": resolution, "adjustment_proposal_id": adj.id}
    return _reply_text(edits) or (
        f"Thanks — we'll credit {sku}. Demo Diamond Co. will confirm the amount shortly."
    )


def _approve_other(s: Session, p: Proposal, edits: dict[str, Any], now: datetime) -> dict:
    """A8: REPLY / RETURN_CHECK / PROMISE_NOTE → DONE; reply text reaches the buyer.
    P3b step 5: PROMISE_NOTE takes `{promise_date}` (future, ≤ 90 days); RETURN_CHECK takes
    `{resolution: CREDIT_LINE | ASK_FOR_SLIP, text?}` (BACKEND_SCHEMA §8)."""
    if p.kind == "PROMISE_NOTE":
        unknown = set(edits) - {"promise_date"}
        if unknown:
            raise ProposalError(422, "BAD_EDIT", f"can't edit {sorted(unknown)} here")
        if "promise_date" in edits:
            p.payload = {
                **p.payload,
                "promise_date": _promise_date(edits["promise_date"]),
                "original": p.payload.get("original")
                or {"promise_date": p.payload.get("promise_date")},
            }
        reply = None
    elif p.kind == "RETURN_CHECK" and edits.get("resolution"):
        reply = _resolve_return_check(s, p, edits)
    elif p.kind in ("REPLY", "RETURN_CHECK"):
        unknown = set(edits) - {"text"}
        if unknown:
            raise ProposalError(422, "BAD_EDIT", f"can't edit {sorted(unknown)} here")
        reply = _reply_text(edits)
        if p.kind == "RETURN_CHECK" and not reply:
            raise ProposalError(422, "TEXT_REQUIRED", "Write the buyer a reply (P3a)")
    else:
        raise ProposalError(422, "NOT_YET", f"{p.kind} approvals come in a later step")
    p.status, p.decided_at = "DONE", now
    if reply:
        p.result = {**(p.result or {}), "reply_text": reply}
    audit.log(s, "supplier", "proposal.approved", ref=str(p.id), detail={"kind": p.kind})
    return snapshot(p)


def _finish(
    sf: Callable[[], Session],
    job: dict[str, Any],
    body: dict[str, Any] | None,
    err: PayPalError | None,
) -> dict[str, Any]:
    with sf() as s, s.begin():
        p = _load(s, job["proposal_id"])
        inv = s.get(Invoice, job["invoice_id"])
        assert inv is not None
        old = p.result or {}
        attempts = int(old.get("attempts", 0)) + 1
        if err is not None:
            p.status = "FAILED"
            p.result = {**old, "error": err.name, "debug_id": err.debug_id, "attempts": attempts}
            audit.log(
                s,
                "paypal",
                "proposal.paypal_failed",
                ref=str(p.id),
                detail={"error": err.name, "debug_id": err.debug_id},
            )
            log.warning(
                "adjustment PUT failed proposal=%s %s debug_id=%s", p.id, err.name, err.debug_id
            )
            return snapshot(p)
        assert body is not None
        due = to_cents((body.get("due_amount") or {}).get("value", "0"))
        ours = inv.total_cents - inv.paid_cents
        inv.paypal_due_cents = due
        inv.paypal_status = body.get("status") or inv.paypal_status
        inv.paypal_synced_at = datetime.now(UTC)
        inv.mismatch = None if due == ours else f"PayPal due {due} ≠ ours {ours} (cents)"
        p.status = "DONE"
        p.result = {
            **old,
            "paypal_due_cents": due,
            "paypal_status": body.get("status"),
            "attempts": attempts,
            "error": None,
            "debug_id": None,
        }
        audit.log(
            s,
            "paypal",
            "proposal.done",
            ref=str(p.id),
            detail={"paypal_due_cents": due, "mismatch": inv.mismatch},
        )
        return snapshot(p)


async def decide(
    sf: Callable[[], Session],
    pp: PayPal,
    proposal_id: int,
    decision: str,
    buyer_ids: frozenset[int] | None = None,
    mode: str | None = None,
    edits: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if decision not in ("APPROVE", "REJECT"):
        raise ProposalError(422, "BAD_DECISION", "decision must be APPROVE or REJECT")
    snap, job = await run_in_threadpool(_apply, sf, proposal_id, decision, buyer_ids, mode, edits)
    if job is None:
        return snap
    if job.get("kind") == "REMINDER":
        rerr: PayPalError | None = None
        if not job["paypal_invoice_id"]:
            rerr = PayPalError("NOT_MIRRORED", "invoice isn't on PayPal yet", None, [], None, False)
        else:
            try:
                await pinv.remind(
                    pp, job["paypal_invoice_id"], note=job["text"], request_key=job["request_key"]
                )
            except PayPalError as e:
                rerr = e
        return await run_in_threadpool(_finish_reminder, sf, job, rerr)
    body: dict[str, Any] | None = None
    err: PayPalError | None = None
    if not job["paypal_invoice_id"]:
        err = PayPalError("NOT_MIRRORED", "invoice isn't on PayPal yet", None, [], None, False)
    else:
        try:
            body = await pinv.put_adjustment(
                pp,
                job["paypal_invoice_id"],
                proposal_uid=job["proposal_uid"],
                sku=job["sku"],
                amount_cents=job["amount_cents"],
                reason=job["reason"],
            )
        except PayPalError as e:
            err = e
    return await run_in_threadpool(_finish, sf, job, body, err)
