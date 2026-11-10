"""One audit row per money/agent/import event (TRD §4 Observability)."""

from typing import Any

from sqlalchemy.orm import Session

from backend.db.models import AuditLog


def log(
    session: Session,
    actor: str,
    action: str,
    ref: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    """Add an audit row to the caller's transaction (the caller commits)."""
    session.add(AuditLog(actor=actor, action=action, ref=ref, detail=detail))
