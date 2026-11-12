"""Worker cadence (P3b B5, P2 D24): 30 s while busy or recently active, else 60 min."""

from datetime import UTC, datetime, timedelta

from backend.worker import ACTIVE_S, IDLE_S, next_interval

NOW = datetime(2026, 11, 12, 14, 0, tzinfo=UTC)


def test_work_done_keeps_it_at_30_s():
    assert next_interval(True, None, NOW) == ACTIVE_S == 30


def test_activity_in_the_last_15_minutes_keeps_it_at_30_s():
    assert next_interval(False, NOW - timedelta(minutes=14, seconds=59), NOW) == ACTIVE_S


def test_quiet_for_15_minutes_sleeps_an_hour():
    assert next_interval(False, NOW - timedelta(minutes=15), NOW) == IDLE_S == 3600
    assert next_interval(False, None, NOW) == IDLE_S  # nothing ever happened
