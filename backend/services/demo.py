"""Demo data and "Start the demo" (FR-12; P1 D3, D8, D9, D16–D18).

`build_csv(D, buyer_email)` writes the §Seed dataset (IMPLEMENTATION_PLAN) as an import
file with every date relative to D (invariant 8), so a demo copy goes through the same
importer as a real ERP export.
"""

import asyncio
import csv
import hashlib
import io
import logging
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from itsdangerous import BadSignature, URLSafeTimedSerializer
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from backend.core.config import Settings
from backend.core.dates import offset, today_ny
from backend.db.models import Buyer, DemoRate, Invoice, Memo, MemoLine, Message, Proposal
from backend.paypal.client import PayPal, PayPalNotConfiguredError
from backend.services import audit
from backend.services.importer import import_csv
from backend.services.mirror import mirror_invoices

COLUMNS = (
    "record_type",
    "buyer_name",
    "buyer_email",
    "memo_no",
    "sent_on",
    "due_back_on",
    "sku",
    "description",
    "price",
    "returned_on",
    "return_ref",
    "invoice_no",
    "issued_on",
    "due_on",
)

BUYER_NAME = "Northside Jewelers"

# memo_no, sent (days from D), [(sku, description, price $, state)]
# state: ("KEPT", invoice_no) | ("RETURNED", days, ref) | ("OUT", due back days)
_MEMOS = [
    (
        "M-201",
        -75,
        [
            ("S-101", "Round 1.01ct", "2350", ("KEPT", "INV-1038")),
            ("S-102", "Oval 1.20ct", "1200", ("RETURNED", -38, "R-311")),
            ("S-103", "Pendant", "800", ("OUT", 11)),
            ("S-104", "Stud pair", "1150", ("KEPT", "INV-1038")),
        ],
    ),
    (
        "M-207",
        -45,
        [
            ("S-110", "Cushion 0.90ct", "1900", ("KEPT", "INV-1042")),
            ("S-111", "Tennis bracelet", "1600", ("KEPT", "INV-1042")),
            ("S-112", "Emerald 1.50ct", "2100", ("RETURNED", -21, "R-318")),
            ("S-113", "Halo ring", "950", ("RETURNED", -21, "R-318")),
        ],
    ),
    (
        "M-212",
        -20,
        [
            ("S-120", "Solitaire ring", "1420", ("KEPT", "INV-1051")),
            ("S-121", "Hoops", "640", ("RETURNED", -6, "R-325")),
            ("S-122", "Pear 0.70ct", "1300", ("RETURNED", -6, "R-325")),
            ("S-123", "Bangle", "780", ("OUT", 25)),
        ],
    ),
]
# invoice_no, memo_no, issued (days), due (days)
_INVOICES = [
    ("INV-1038", "M-201", -48, -18),
    ("INV-1042", "M-207", -20, 10),
    ("INV-1051", "M-212", -5, 25),
]


def build_csv(d: date, buyer_email: str) -> str:
    """The §Seed dataset as import CSV, dates relative to D."""
    out = io.StringIO()
    w = csv.DictWriter(out, fieldnames=COLUMNS, lineterminator="\n")
    w.writeheader()
    w.writerow({"record_type": "BUYER", "buyer_name": BUYER_NAME, "buyer_email": buyer_email})
    for memo_no, sent, lines in _MEMOS:
        due_back = [s[1] for *_, s in lines if s[0] == "OUT"]
        w.writerow(
            {
                "record_type": "MEMO",
                "memo_no": memo_no,
                "buyer_email": buyer_email,
                "sent_on": offset(d, sent).isoformat(),
                "due_back_on": offset(d, max(due_back)).isoformat() if due_back else "",
            }
        )
        for sku, desc, price, _ in lines:
            w.writerow(
                {
                    "record_type": "LINE",
                    "memo_no": memo_no,
                    "sku": sku,
                    "description": desc,
                    "price": price,
                }
            )
        for sku, _, _, state in lines:
            if state[0] == "RETURNED":
                w.writerow(
                    {
                        "record_type": "RETURN",
                        "memo_no": memo_no,
                        "sku": sku,
                        "returned_on": offset(d, state[1]).isoformat(),
                        "return_ref": state[2],
                    }
                )
    for inv_no, memo_no, issued, due in _INVOICES:
        w.writerow(
            {
                "record_type": "INVOICE",
                "invoice_no": inv_no,
                "memo_no": memo_no,
                "issued_on": offset(d, issued).isoformat(),
                "due_on": offset(d, due).isoformat(),
            }
        )
        for m_no, _, lines in _MEMOS:
            for sku, *_, state in lines:
                if m_no == memo_no and state == ("KEPT", inv_no):
                    w.writerow(
                        {
                            "record_type": "INVOICE_LINE",
                            "invoice_no": inv_no,
                            "memo_no": memo_no,
                            "sku": sku,
                        }
                    )
    return out.getvalue()


# ---------------------------------------------------------------------------
# Demo start (P1 step 9: D3, D8, D9, D16–D18; review R2–R4; plan G6, G23)
# ---------------------------------------------------------------------------

REQUIRED_SETTINGS = (
    ("PAYPAL_CLIENT_ID", "paypal_client_id"),
    ("PAYPAL_CLIENT_SECRET", "paypal_client_secret"),
    ("PAYPAL_BUSINESS_EMAIL", "paypal_business_email"),
    ("SANDBOX_BUYER_EMAIL", "sandbox_buyer_email"),
    ("DEMO_COOKIE_SECRET", "demo_cookie_secret"),
    ("IP_HASH_SECRET", "ip_hash_secret"),
    ("APP_BASE_URL", "app_base_url"),
)
PER_IP_PER_HOUR = 15
GLOBAL_PER_HOUR = 60
WARN_AT = int(GLOBAL_PER_HOUR * 0.8)  # 48
DEADLINE_S = 20.0
COOKIE_NAME = "mtc_demo"
COOKIE_MAX_AGE = 7 * 24 * 3600
_COOKIE_SALT = "mtc-demo-v1"

SEED_MESSAGE = "There's a small scratch on the S-120 solitaire, please take $20 off."
SEED_DEDUCTION_CENTS = -2000
SEED_ACTION = f"ADJUSTMENT:INV-1051:{SEED_DEDUCTION_CENTS}"  # the only agent result used (D13)

log = logging.getLogger("demo")


class DemoError(Exception):
    """Mapped by the route to {error: {code, message, paypal_debug_id?}} (BACKEND_SCHEMA §2)."""

    def __init__(self, status: int, code: str, message: str, debug_id: str | None = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.debug_id = debug_id


@dataclass
class DemoStart:
    statement_token: str
    buyer_id: int
    ms: int
    cookie: str
    demo_session: str


def missing_settings(settings: Settings) -> list[str]:
    return [env for env, attr in REQUIRED_SETTINGS if not getattr(settings, attr)]


def client_ip(x_forwarded_for: str | None, socket_host: str | None) -> str:
    """First address in X-Forwarded-For (Render's edge), else the socket (D17)."""
    if x_forwarded_for:
        first = x_forwarded_for.split(",")[0].strip()
        if first:
            return first
    return socket_host or "unknown"


def ip_hash(ip: str, settings: Settings) -> str:
    assert settings.ip_hash_secret
    secret = settings.ip_hash_secret.get_secret_value()
    return hashlib.sha256(f"{ip}{secret}".encode()).hexdigest()


def hour_bucket(now: datetime) -> datetime:
    """Rate windows are whole hours in UTC (D17)."""
    return now.astimezone(UTC).replace(minute=0, second=0, microsecond=0)


def _serializer(settings: Settings) -> URLSafeTimedSerializer:
    assert settings.demo_cookie_secret
    return URLSafeTimedSerializer(settings.demo_cookie_secret.get_secret_value(), salt=_COOKIE_SALT)


def make_demo_cookie(demo_session: str, settings: Settings) -> str:
    return _serializer(settings).dumps({"s": demo_session})


def read_demo_cookie(value: str | None, settings: Settings) -> str | None:
    """The demo session in a signed cookie; tampered, expired or missing → None (D8)."""
    if not value or not settings.demo_cookie_secret:
        return None
    try:
        data = _serializer(settings).loads(value, max_age=COOKIE_MAX_AGE)
    except BadSignature:  # includes SignatureExpired
        return None
    return data.get("s") if isinstance(data, dict) else None


def _rate_counts(session: Session, ip_key: str, bucket: datetime) -> tuple[int, int]:
    rows = dict(
        session.execute(
            select(DemoRate.ip_hash, DemoRate.count).where(
                DemoRate.window_start == bucket, DemoRate.ip_hash.in_((ip_key, "global"))
            )
        ).all()
    )
    return rows.get(ip_key, 0), rows.get("global", 0)


def _bump(session: Session, key: str, bucket: datetime) -> int:
    """Atomic upsert; returns the new count (D17)."""
    stmt = (
        pg_insert(DemoRate)
        .values(ip_hash=key, window_start=bucket, count=1)
        .on_conflict_do_update(
            index_elements=[DemoRate.ip_hash, DemoRate.window_start],
            set_={"count": DemoRate.count + 1},
        )
        .returning(DemoRate.count)
    )
    return int(session.execute(stmt).scalar_one())


def check_rate(session_factory: Callable[[], Session], ip_key: str, now: datetime) -> None:
    with session_factory() as s:
        mine, total = _rate_counts(s, ip_key, hour_bucket(now))
    if mine >= PER_IP_PER_HOUR:
        raise DemoError(429, "RATE_LIMITED", "Too many demos from your network — try again later.")
    if total >= GLOBAL_PER_HOUR:
        raise DemoError(429, "DEMO_HOURLY_LIMIT", "The demo is busy this hour — try again later.")


def _create_copy(
    session_factory: Callable[[], Session], settings: Settings, demo_session: str, today: date
) -> tuple[int, str, Any, bool]:
    """Importer → the seed *message only*, one transaction (P4 D13). Returns (buyer id, token,
    the buyer's Facts for the triage, whether the global LLM cap allows a call)."""
    from backend.agent import caps
    from backend.agent.facts import facts_for_buyer

    assert settings.sandbox_buyer_email
    with session_factory() as s, s.begin():
        res = import_csv(
            s, build_csv(today, settings.sandbox_buyer_email), demo_session=demo_session
        )
        if not res.ok:  # the seed is ours: this is a bug, not a user error
            raise RuntimeError(f"seed import failed: {res.errors}")
        buyer = s.get(Buyer, res.buyer_ids[0])
        assert buyer is not None
        s.add(Message(buyer_id=buyer.id, text=SEED_MESSAGE, kind="MESSAGE",
                      ai_result={"seeded": True}))  # fmt: skip
        allowed = caps.take_unit(s, today, None)  # demo start counts toward global only (D9)
        return buyer.id, buyer.statement_token, facts_for_buyer(s, buyer.id, today), allowed


def _seed_proposal(s: Session, buyer_id: int, msg: Message) -> Proposal:
    """The P1 deterministic proposal — the fallback when the agent's triage isn't the
    expected one (D13)."""
    inv = s.scalars(
        select(Invoice).where(Invoice.buyer_id == buyer_id, Invoice.erp_invoice_no == "INV-1051")
    ).one()
    line = s.scalars(
        select(MemoLine)
        .join(Memo, MemoLine.memo_id == Memo.id)
        .where(Memo.buyer_id == buyer_id, MemoLine.sku == "S-120")
    ).one()
    return Proposal(
        buyer_id=buyer_id,
        kind="ADJUSTMENT",
        payload={
            "invoice_id": inv.id,
            "amount_cents": SEED_DEDUCTION_CENTS,
            "reason": "Scratch on the S-120 solitaire (buyer message)",
        },
        evidence={
            "source": "seed",
            "checks": [
                "S-120 is KEPT on INV-1051",
                f"deduction 2000 ≤ line price {line.price_cents}",
                f"deduction 2000 ≤ invoice due {inv.total_cents - inv.paid_cents}",
            ],
            "records": ["memo_line:S-120", "invoice:INV-1051", "message:seed"],
        },
        source_message_id=msg.id,
    )


def _finish(
    session_factory: Callable[[], Session],
    buyer_id: int,
    ip_key: str,
    now: datetime,
    tri: Any,
) -> str:
    """One transaction: exactly one proposal (the agent's, or the seed one), the copy marked
    reachable, the start counted — only on success (D9, D13, D17, R2). Returns its source."""
    from backend.services.agent import proposal_for

    bucket = hour_bucket(now)
    with session_factory() as s, s.begin():
        s.execute(text("SELECT pg_advisory_xact_lock(:id)"), {"id": buyer_id})
        buyer = s.get(Buyer, buyer_id)
        assert buyer is not None
        msg = s.scalars(
            select(Message).where(Message.buyer_id == buyer_id).order_by(Message.id)
        ).first()
        assert msg is not None
        msg.ai_result = {"seeded": True, "triage": tri.ai_result()}
        if tri.action is not None and tri.action.code == SEED_ACTION:
            prop = proposal_for(s, buyer_id, tri.action, msg, tri.label)
            source, actor, action = "agent", "agent", "proposal.created"
        else:
            prop = _seed_proposal(s, buyer_id, msg)
            source, actor, action = "seed", "system", "proposal.seeded"
        s.add(prop)
        s.flush()
        audit.log(s, actor, action, ref="INV-1051", detail={"buyer_id": buyer_id,
                  "proposal_id": prop.id, "triage": tri.action.code if tri.action else
                  tri.fallback_reason})  # fmt: skip
        buyer.demo_ready = True
        _bump(s, ip_key, bucket)
        total = _bump(s, "global", bucket)
        audit.log(
            s, "system", "demo.started", ref=buyer.demo_session, detail={"buyer_id": buyer_id}
        )
    if total >= WARN_AT:
        log.warning("demo starts this hour: %d of %d", total, GLOBAL_PER_HOUR)
    if source == "seed":
        log.info("demo start used the seed proposal (triage %s)",
                 tri.action.code if tri.action else tri.fallback_reason)  # fmt: skip
    return source


async def _triage(facts: Any, allowed: bool, client: Any) -> Any:
    from backend.agent import llm
    from backend.agent.triage import TriageOutcome, run_triage

    if not allowed:
        return TriageOutcome(None, {}, None, "cap")
    return await run_triage(facts, SEED_MESSAGE, llm.Budget(), client=client)


async def start_demo(
    settings: Settings,
    pp: PayPal,
    session_factory: Callable[[], Session],
    *,
    ip: str,
    now: datetime | None = None,
    client: Any = None,
) -> DemoStart:
    """A fresh, private demo copy: seed → PayPal invoices → ready (FR-12).

    Order (D17): settings → rate check (read) → work → count only on success. Any failure
    leaves the copy unreachable (`demo_ready=false`) and sets no cookie (D9)."""
    started = time.perf_counter()
    missing = missing_settings(settings)
    if missing:
        raise DemoError(503, "NOT_CONFIGURED", "Demo isn't configured: " + ", ".join(missing))
    when = now or datetime.now(UTC)
    ip_key = ip_hash(ip, settings)
    await run_in_threadpool(check_rate, session_factory, ip_key, when)

    demo_session = secrets.token_urlsafe(16)
    buyer_id, token, facts, allowed = await run_in_threadpool(
        _create_copy, session_factory, settings, demo_session, today_ny(when)
    )
    # D13: the seed message goes through the real triage while PayPal mirrors the invoices
    triage_task = asyncio.create_task(_triage(facts, allowed, client))
    try:
        result = await asyncio.wait_for(
            mirror_invoices(buyer_id, pp, settings, session_factory, today=today_ny(when)),
            timeout=DEADLINE_S,
        )
    except TimeoutError as e:
        await _drop(triage_task)
        raise DemoError(503, "PAYPAL_UNAVAILABLE", UNAVAILABLE_MSG) from e
    except PayPalNotConfiguredError as e:
        await _drop(triage_task)
        raise DemoError(
            503, "NOT_CONFIGURED", "Demo isn't configured: " + ", ".join(e.missing)
        ) from e
    except BaseException:
        await _drop(triage_task)
        raise
    if not result.ok:
        await _drop(triage_task)  # mirror failed: the triage result is discarded (D13)
        if result.transient:
            raise DemoError(503, "PAYPAL_UNAVAILABLE", UNAVAILABLE_MSG)
        _, err = result.errors[0]
        log.error("demo start rejected by PayPal: %s debug_id=%s", err.name, err.debug_id)
        raise DemoError(
            502,
            "PAYPAL_REJECTED",
            "Demo setup failed on our side — we've logged it.",
            debug_id=err.debug_id,
        )
    tri = await triage_task
    await run_in_threadpool(_finish, session_factory, buyer_id, ip_key, when, tri)
    return DemoStart(
        statement_token=token,
        buyer_id=buyer_id,
        ms=int((time.perf_counter() - started) * 1000),
        cookie=make_demo_cookie(demo_session, settings),
        demo_session=demo_session,
    )


async def _drop(task: "asyncio.Task[Any]") -> None:
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


UNAVAILABLE_MSG = "PayPal's sandbox is slow right now — please try again in a minute."
