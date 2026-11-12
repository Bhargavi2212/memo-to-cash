"""PayPal sync (FR-7; P3b B1–B4, F4, F8; TRD §6b–6b-5): webhook events and the sweep.

1a: `store_event` — one row per PayPal event id (redelivery → nothing new).
"""

import logging
from collections.abc import Callable
from typing import Any

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from backend.db.models import WebhookEvent

log = logging.getLogger("sync")


def store_event(session_factory: Callable[[], Session], event: dict[str, Any]) -> int | None:
    """Insert the verified event (raw payload). Returns its row id, or None if PayPal already
    delivered it (unique `paypal_event_id`, invariant 4)."""
    with session_factory() as s, s.begin():
        row_id = s.execute(
            pg_insert(WebhookEvent)
            .values(paypal_event_id=event["id"], event_type=event["event_type"], payload=event)
            .on_conflict_do_nothing(index_elements=[WebhookEvent.paypal_event_id])
            .returning(WebhookEvent.id)
        ).scalar_one_or_none()
    # event type and id only — payloads carry payer emails (CLAUDE §4: never log them at INFO)
    log.info("webhook.received id=%s type=%s new=%s verified=true",
             event["id"], event["event_type"], row_id is not None)  # fmt: skip
    return row_id
