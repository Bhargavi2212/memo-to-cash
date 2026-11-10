"""Buyer statement routes (FR-3, FR-4). HTTP only; the work is in services/.

Auth = the statement token in the path (BACKEND_SCHEMA §8); every query is scoped to the
token's buyer (invariant 7).
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, StrictInt
from sqlalchemy.orm import Session

from backend.core.config import Settings, get_settings
from backend.core.dates import today_ny
from backend.core.split import SplitError
from backend.db.session import get_session
from backend.services import statement as svc

router = APIRouter(prefix="/api")


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)


NOT_FOUND = ("NOT_FOUND", "This statement link isn't valid. Ask your supplier for a new one.")


class PreviewIn(BaseModel):
    invoice_ids: list[StrictInt] | None = None
    amount_cents: StrictInt | None = None  # integer cents only (invariant 1)


@router.get("/statement/{token}", response_model=svc.Statement)
def get_statement(
    token: str,
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
):
    try:
        return svc.get_statement(session, token, today_ny(), settings)
    except svc.StatementNotFoundError:
        return _error(404, *NOT_FOUND)


@router.post("/statement/{token}/preview", response_model=svc.Preview)
def preview(token: str, body: PreviewIn, session: Annotated[Session, Depends(get_session)]):
    try:
        return svc.preview(session, token, body.invoice_ids, body.amount_cents)
    except svc.StatementNotFoundError:
        return _error(404, *NOT_FOUND)
    except SplitError as e:
        return _error(422, e.code, e.message)
