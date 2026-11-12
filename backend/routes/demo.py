"""POST /api/demo/start — each visitor gets their own demo copy (FR-12, P1 D8, D16–D18).

HTTP only: the work is in services/demo.py. Public route, DEMO_MODE only.
"""

from collections.abc import Callable
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.agent import llm
from backend.core.config import Settings, get_settings
from backend.db.session import get_session, get_session_factory
from backend.paypal.client import PayPal, get_paypal
from backend.services import demo

router = APIRouter(prefix="/api")


class DemoStartOut(BaseModel):
    statement_token: str
    buyer_id: int
    ms: int


def _error(status: int, code: str, message: str, debug_id: str | None = None) -> JSONResponse:
    err: dict[str, str] = {"code": code, "message": message}
    if debug_id:
        err["paypal_debug_id"] = debug_id
    return JSONResponse({"error": err}, status_code=status)


@router.post("/demo/start", response_model=DemoStartOut)
async def demo_start(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    pp: Annotated[PayPal, Depends(get_paypal)],
    session_factory: Annotated[Callable[[], Session], Depends(get_session_factory)],
    client: Annotated[Any, Depends(llm.get_job_client)],
):
    if not settings.demo_mode:
        return _error(404, "NOT_FOUND", "Not found")
    ip = demo.client_ip(
        request.headers.get("x-forwarded-for"), request.client.host if request.client else None
    )
    try:
        started = await demo.start_demo(settings, pp, session_factory, ip=ip, client=client)
    except demo.DemoError as e:
        return _error(e.status, e.code, e.message, e.debug_id)
    resp = JSONResponse(
        DemoStartOut(
            statement_token=started.statement_token, buyer_id=started.buyer_id, ms=started.ms
        ).model_dump()
    )
    resp.set_cookie(
        demo.COOKIE_NAME,
        started.cookie,
        max_age=demo.COOKIE_MAX_AGE,
        path="/",
        secure=True,
        httponly=True,
        samesite="lax",
    )
    return resp


@router.get("/demo/whoami")
def demo_whoami(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, str | None]:
    """The statement token of the cookie's own demo copy (P3 A10, R10): the "See it as Demo
    Diamond Co." link shows only on that copy's pages. No cookie / not demo mode → null."""
    from backend.services import supplier

    return {
        "statement_token": supplier.whoami(session, settings, request.cookies.get(demo.COOKIE_NAME))
    }
