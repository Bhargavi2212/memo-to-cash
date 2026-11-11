"""Supplier routes (P3a A1–A5; BACKEND_SCHEMA §2/§8). HTTP only; the work is in services/.

Auth: `Authorization: Bearer ADMIN_TOKEN`, or in DEMO_MODE the `mtc_demo` cookie (that visitor's
own copy only). Out of scope → 404; no auth → 401 (the UI then goes to the landing page).
"""

from collections.abc import Callable
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from backend.core.config import Settings, get_settings
from backend.core.dates import today_ny
from backend.db.session import get_session, get_session_factory
from backend.paypal.client import PayPal, get_paypal
from backend.services import demo, proposals
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
    edits: dict[str, Any] | None = None  # P3a step a2


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
    if body.edits:
        return _error(
            422, "EDITS_NOT_SUPPORTED", "Edit before approve comes with the supplier screens"
        )
    try:
        return await proposals.decide(
            session_factory, pp, proposal_id, body.decision, scope.buyer_ids
        )
    except proposals.ProposalError as e:
        return _error(e.status, e.code, e.message)
