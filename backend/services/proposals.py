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
from backend.core.money import to_cents
from backend.db.models import Invoice, InvoiceLine, MemoLine, Proposal
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
        if p.kind != "ADJUSTMENT":
            return _approve_other(s, p, edits, now), None

        inv = s.get(Invoice, p.payload["invoice_id"])
        if inv is None or inv.buyer_id != p.buyer_id:
            raise ProposalError(409, "INVOICE_GONE", "The invoice for this proposal is gone")
        sku = str(p.payload.get("sku") or proposal_sku(p) or "")
        if p.status == "PENDING":
            if mode is not None and mode not in MODES:
                raise ProposalError(422, "BAD_MODE", "mode must be APPLY, CREDIT or SPLIT")
            if mode == "SPLIT":
                raise ProposalError(422, "NOT_YET", "Deduct-and-credit comes in a later step")
            if edits:
                _edit_adjustment(s, p, inv, sku, edits)
            amount = -int(p.payload["amount_cents"])  # stored negative (BACKEND_SCHEMA)
            due = inv.total_cents - inv.paid_cents
            if mode is None:  # decided here, under the lock (R5)
                mode = "APPLY" if due >= amount else "CREDIT"
            if mode == "APPLY" and due < amount:
                raise ProposalError(409, "NEEDS_CREDIT", NEEDS_CREDIT_MSG)
            pl = dict(p.payload)
            pl.setdefault(
                "original", {"amount_cents": pl["amount_cents"], "reason": pl.get("reason")}
            )
            pl["mode"] = mode
            pl["credit_cents"] = amount if mode == "CREDIT" else 0
            p.payload = pl  # effective values, before any PayPal call (R1)
            p.decided_at = now
            if mode == "CREDIT":
                p.status = "DONE"
                p.result = {**(p.result or {}), "mode": "CREDIT", "credit_cents": amount}
                audit.log(s, "supplier", "proposal.credited", ref=str(p.id),
                          detail={"invoice": inv.erp_invoice_no, "credit": amount})  # fmt: skip
                return snapshot(p), None
            # memo_line_id stays NULL: the unique indexes are for kept lines (BACKEND_SCHEMA)
            s.add(InvoiceLine(invoice_id=inv.id, memo_line_id=None,
                              name=f"Adjustment {sku}", amount_cents=-amount))  # fmt: skip
            inv.total_cents -= amount
            inv.ledger_status = _ledger_status(inv)
            p.status = "APPROVED"
            audit.log(s, "supplier", "proposal.approved", ref=str(p.id),
                      detail={"invoice": inv.erp_invoice_no, "amount_cents": -amount})  # fmt: skip
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


def _approve_other(s: Session, p: Proposal, edits: dict[str, Any], now: datetime) -> dict:
    """A8 (P3a scope): REPLY / RETURN_CHECK / PROMISE_NOTE → DONE; reply text reaches the buyer."""
    if p.kind == "PROMISE_NOTE":
        if edits:
            raise ProposalError(422, "NOT_YET", "Changing a promised date comes in a later step")
        reply = None
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
            audit.log(s, "paypal", "proposal.paypal_failed", ref=str(p.id),
                      detail={"error": err.name, "debug_id": err.debug_id})  # fmt: skip
            log.warning("adjustment PUT failed proposal=%s %s debug_id=%s", p.id, err.name,
                        err.debug_id)  # fmt: skip
            return snapshot(p)
        assert body is not None
        due = to_cents((body.get("due_amount") or {}).get("value", "0"))
        ours = inv.total_cents - inv.paid_cents
        inv.paypal_due_cents = due
        inv.paypal_status = body.get("status") or inv.paypal_status
        inv.paypal_synced_at = datetime.now(UTC)
        inv.mismatch = None if due == ours else f"PayPal due {due} ≠ ours {ours} (cents)"
        p.status = "DONE"
        p.result = {**old, "paypal_due_cents": due, "paypal_status": body.get("status"),
                    "attempts": attempts, "error": None, "debug_id": None}  # fmt: skip
        audit.log(s, "paypal", "proposal.done", ref=str(p.id),
                  detail={"paypal_due_cents": due, "mismatch": inv.mismatch})  # fmt: skip
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
