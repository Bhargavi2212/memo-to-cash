"""Supplier routes (admin = `Authorization: Bearer ADMIN_TOKEN`, BACKEND_SCHEMA §8).

P4 (X2, step 5b): only the decide endpoint, so the AI → proposal → PayPal invoice loop works
before P3a's screens; P3a adds the rest of this file and the demo-cookie access.
"""

import secrets
from collections.abc import Callable
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.core.config import Settings, get_settings
from backend.db.session import get_session_factory
from backend.paypal.client import PayPal, get_paypal
from backend.services import proposals

router = APIRouter(prefix="/api/supplier")


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)


def _admin_error(request: Request, settings: Settings) -> JSONResponse | None:
    if settings.admin_token is None:
        return _error(503, "ADMIN_TOKEN_UNSET", "Supplier API unavailable: ADMIN_TOKEN not set")
    given = request.headers.get("authorization", "")
    expected = f"Bearer {settings.admin_token.get_secret_value()}"
    if not secrets.compare_digest(given.encode(), expected.encode()):
        return _error(401, "UNAUTHORIZED", "Admin token required")
    return None


class DecideIn(BaseModel):
    decision: Literal["APPROVE", "REJECT"]
    edits: dict[str, Any] | None = None  # P3a (edit before approve)


@router.post("/proposals/{proposal_id}/decide")
async def decide(
    proposal_id: int,
    body: DecideIn,
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    pp: Annotated[PayPal, Depends(get_paypal)],
    session_factory: Annotated[Callable[[], Session], Depends(get_session_factory)],
) -> Any:
    if err := _admin_error(request, settings):
        return err
    if body.edits:
        return _error(
            422, "EDITS_NOT_SUPPORTED", "Edit before approve comes with the supplier screens"
        )
    try:
        return await proposals.decide(session_factory, pp, proposal_id, body.decision)
    except proposals.ProposalError as e:
        return _error(e.status, e.code, e.message)
