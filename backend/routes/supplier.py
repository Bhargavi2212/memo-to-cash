"""Supplier routes (P3a A1–A5; BACKEND_SCHEMA §2/§8). HTTP only; the work is in services/.

Auth: `Authorization: Bearer ADMIN_TOKEN`, or in DEMO_MODE the `mtc_demo` cookie (that visitor's
own copy only). Out of scope → 404; no auth → 401 (the UI then goes to the landing page).
"""

import uuid
from collections.abc import Callable
from datetime import date
from typing import Annotated, Any, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, StrictInt
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from backend.core.config import Settings, get_settings
from backend.core.dates import today_ny
from backend.core.split import SplitError
from backend.db.session import get_session, get_session_factory
from backend.paypal.client import PayPal, get_paypal
from backend.services import demo, payments, proposals
from backend.services import supplier as svc

router = APIRouter(prefix="/api/supplier")

NOT_FOUND = ("NOT_FOUND", "Not found")


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)


def _scope(request: Request, session: Session, settings: Settings) -> svc.Scope:
    return svc.resolve_scope(
        session,
        settings,
        request.headers.get("authorization"),
        request.cookies.get(demo.COOKIE_NAME),
    )


@router.get("/overview")
def overview(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> Any:
    try:
        return svc.overview(session, _scope(request, session, settings), today_ny(), settings)
    except svc.ScopeError as e:
        return _error(e.status, e.code, e.message)


@router.get("/buyers/{buyer_id}")
def buyer(
    buyer_id: int,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> Any:
    try:
        scope = _scope(request, session, settings)
        return svc.buyer_detail(session, scope, buyer_id, today_ny(), settings)
    except svc.ScopeError as e:
        return _error(e.status, e.code, e.message)
    except svc.NotFoundError:
        return _error(404, *NOT_FOUND)


@router.get("/proposals")
def proposal_list(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    status: str = "PENDING,FAILED",
) -> Any:
    try:
        scope = _scope(request, session, settings)
    except svc.ScopeError as e:
        return _error(e.status, e.code, e.message)
    wanted = [s.strip().upper() for s in status.split(",") if s.strip()]
    return [c.model_dump() for c in svc.proposal_cards(session, scope, wanted)]


class DecideIn(BaseModel):
    decision: Literal["APPROVE", "REJECT"]
    mode: Literal["APPLY", "CREDIT", "SPLIT"] | None = None  # None → decided under the lock (A6)
    edits: dict[str, Any] | None = None  # A7: {amount_cents?, reason?} or {text} (reply)


def _scope_in_thread(
    request: Request, session_factory: Callable[[], Session], settings: Settings
) -> svc.Scope:
    with session_factory() as s:
        return _scope(request, s, settings)


@router.post("/proposals/{proposal_id}/decide")
async def decide(
    proposal_id: int,
    body: DecideIn,
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    pp: Annotated[PayPal, Depends(get_paypal)],
    session_factory: Annotated[Callable[[], Session], Depends(get_session_factory)],
) -> Any:
    try:
        scope = await run_in_threadpool(_scope_in_thread, request, session_factory, settings)
    except svc.ScopeError as e:
        return _error(e.status, e.code, e.message)
    try:
        return await proposals.decide(
            session_factory, pp, proposal_id, body.decision, scope.buyer_ids, body.mode,
            body.edits,
        )  # fmt: skip
    except proposals.ProposalError as e:
        return _error(e.status, e.code, e.message)


# ---------- record an off-PayPal payment (A9, FR-14) ----------


class RecordIn(BaseModel):
    buyer_id: StrictInt
    client_uid: uuid.UUID  # makes a double-click a no-op (R3)
    method: Literal["CHECK", "WIRE_TRANSFER", "OTHER"]
    reference: str = ""
    amount_cents: StrictInt | None = None  # integer cents only (invariant 1)
    invoice_ids: list[StrictInt] | None = None
    received_on: date | None = None


def _record_txn(
    request: Request, session_factory: Callable[[], Session], settings: Settings, body: RecordIn
) -> tuple[int, bool]:
    with session_factory() as s:
        scope = _scope(request, s, settings)
        svc.buyer_in_scope(s, scope, body.buyer_id)  # 404 outside the scope
    return payments.record_off_paypal(
        session_factory,
        buyer_id=body.buyer_id,
        client_uid=body.client_uid,
        method=body.method,
        reference=body.reference,
        amount_cents=body.amount_cents,
        invoice_ids=body.invoice_ids,
        received_on=body.received_on,
    )


@router.post("/payments/record")
async def record_payment(
    body: RecordIn,
    request: Request,
    background: BackgroundTasks,
    settings: Annotated[Settings, Depends(get_settings)],
    pp: Annotated[PayPal, Depends(get_paypal)],
    session_factory: Annotated[Callable[[], Session], Depends(get_session_factory)],
) -> Any:
    try:
        pid, repeat = await run_in_threadpool(_record_txn, request, session_factory, settings, body)
    except svc.ScopeError as e:
        return _error(e.status, e.code, e.message)
    except svc.NotFoundError:
        return _error(404, *NOT_FOUND)
    except SplitError as e:
        return _error(422, e.code, e.message)
    except payments.RecordError as e:
        return _error(e.status, e.code, e.message)
    # record on each PayPal invoice after the response (same job as statement payments, R3)
    background.add_task(payments.record_allocations, session_factory, pp, pid)
    with session_factory() as s:
        out = svc.payment_summary(s, pid)
    return out | {"repeat": repeat}
