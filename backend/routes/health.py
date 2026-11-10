"""Health checks.

GET /api/health/live  public, no DB — Render's health check (lets Neon sleep).
GET /api/health       public. Never 500s: a failed DB check reports db:false.
GET /api/health/deep  admin only. DB + PayPal OAuth + LLM ping in parallel, each with
                      its own time limit. Never returns secrets or token values.
"""

import asyncio
import secrets
from typing import Any

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from backend.agent import llm
from backend.core.config import settings
from backend.db.session import engine

router = APIRouter(prefix="/api", tags=["health"])

DB_TIMEOUT_S = 5.0  # long enough for a sleeping Neon compute to wake
PAYPAL_TIMEOUT_S = 5.0
LLM_TIMEOUT_S = 8.0


def check_db() -> bool:
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return True


def check_paypal_token() -> bool:
    """Fetch an OAuth token only (sandbox). False if keys are missing or PayPal refuses."""
    if not settings.paypal_client_id or settings.paypal_client_secret is None:
        return False
    resp = httpx.post(
        f"{settings.paypal_base_url}/v1/oauth2/token",
        auth=(settings.paypal_client_id, settings.paypal_client_secret.get_secret_value()),
        data={"grant_type": "client_credentials"},
        timeout=PAYPAL_TIMEOUT_S,
    )
    return resp.status_code == 200 and bool(resp.json().get("access_token"))


def check_llm() -> dict[str, Any]:
    r = llm.ping(retry=False)
    return {"ok": r.ok, "model": r.model, "ms": r.ms}


async def _run(fn, timeout: float, failed: Any) -> Any:
    """Run a blocking check in a thread with a time limit; any error -> `failed`."""
    try:
        return await asyncio.wait_for(asyncio.to_thread(fn), timeout)
    except Exception:
        return failed


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


@router.get("/health/live")
async def health_live() -> dict[str, bool]:
    """Render's health check (P2 D24). No database call, so Neon's free-plan compute can
    scale to zero when nobody uses the app (a DB check every few seconds kept it awake
    24/7 and would exhaust the 100 CU-h/month allowance)."""
    return {"ok": True}


@router.get("/health")
async def health() -> dict[str, Any]:
    db = await _run(check_db, DB_TIMEOUT_S, False)
    return {"ok": True, "sandbox": True, "db": db, "env": settings.app_env}


@router.get("/health/deep")
async def health_deep(request: Request) -> Any:
    if settings.admin_token is None:
        return _error(503, "ADMIN_TOKEN_UNSET", "Deep health is unavailable: ADMIN_TOKEN not set")
    given = request.headers.get("authorization", "")
    expected = f"Bearer {settings.admin_token.get_secret_value()}"
    if not secrets.compare_digest(given.encode(), expected.encode()):
        return _error(401, "UNAUTHORIZED", "Admin token required")

    llm_failed = {"ok": False, "model": settings.llm_model, "ms": int(LLM_TIMEOUT_S * 1000)}
    db, paypal_token, llm_result = await asyncio.gather(
        _run(check_db, DB_TIMEOUT_S, False),
        _run(check_paypal_token, PAYPAL_TIMEOUT_S, False),
        _run(check_llm, LLM_TIMEOUT_S, llm_failed),
    )
    return {"db": db, "paypal_token": paypal_token, "llm": llm_result}
