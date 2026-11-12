"""Background worker (P3b B5; TRD §6b-4(b); P2 D24): `python -m backend.worker` on Render.

Loop: `sync.sweep()` → maybe Job C's daily run → sleep. Adaptive: **30 s** while the last sweep
did work or anything happened in the last 15 min (a payment changed, a webhook arrived, a buyer
wrote, a proposal was decided), otherwise **60 min**, so Neon can scale to zero overnight. Dev
events reaching production (`ignored: unknown`) don't count as activity (X5). Job C runs once
per New York date, on the first wake at or after 09:00, and the date is written to `audit_log`
(`reminders.daily_run`) **before** it runs, so a restart never repeats it (R9).
"""

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from backend.db.models import AuditLog, Message, Payment, Proposal, WebhookEvent

log = logging.getLogger("worker")

ACTIVE_S = 30
IDLE_S = 3600
ACTIVITY_WINDOW = timedelta(minutes=15)
DAILY_HOUR_NY = 9
NY = ZoneInfo("America/New_York")


def last_activity(session: Session) -> datetime | None:
    """The latest of the four activity clocks (R8 — real column names)."""
    stamps = [
        session.scalar(select(func.max(Payment.updated_at))),
        session.scalar(
            select(func.max(WebhookEvent.received_at)).where(
                or_(WebhookEvent.error.is_(None), WebhookEvent.error != "ignored: unknown")
            )
        ),  # X5
        session.scalar(select(func.max(Message.created_at))),
        session.scalar(select(func.max(Proposal.decided_at))),
    ]
    found = [s for s in stamps if s is not None]
    return max(found) if found else None


def next_interval(did_work: bool, last: datetime | None, now: datetime) -> int:
    if did_work or (last is not None and now - last < ACTIVITY_WINDOW):
        return ACTIVE_S
    return IDLE_S


def claim_daily_run(session_factory: Callable[[], Session], now: datetime) -> str | None:
    """Today's NY date if Job C should run now (≥ 09:00 NY, not yet logged today) — and log it
    in the same transaction, before running, so a restart can't run it twice."""
    local = now.astimezone(NY)
    if local.hour < DAILY_HOUR_NY:
        return None
    day = local.date().isoformat()
    with session_factory() as s, s.begin():
        done = s.scalar(
            select(func.count()).where(
                AuditLog.action == "reminders.daily_run", AuditLog.ref == day
            )
        )
        if done:
            return None
        s.add(AuditLog(actor="system", action="reminders.daily_run", ref=day))
    return day


async def run_once(
    session_factory: Callable[[], Session],
    pp: Any,
    settings: Any,
    *,
    llm_client: Any = None,
    now: datetime | None = None,
) -> int:
    """One wake: the sweep, then Job C if it's due. Returns the sweep's work count."""
    from backend.services import reminders, sync

    when = now or datetime.now(UTC)
    did = await sync.sweep(session_factory, pp, settings, now=when)
    day = claim_daily_run(session_factory, when)
    if day is not None:
        made = await reminders.daily_run(
            session_factory, client=llm_client, today=datetime.fromisoformat(day).date()
        )
        log.info("reminders.daily_run %s: %d drafts", day, made)
    return did


async def main() -> None:  # pragma: no cover — the loop itself; run_once is what's tested
    from backend.agent import llm
    from backend.core.config import settings
    from backend.db.session import SessionLocal
    from backend.paypal.client import PayPal

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    pp, client = PayPal(settings), llm.get_job_client()
    log.info("worker started")
    while True:
        try:
            did = await run_once(SessionLocal, pp, settings, llm_client=client)
            with SessionLocal() as s:
                last = last_activity(s)
        except Exception:
            log.exception("worker wake failed")
            did, last = 0, None
        wait = next_interval(did > 0, last, datetime.now(UTC))
        log.info("sleeping %s", "30 s" if wait == ACTIVE_S else "60 min")
        await asyncio.sleep(wait)


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(main())
