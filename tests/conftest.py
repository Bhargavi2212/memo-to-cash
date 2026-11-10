"""Test setup, run before any app module is imported.

APP_ENV=test makes settings skip the local .env, so real secrets never reach tests.
Integration tests that need Postgres use the `db` fixture (marker `db`, P1 D10–D11):
- TEST_DATABASE_URL from the shell env, else that one key from `.env` (Neon `test` branch,
  direct URL; refused if it points at `.env`'s DATABASE_URL/DATABASE_URL_UNPOOLED).
- First `db` test of the run: DROP + CREATE schema `public`, `alembic upgrade head`.
- After each `db` test: roll back + close the session, dispose the engine, TRUNCATE every
  table except alembic_version.
- No TEST_DATABASE_URL → `db` tests skip, unless REQUIRE_TEST_DB=1 (CI), then they fail.
"""

import os
from pathlib import Path

import pytest
from dotenv import dotenv_values

from tests.dbguard import pick_test_db_url

ROOT = Path(__file__).resolve().parent.parent
_dotenv = dotenv_values(ROOT / ".env") if (ROOT / ".env").exists() else {}
TEST_DB_URL = pick_test_db_url(os.environ, _dotenv)
del _dotenv  # nothing else from .env reaches tests

os.environ["APP_ENV"] = "test"
os.environ.setdefault("PAYPAL_BASE_URL", "https://api-m.sandbox.paypal.com")
if TEST_DB_URL:
    os.environ["DATABASE_URL"] = TEST_DB_URL
    os.environ["DATABASE_URL_UNPOOLED"] = TEST_DB_URL
else:
    os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/test")


def pytest_collection_modifyitems(items):
    for item in items:
        if "db" in getattr(item, "fixturenames", ()):
            item.add_marker(pytest.mark.db)


@pytest.fixture
def anyio_backend():
    return "asyncio"  # don't also run async tests under trio


@pytest.fixture(scope="session")
def _test_schema():
    """Fresh schema at head, once per run, only when a `db` test runs."""
    if not TEST_DB_URL:
        if os.environ.get("REQUIRE_TEST_DB") == "1":
            pytest.fail("REQUIRE_TEST_DB=1 but TEST_DATABASE_URL is not set")
        pytest.skip("TEST_DATABASE_URL not set (integration test needs Postgres)")

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text

    from backend.db.session import engine

    with engine.connect() as conn:
        conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
        conn.commit()
    engine.dispose()
    command.upgrade(Config(str(ROOT / "alembic.ini")), "head")
    yield


@pytest.fixture
def db(_test_schema):
    """A session on the test database; every table is emptied afterwards."""
    from sqlalchemy import text

    from backend.db.session import SessionLocal, engine

    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
        engine.dispose()
        with engine.connect() as conn:
            tables = conn.execute(
                text(
                    "SELECT tablename FROM pg_tables "
                    "WHERE schemaname = 'public' AND tablename <> 'alembic_version'"
                )
            ).scalars()
            names = ", ".join(f'"{t}"' for t in tables)
            if names:
                conn.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))
                conn.commit()
