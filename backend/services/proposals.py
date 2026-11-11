"""Supplier decisions on agent proposals (P4 X2 / step 5b; FR-10; invariant 5).

APPROVE on an ADJUSTMENT: one transaction under `pg_advisory_xact_lock(buyer_id)` adds the
negative invoice line, lowers `total_cents`, recomputes `ledger_status` and marks the proposal
APPROVED → commit → PayPal invoice PUT (no lock held) → GET → DONE with PayPal's due amount
(a difference is recorded in `invoices.mismatch`, never overwritten), or FAILED with the error.
APPROVE again on FAILED, or on an APPROVED left by a crash, retries only the PayPal step (the
line's tag makes it idempotent); on DONE it's a no-op. Due < deduction → 409 NEEDS_CREDIT and
nothing changes (Z4; P3a records it as credit). Other kinds → APPROVED, no PayPal call.
REJECT on PENDING → REJECTED.
"""

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from backend.agent.facts import proposal_sku
from backend.core.money import to_cents
from backend.db.models import Invoice, InvoiceLine, Proposal
from backend.paypal import invoices as pinv
from backend.paypal.client import PayPal, PayPalError
from backend.services import audit
from backend.services.payments import _ledger_status

log = logging.getLogger(__name__)

NEEDS_CREDIT_MSG = "Invoice already paid — record it as credit instead"


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


def _apply(
    sf: Callable[[], Session],
    proposal_id: int,
    decision: str,
    buyer_ids: frozenset[int] | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """The ledger part. Returns (proposal snapshot, the PayPal job or None)."""
    with sf() as s, s.begin():
        p = _load(s, proposal_id, buyer_ids)
        now = datetime.now(UTC)
        if decision == "REJECT":
            if p.status != "PENDING":
                raise ProposalError(409, "NOT_PENDING", f"Proposal is {p.status}")
            p.status, p.decided_at = "REJECTED", now
            audit.log(s, "supplier", "proposal.rejected", ref=str(p.id))
            return snapshot(p), None

        if p.status == "REJECTED":
            raise ProposalError(409, "NOT_PENDING", "Proposal is REJECTED")
        if p.status == "DONE":
            return snapshot(p), None  # repeat approve: no-op
        if p.kind != "ADJUSTMENT":
            if p.status == "PENDING":
                p.status, p.decided_at = "APPROVED", now
                audit.log(s, "supplier", "proposal.approved", ref=str(p.id),
                          detail={"kind": p.kind})  # fmt: skip
            return snapshot(p), None

        inv = s.get(Invoice, p.payload["invoice_id"])
        if inv is None or inv.buyer_id != p.buyer_id:
            raise ProposalError(409, "INVOICE_GONE", "The invoice for this proposal is gone")
        amount = -int(p.payload["amount_cents"])  # stored negative (BACKEND_SCHEMA proposals)
        sku = str(p.payload.get("sku") or proposal_sku(p) or "")
        if p.status == "PENDING":
            if inv.total_cents - inv.paid_cents < amount:
                raise ProposalError(409, "NEEDS_CREDIT", NEEDS_CREDIT_MSG)
            # memo_line_id stays NULL: the unique indexes are for kept lines (BACKEND_SCHEMA)
            s.add(InvoiceLine(invoice_id=inv.id, memo_line_id=None,
                              name=f"Adjustment {sku}", amount_cents=-amount))  # fmt: skip
            inv.total_cents -= amount
            inv.ledger_status = _ledger_status(inv)
            p.status, p.decided_at = "APPROVED", now
            audit.log(s, "supplier", "proposal.approved", ref=str(p.id),
                      detail={"invoice": inv.erp_invoice_no, "amount_cents": -amount})  # fmt: skip
        # APPROVED (now, or left by a crash) / FAILED → the PayPal step (again)
        job = {
            "proposal_id": p.id,
            "proposal_uid": str(p.uid),
            "invoice_id": inv.id,
            "paypal_invoice_id": inv.paypal_invoice_id,
            "sku": sku,
            "amount_cents": -amount,
            "reason": str(p.payload.get("reason") or ""),
        }
        return snapshot(p), job


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
        attempts = int((p.result or {}).get("attempts", 0)) + 1
        if err is not None:
            p.status = "FAILED"
            p.result = {"error": err.name, "debug_id": err.debug_id, "attempts": attempts}
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
        p.result = {"paypal_due_cents": due, "paypal_status": body.get("status"),
                    "attempts": attempts}  # fmt: skip
        audit.log(s, "paypal", "proposal.done", ref=str(p.id),
                  detail={"paypal_due_cents": due, "mismatch": inv.mismatch})  # fmt: skip
        return snapshot(p)


async def decide(
    sf: Callable[[], Session],
    pp: PayPal,
    proposal_id: int,
    decision: str,
    buyer_ids: frozenset[int] | None = None,
) -> dict[str, Any]:
    if decision not in ("APPROVE", "REJECT"):
        raise ProposalError(422, "BAD_DECISION", "decision must be APPROVE or REJECT")
    snap, job = await run_in_threadpool(_apply, sf, proposal_id, decision, buyer_ids)
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
