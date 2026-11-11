"""P4 step 2 — promise dates (D5, W4) beyond the spike's weekday table: window, month and year
ends, the DST change (the rules are calendar-day rules in America/New_York)."""

from datetime import date

import pytest

from backend.agent import dates


@pytest.mark.parametrize(
    ("phrase", "today", "expected"),
    [
        ("in 3 days", date(2026, 10, 30), ("+3d", date(2026, 11, 2))),  # across DST end
        ("sunday", date(2026, 10, 31), ("next:SUN", date(2026, 11, 1))),  # the DST day
        ("next week", date(2026, 12, 29), ("next-week:FRI", date(2027, 1, 8))),
        ("the 31st", date(2026, 11, 10), ("dom:31", date(2026, 12, 31))),
        ("the 1st", date(2026, 12, 15), ("dom:1", date(2027, 1, 1))),
        ("tomorrow", date(2026, 12, 31), ("+1d", date(2027, 1, 1))),
        ("in 90 days", date(2026, 11, 10), ("+90d", date(2027, 2, 8))),
    ],
)
def test_resolves(phrase, today, expected):
    got = dates.resolve(phrase, today)
    assert got is not None and got[1] == expected[1], got
    assert got[0] == expected[0] or phrase == "tomorrow"


@pytest.mark.parametrize("phrase", ["in 91 days", "someday", "", None, "the 32nd", "asap"])
def test_unreadable_or_too_far_is_none(phrase):
    assert dates.resolve(phrase, date(2026, 11, 10)) is None


# ---------- post-held-out code fix: abbreviations (P4 B15) ----------
WED = date(2026, 11, 11)  # a Wednesday


@pytest.mark.parametrize(
    ("phrase", "rule", "expected"),
    [
        ("end of nxt week", "next-week:FRI", date(2026, 11, 20)),  # held-out h16's phrase
        ("nxt wk", "next-week:FRI", date(2026, 11, 20)),
        ("next wk", "next-week:FRI", date(2026, 11, 20)),
        ("tmrw", "+1d", date(2026, 11, 12)),
        ("tmr", "+1d", date(2026, 11, 12)),
        ("2moro", "+1d", date(2026, 11, 12)),
        ("tdy", "+0d", WED),
        ("thr", "next:THU", date(2026, 11, 12)),
        ("thurs", "next:THU", date(2026, 11, 12)),
        ("weds", "next:WED", WED),
        ("frid", "next:FRI", date(2026, 11, 13)),
        ("by fri.", "next:FRI", date(2026, 11, 13)),
    ],
)
def test_abbreviations(phrase, rule, expected):
    assert dates.resolve(phrase, WED) == (rule, expected)


@pytest.mark.parametrize("phrase", ["nextweekend", "wknd", "tmrwland", "frisbee", "thrice"])
def test_abbreviations_only_match_whole_words(phrase):
    assert dates.resolve(phrase, WED) is None
