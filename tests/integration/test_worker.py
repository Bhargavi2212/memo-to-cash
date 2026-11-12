"""Worker wake (P3b B5, R8, R9, X5; plan step 4): one sweep per wake; Job C once per New York
date at or after 09:00 — logged before it runs, so a restart never repeats it."""

import asyncio
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select

from backend import worker
from backend.db.models import AuditLog, WebhookEvent
from backend.db.session import SessionLocal
from backend.services import reminders, sync

NY = ZoneInfo("America/New_York")


def at_ny(hour: int, minute: int = 0, day_offset: int = 0) -> datetime:
    d = datetime.now(NY).date() + timedelta(days=day_offset)
    return datetime(d.year, d.month, d.day, hour, minute, tzinfo=NY).astimezone(UTC)


@pytest.fixture
def calls(monkeypatch):
    seen = {"sweep": 0, "daily": []}

    async def fake_sweep(*_a, **_k):
        seen["sweep"] += 1
        return 0

    async def fake_daily(_sf, *, client=None, today=None):
        seen["daily"].append(today)
        return 0

    monkeypatch.setattr(sync, "sweep", fake_sweep)
    monkeypatch.setattr(reminders, "daily_run", fake_daily)
    return seen


def wake(now):
    return asyncio.run(worker.run_once(SessionLocal, None, None, now=now))


def test_job_c_runs_once_per_new_york_date_even_across_restarts(db, calls):
    wake(at_ny(8, 59))
    assert calls["daily"] == []  # not before 09:00 New York
    wake(at_ny(9, 0))
    wake(at_ny(9, 30))  # a restart / the next wake the same day
    assert len(calls["daily"]) == 1 and calls["sweep"] == 3
    assert db.scalar(select(func.count()).where(AuditLog.action == "reminders.daily_run")) == 1
    wake(at_ny(9, 5, day_offset=1))  # the next New York day
    assert len(calls["daily"]) == 2


def test_dev_events_reaching_production_are_not_activity(db):
    db.add(
        WebhookEvent(
            paypal_event_id="WH-dev",
            event_type="INVOICING.INVOICE.PAID",
            payload={},
            error=sync.IGNORED_UNKNOWN,
            processed_at=datetime.now(UTC),
        )
    )
    db.commit()
    with SessionLocal() as s:
        assert worker.last_activity(s) is None  # X5: the worker can still sleep an hour
    db.add(WebhookEvent(paypal_event_id="WH-real", event_type="INVOICING.INVOICE.PAID", payload={}))
    db.commit()
    with SessionLocal() as s:
        last = worker.last_activity(s)
    assert last is not None and worker.next_interval(False, last, datetime.now(UTC)) == 30
