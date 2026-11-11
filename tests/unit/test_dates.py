"""Invariant 8: demo dates are relative to D (today in America/New_York).

The fixed instants below pin timezone edges (DST, midnight); they are not seed or UI dates.
"""

from datetime import UTC, date, datetime, timedelta

import pytest

from backend.core.dates import NY, age_days, days_overdue, offset, today_ny


def test_today_ny_uses_new_york_not_utc():
    # 03:30 UTC is still the previous evening in New York.
    now = datetime(2026, 3, 10, 3, 30, tzinfo=UTC)
    assert today_ny(now) == date(2026, 3, 9)


@pytest.mark.parametrize(
    ("utc", "expected"),
    [
        # DST starts in New York at 2026-03-08 02:00 local (UTC-5 → UTC-4).
        (datetime(2026, 3, 8, 4, 59, tzinfo=UTC), date(2026, 3, 7)),  # 23:59 EST
        (datetime(2026, 3, 8, 5, 0, tzinfo=UTC), date(2026, 3, 8)),  # 00:00 EST
        # DST ends 2026-11-01 02:00 local (UTC-4 → UTC-5).
        (datetime(2026, 11, 1, 3, 59, tzinfo=UTC), date(2026, 10, 31)),  # 23:59 EDT
        (datetime(2026, 11, 2, 4, 59, tzinfo=UTC), date(2026, 11, 1)),  # 23:59 EST
        (datetime(2026, 11, 2, 5, 0, tzinfo=UTC), date(2026, 11, 2)),
    ],
)
def test_today_ny_across_dst(utc, expected):
    assert today_ny(utc) == expected


def test_today_ny_default_is_now_in_ny():
    assert today_ny() == datetime.now(NY).date()


def test_today_ny_rejects_naive_datetime():
    with pytest.raises(ValueError):
        today_ny(datetime(2026, 3, 8, 12, 0))


def test_offset_is_relative_to_d():
    d = today_ny()
    assert offset(d, -48) == d - timedelta(days=48)
    assert offset(d, 25) == d + timedelta(days=25)
    assert offset(d, 0) == d


def test_age_days():
    d = today_ny()
    assert age_days(offset(d, -48), d) == 48
    assert age_days(d, d) == 0


def test_days_overdue_zero_on_and_before_due_date():
    d = today_ny()
    assert days_overdue(d, d) == 0
    assert days_overdue(offset(d, 10), d) == 0


def test_days_overdue_seed_invoice():
    d = today_ny()
    assert days_overdue(offset(d, -18), d) == 18  # INV-1038 in the seed


def test_ny_date_of_a_late_evening_timestamp():
    from backend.core.dates import ny_date

    late = datetime(2026, 11, 10, 23, 30, tzinfo=NY)  # 04:30 UTC on Nov 11
    assert late.astimezone(UTC).date() == date(2026, 11, 11)
    assert ny_date(late) == date(2026, 11, 10)
    assert ny_date(late.astimezone(UTC)) == date(2026, 11, 10)
    with pytest.raises(ValueError):
        ny_date(datetime(2026, 11, 10, 23, 30))
