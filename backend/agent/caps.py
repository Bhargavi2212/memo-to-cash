"""LLM caps (P4 D9, TRD §4): ≤ 20 requests per statement token per New York day, ≤ 500
global. A request = 1 unit however many LLM calls it makes. Over a cap → the caller's D7
fallback (no 429). Demo-start triage counts toward global only (`token=None`).

Each unit is an atomic upsert that only increments while under the cap, so concurrent
requests can't overshoot. The caller commits (same transaction as its message row).
"""

import hashlib
from datetime import date

from sqlalchemy import text
from sqlalchemy.orm import Session

PER_TOKEN = 20
GLOBAL = 500

_TAKE = text(
    """
    INSERT INTO llm_usage (day, scope, calls) VALUES (:day, :scope, 1)
    ON CONFLICT (day, scope) DO UPDATE SET calls = llm_usage.calls + 1
    WHERE llm_usage.calls < :cap
    RETURNING calls
    """
)
_GIVE_BACK = text("UPDATE llm_usage SET calls = calls - 1 WHERE day = :day AND scope = :scope")


def scope_for(token: str) -> str:
    """The token's row key: a hash, never the token itself."""
    return "t:" + hashlib.sha256(token.encode()).hexdigest()


def _take(session: Session, day: date, scope: str, cap: int) -> bool:
    row = session.execute(_TAKE, {"day": day, "scope": scope, "cap": cap}).first()
    return row is not None


def take_unit(session: Session, day: date, token: str | None) -> bool:
    """True = the request may call the model (one unit used); False = a cap is reached."""
    scope = scope_for(token) if token else None
    if scope and not _take(session, day, scope, PER_TOKEN):
        return False
    if not _take(session, day, "global", GLOBAL):
        if scope:
            session.execute(_GIVE_BACK, {"day": day, "scope": scope})
        return False
    return True
