"""Supplier import (FR-1; P3b B7, F6, F7, F8, G6, G7, G20; APP_FLOW J5).

The P1 importer (validate every row first; any bad row → nothing written, every error with its
row number) → each buyer with invoices not yet on PayPal goes through the **same** setup as a
demo copy's Harbor/Lakeside (`mirror.start_setup`, P3b §3 / phase M) → wait at most 20 s (F8):
whatever isn't on PayPal by then keeps going in the background, and the sweep finishes the rest.
The answer lists each buyer with its statement link and PayPal setup state (G7), because the
supplier pages in the browser only show demo copies.
"""

import asyncio
from collections.abc import Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from backend.core.config import Settings
from backend.db.models import Buyer, Invoice
from backend.paypal.client import PayPal
from backend.services import mirror
from backend.services.demo import build_demo_csv
from backend.services.importer import import_csv

MAX_BYTES = 1_000_000
MIRROR_WAIT_S = 20.0
SAMPLE_EMAIL = "buyer@example.com"  # sample-file buyers: plus-addressed like phase M (G1)


def sample_csv(today: Any) -> str:
    """The three-buyer sample, built on request with today's dates (invariant 8, G6)."""
    return build_demo_csv(today, SAMPLE_EMAIL)


def _import(session_factory: Callable[[], Session], text: str) -> tuple[Any, list[int]]:
    with session_factory() as s, s.begin():
        res = import_csv(s, text, actor="supplier")
        if not res.ok:
            return res, []
        drafts = list(
            s.scalars(
                select(Invoice.id).where(
                    Invoice.buyer_id.in_(res.buyer_ids), Invoice.ledger_status == "DRAFT"
                )
            )
        )
        return res, drafts


def _report(
    session_factory: Callable[[], Session],
    buyer_ids: list[int],
    drafts: list[int],
    settings: Settings,
) -> dict[str, Any]:
    with session_factory() as s:
        invs = list(s.scalars(select(Invoice).where(Invoice.id.in_(drafts or [-1]))))
        mirrored = sum(1 for i in invs if i.ledger_status != "DRAFT")
        errors = [
            {"invoice_no": i.erp_invoice_no, "msg": i.mirror_error}
            for i in invs
            if i.ledger_status == "DRAFT" and i.mirror_error
        ]
        buyers = []
        for b in s.scalars(
            select(Buyer).where(Buyer.id.in_(buyer_ids or [-1])).order_by(Buyer.name)
        ):
            buyers.append(
                {
                    "id": b.id,
                    "name": b.name,
                    "statement_url": f"{settings.app_base_url or ''}/s/{b.statement_token}",
                    "paypal_setup": mirror.buyer_setup_state(s, b.id),
                }
            )
        return {
            "mirrored": mirrored,
            "mirror_errors": errors,
            "setting_up": len(invs) - mirrored - len(errors),
            "buyers": buyers,
        }


async def run_import(
    session_factory: Callable[[], Session],
    pp: PayPal,
    settings: Settings,
    text: str,
    *,
    wait_s: float | None = None,
) -> dict[str, Any]:
    res, drafts = await run_in_threadpool(_import, session_factory, text)
    out: dict[str, Any] = {"created": res.created, "updated": res.updated, "errors": res.errors}
    if not res.ok:
        return out | {"mirrored": 0, "mirror_errors": [], "setting_up": 0, "buyers": []}
    if drafts:

        def owners() -> list[int]:
            with session_factory() as s:
                return sorted(
                    set(s.scalars(select(Invoice.buyer_id).where(Invoice.id.in_(drafts))))
                )

        tasks = [
            mirror.start_setup(b, pp, settings, session_factory)
            for b in await run_in_threadpool(owners)
        ]
        # the rest keeps running in the background; the sweep catches up after that
        await asyncio.wait(tasks, timeout=MIRROR_WAIT_S if wait_s is None else wait_s)
    report = await run_in_threadpool(_report, session_factory, res.buyer_ids, drafts, settings)
    return out | report
