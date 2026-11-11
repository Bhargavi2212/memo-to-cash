"""P4 step 3 — LLM caps on Postgres (D9): 20 per token per day, 500 global, atomic."""

from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import select, text

from backend.agent import caps
from backend.core.dates import today_ny
from backend.db.models import LlmUsage
from backend.db.session import SessionLocal

D = today_ny()


def take(token):
    with SessionLocal() as s, s.begin():
        return caps.take_unit(s, D, token)


def calls(db, scope):
    db.expire_all()
    row = db.scalars(select(LlmUsage).where(LlmUsage.day == D, LlmUsage.scope == scope)).first()
    return row.calls if row else 0


def test_twenty_per_token_then_fallback(db):
    assert [take("tok-a") for _ in range(21)] == [True] * 20 + [False]
    assert take("tok-b")  # another token still has its own 20
    assert calls(db, caps.scope_for("tok-a")) == 20 and calls(db, "global") == 21


def test_token_is_stored_hashed(db):
    take("tok-secret-value")
    scopes = list(db.scalars(select(LlmUsage.scope)))
    assert all("tok-secret-value" not in s for s in scopes)


def test_global_cap_and_the_token_unit_is_given_back(db):
    db.execute(text("INSERT INTO llm_usage (day, scope, calls) VALUES (:d, 'global', 499)"),
               {"d": D})  # fmt: skip
    db.commit()
    assert take("tok-a") is True
    assert take("tok-b") is False
    assert calls(db, "global") == 500 and calls(db, caps.scope_for("tok-b")) == 0


def test_demo_start_counts_global_only(db):
    assert take(None) is True
    assert calls(db, "global") == 1 and db.scalar(select(LlmUsage.scope).limit(2)) == "global"


def test_concurrent_requests_never_overshoot(db):
    with ThreadPoolExecutor(8) as ex:
        got = list(ex.map(take, ["tok-race"] * 30))
    assert got.count(True) == 20 and calls(db, caps.scope_for("tok-race")) == 20
