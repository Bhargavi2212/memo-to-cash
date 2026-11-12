"""Buyer statement routes (FR-3, FR-4). HTTP only; the work is in services/.

Auth = the statement token in the path (BACKEND_SCHEMA §8); every query is scoped to the
token's buyer (invariant 7).
"""

from collections.abc import Callable
from dataclasses import asdict
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, StrictInt
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from backend.agent import llm
from backend.core.config import Settings, get_settings
from backend.core.dates import today_ny
from backend.core.split import SplitError
from backend.db.session import get_session, get_session_factory
from backend.paypal.client import PayPal, get_paypal
from backend.services import agent, mirror, payments
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


# ---------- payments (P2 D4, D5, D11, D13, R4, R14) ----------


class OrderIn(BaseModel):
    invoice_ids: list[StrictInt] | None = None
    amount_cents: StrictInt | None = None
    funding_source: str | None = None  # the JS SDK's data.paymentSource (R3)


class OrderOut(BaseModel):
    paypal_order_id: str


class CaptureIn(BaseModel):
    paypal_order_id: str


def _pay_error(e: payments.PaymentError) -> JSONResponse:
    err: dict[str, str] = {"code": e.code, "message": e.message}
    if e.debug_id:
        err["paypal_debug_id"] = e.debug_id
    return JSONResponse({"error": err}, status_code=e.status)


@router.post("/statement/{token}/order", response_model=OrderOut)
async def create_order(
    token: str,
    body: OrderIn,
    pp: Annotated[PayPal, Depends(get_paypal)],
    session_factory: Annotated[Callable[[], Session], Depends(get_session_factory)],
):
    try:
        o = await payments.create_order(
            session_factory, pp, token, body.invoice_ids, body.amount_cents, body.funding_source
        )
    except svc.StatementNotFoundError:
        return _error(404, *NOT_FOUND)
    except SplitError as e:
        return _error(422, e.code, e.message)
    except payments.PaymentError as e:
        return _pay_error(e)
    return OrderOut(paypal_order_id=o.paypal_order_id)


def _receipt(session_factory: Callable[[], Session], token: str, payment_id: int) -> svc.Receipt:
    with session_factory() as s:
        return svc.get_receipt(s, token, payment_id)


@router.post("/statement/{token}/capture", response_model=svc.Receipt)
async def capture(
    token: str,
    body: CaptureIn,
    background: BackgroundTasks,
    pp: Annotated[PayPal, Depends(get_paypal)],
    session_factory: Annotated[Callable[[], Session], Depends(get_session_factory)],
):
    """Answers right after the capture commit; recording on PayPal runs afterwards as a
    BackgroundTask and the receipt page polls (R14). A repeat returns the same receipt."""
    try:
        r = await payments.capture_payment(session_factory, pp, token, body.paypal_order_id)
        receipt = await run_in_threadpool(_receipt, session_factory, token, r.payment_id)
    except svc.StatementNotFoundError:
        return _error(404, *NOT_FOUND)
    except payments.PaymentError as e:
        return _pay_error(e)
    if any(a.record_state == "PENDING" for a in receipt.allocations):
        background.add_task(payments.record_allocations, session_factory, pp, r.payment_id)
    return receipt


@router.get("/statement/{token}/payments/{payment_id}", response_model=svc.Receipt)
def get_receipt(token: str, payment_id: int, session: Annotated[Session, Depends(get_session)]):
    try:
        return svc.get_receipt(session, token, payment_id)
    except svc.StatementNotFoundError:
        return _error(404, *NOT_FOUND)


@router.post("/statement/{token}/payments/{payment_id}/refresh", response_model=svc.Receipt)
async def refresh(
    token: str,
    payment_id: int,
    pp: Annotated[PayPal, Depends(get_paypal)],
    session_factory: Annotated[Callable[[], Session], Depends(get_session_factory)],
):
    """'Retry updating PayPal' (R4): resolve_recording for this payment only, then the receipt."""
    try:
        await run_in_threadpool(_receipt, session_factory, token, payment_id)  # scope check first
        await payments.resolve_recording(session_factory, pp, payment_id)
        return await run_in_threadpool(_receipt, session_factory, token, payment_id)
    except svc.StatementNotFoundError:
        return _error(404, *NOT_FOUND)


# ---------- agent: ask + message (P4 D10; FR-8, FR-9) ----------

ASK_MAX, MESSAGE_MAX = 500, 2000


class AskIn(BaseModel):
    question: str


class MessageIn(BaseModel):
    text: str


class AskOut(BaseModel):
    answer: str
    cited_records: list[str]
    looked_up: list[str]
    confidence: str
    fallback: bool
    proposal_id: int | None = None


class MessageOut(BaseModel):
    received: bool
    label: str | None
    summary: str
    checks: list[str]
    proposal_id: int | None = None
    proposal_ids: list[int]
    answer: str | None = None
    fallback: bool


def _text_error(value: str, limit: int) -> JSONResponse | None:
    if not value.strip():
        return _error(422, "TEXT_EMPTY", "Type a question or message first.")
    if len(value) > limit:
        return _error(422, "TEXT_TOO_LONG", f"Please keep it under {limit} characters.")
    return None


@router.post("/statement/{token}/ask", response_model=AskOut)
async def ask(
    token: str,
    body: AskIn,
    session_factory: Annotated[Callable[[], Session], Depends(get_session_factory)],
    client: Annotated[Any, Depends(llm.get_job_client)],
):
    if err := _text_error(body.question, ASK_MAX):
        return err
    try:
        r = await agent.ask(session_factory, token, body.question.strip(), client=client)
    except svc.StatementNotFoundError:
        return _error(404, *NOT_FOUND)
    return AskOut(**asdict(r))


@router.post("/statement/{token}/message", response_model=MessageOut)
async def message(
    token: str,
    body: MessageIn,
    session_factory: Annotated[Callable[[], Session], Depends(get_session_factory)],
    client: Annotated[Any, Depends(llm.get_job_client)],
):
    if err := _text_error(body.text, MESSAGE_MAX):
        return err
    try:
        r = await agent.message(session_factory, token, body.text.strip(), client=client)
    except svc.StatementNotFoundError:
        return _error(404, *NOT_FOUND)
    return MessageOut(**asdict(r))


class SetupOut(BaseModel):
    paypal_setup: str  # READY | SETTING_UP | FAILED


def _buyer_id(session_factory: Callable[[], Session], token: str) -> int:
    with session_factory() as s:
        return svc.find_buyer(s, token).id


@router.post("/statement/{token}/setup", response_model=SetupOut)
async def setup(
    token: str,
    pp: Annotated[PayPal, Depends(get_paypal)],
    settings: Annotated[Settings, Depends(get_settings)],
    session_factory: Annotated[Callable[[], Session], Depends(get_session_factory)],
):
    """P3 §M M9: a buyer whose invoices aren't on PayPal yet — run or join its setup, wait
    up to 10 s, answer the state. READY → at once, no PayPal call."""
    try:
        buyer_id = await run_in_threadpool(_buyer_id, session_factory, token)
    except svc.StatementNotFoundError:
        return _error(404, *NOT_FOUND)
    state = await mirror.ensure_setup(buyer_id, pp, settings, session_factory)
    return SetupOut(paypal_setup=state)
