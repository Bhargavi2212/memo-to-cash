"""POST /api/paypal/webhook (FR-7; P3b B1, §9 F1; plan 1a, G4).

Registered on the **api's public URL** (TRD §7), not through the web proxy, so PayPal's headers
and raw body arrive untouched. HTTP only — verification is in `paypal/webhooks.py`, storing and
processing in `services/sync.py`.
  no PAYPAL_WEBHOOK_ID → 503 NOT_CONFIGURED · not an event / missing headers → 400 (PayPal not
  asked) · signature FAILURE → 400, not stored · PayPal's verify unreachable → 503 (PayPal
  retries) · verified → stored once → 200 → processed in a BackgroundTask (a redelivery of an
  event we already have isn't processed again here; the sweep retries unprocessed ones).
"""

import json
import logging
from collections.abc import Callable
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from backend.core.config import Settings, get_settings
from backend.db.session import get_session_factory
from backend.paypal import webhooks
from backend.paypal.client import PayPal, PayPalError, get_paypal
from backend.services import sync

router = APIRouter(prefix="/api")
log = logging.getLogger("webhook")


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)


def _event(raw: bytes) -> dict[str, Any] | None:
    try:
        ev = json.loads(raw)
    except ValueError:
        return None
    ok = isinstance(ev, dict) and isinstance(ev.get("id"), str)
    return ev if ok and isinstance(ev.get("event_type"), str) else None


@router.post("/paypal/webhook")
async def paypal_webhook(
    request: Request,
    background: BackgroundTasks,
    settings: Annotated[Settings, Depends(get_settings)],
    pp: Annotated[PayPal, Depends(get_paypal)],
    session_factory: Annotated[Callable[[], Session], Depends(get_session_factory)],
) -> Any:
    if not settings.paypal_webhook_id:
        log.error("webhook received but PAYPAL_WEBHOOK_ID is not set")
        return _error(503, "NOT_CONFIGURED", "Webhook isn't configured")
    raw = await request.body()
    event = _event(raw)
    fields = webhooks.transmission(request.headers)
    if event is None or fields is None:
        return _error(400, "BAD_REQUEST", "Not a PayPal webhook event")
    try:
        verified = await webhooks.verify(pp, fields, settings.paypal_webhook_id, raw)
    except PayPalError as e:
        if e.transient:
            return _error(503, "PAYPAL_UNAVAILABLE", "Couldn't verify with PayPal — retry")
        log.error("webhook verify rejected: %s debug_id=%s", e.name, e.debug_id)
        return _error(400, "NOT_VERIFIED", "Signature not verified")
    if not verified:
        log.warning(
            "webhook signature not verified id=%s type=%s", event["id"], event["event_type"]
        )
        return _error(400, "NOT_VERIFIED", "Signature not verified")
    row_id = await run_in_threadpool(sync.store_event, session_factory, event)
    if row_id is not None:  # 200 first; processing runs after the response (B1)
        background.add_task(sync.process_then_sweep, session_factory, pp, settings, row_id)
    return {"ok": True}
