"""Payment-promise date phrases → a fixed rule + a date (P4 D5, R5, W4; invariant 8).

The model returns the phrase as the buyer wrote it ("friday", "next week", "the 20th",
"in 3 days"); this code — never the model — turns it into a date in America/New_York.
Rules (W4, plan P1 — today counts):
- a weekday            → `next:FRI`       first such weekday on or after today
- "next week"          → `next-week:FRI`  Friday of next week
- "the 20th" / "20th"  → `dom:20`         the next 20th on or after today
- "in 3 days", "tomorrow", "today" → `+3d` / `+1d` / `+0d`
Anything else → None (the caller answers with a REPLY). Dates more than 90 days out → None.
"""

import re
from datetime import date, timedelta

WEEKDAYS = {
    "mon": 0, "monday": 0, "tue": 1, "tues": 1, "tuesday": 1, "wed": 2, "wednesday": 2,
    "thu": 3, "thur": 3, "thurs": 3, "thursday": 3, "fri": 4, "friday": 4,
    "sat": 5, "saturday": 5, "sun": 6, "sunday": 6,
}  # fmt: skip
CODES = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")
MAX_DAYS = 90


def resolve(phrase: str | None, today: date) -> tuple[str, date] | None:
    if not phrase:
        return None
    s = phrase.strip().lower()
    out: tuple[str, date] | None = None
    if re.search(r"\bnext\s+week\b", s):
        monday_next = today + timedelta(days=7 - today.weekday())
        out = ("next-week:FRI", monday_next + timedelta(days=4))
    elif m := re.search(r"\bin\s+(\d{1,2})\s+days?\b", s):
        n = int(m.group(1))
        out = (f"+{n}d", today + timedelta(days=n))
    elif re.search(r"\btomorrow\b", s):
        out = ("+1d", today + timedelta(days=1))
    elif re.search(r"\btoday\b", s):
        out = ("+0d", today)
    elif m := re.search(r"\b(?:the\s+)?(\d{1,2})(?:st|nd|rd|th)\b", s):
        day = int(m.group(1))
        out = _next_dom(day, today)
    else:
        for word in re.findall(r"[a-z]+", s):
            if word in WEEKDAYS:
                wd = WEEKDAYS[word]
                d = today + timedelta(days=(wd - today.weekday()) % 7)
                out = (f"next:{CODES[wd]}", d)
                break
    if out and 0 <= (out[1] - today).days <= MAX_DAYS:
        return out
    return None


def _next_dom(day: int, today: date) -> tuple[str, date] | None:
    if not 1 <= day <= 31:
        return None
    y, m = today.year, today.month
    for _ in range(3):
        try:
            d = date(y, m, day)
        except ValueError:  # e.g. the 31st in a 30-day month
            d = None
        if d and d >= today:
            return (f"dom:{day}", d)
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return None
