"""A demo copy as /demo/start leaves it, without PayPal: the seed import + seed message
(`_create_copy`) and the finishing transaction with the seed proposal (triage not run)."""

from datetime import UTC, date, datetime

from backend.agent.triage import TriageOutcome
from backend.core.config import Settings
from backend.db.session import SessionLocal
from backend.services import demo


def demo_copy(settings: Settings, demo_session: str, today: date) -> tuple[int, str]:
    bid, token, _ids, _facts, _allowed = demo._create_copy(
        SessionLocal, settings, demo_session, today
    )
    demo._finish(SessionLocal, bid, "test-ip", datetime.now(UTC),
                 TriageOutcome(None, {}, None, "not_run"))  # fmt: skip
    return bid, token
