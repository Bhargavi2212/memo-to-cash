"""Demo dates are relative to D = today in America/New_York (invariant 8)."""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")


def today_ny(now: datetime | None = None) -> date:
    """Today's date in New York. `now` must be timezone-aware (tests pin edges with it)."""
    if now is None:
        return datetime.now(NY).date()
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return now.astimezone(NY).date()


def offset(d: date, days: int) -> date:
    """D±n, e.g. offset(D, -48) for the seed's oldest invoice."""
    return d + timedelta(days=days)


def age_days(issued_on: date, today: date) -> int:
    return (today - issued_on).days


def days_overdue(due_on: date, today: date) -> int:
    """0 on or before the due date."""
    return max(0, (today - due_on).days)
