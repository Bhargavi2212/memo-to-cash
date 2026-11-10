"""Database engine and per-request session.

Render runs a long-lived server, so a small normal pool is fine. psycopg never uses
server-side prepared statements (prepare_threshold=None) because the app connects
through Neon's connection pooler.
"""

from collections.abc import Callable, Iterator
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session, sessionmaker

from backend.core.config import settings, sqlalchemy_url

LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")


def connect_args(url: str) -> dict[str, Any]:
    args: dict[str, Any] = {"prepare_threshold": None, "connect_timeout": 5}
    parsed = make_url(url)
    if "sslmode" not in parsed.query and parsed.host not in LOCAL_HOSTS:
        args["sslmode"] = "require"  # Neon needs TLS; local/CI Postgres doesn't have it
    return args


def make_engine(url: str) -> Engine:
    url = sqlalchemy_url(url)
    return create_engine(
        url,
        pool_size=5,
        max_overflow=5,
        pool_pre_ping=True,
        pool_recycle=300,
        connect_args=connect_args(url),
    )


engine = make_engine(settings.database_url.get_secret_value())  # no connection until first use
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_session() -> Iterator[Session]:
    """FastAPI dependency: one session per request, always closed."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def get_session_factory() -> "Callable[[], Session]":
    """FastAPI dependency for services that open their own transactions (PayPal calls happen
    between them, so one request-scoped session isn't enough)."""
    return SessionLocal
