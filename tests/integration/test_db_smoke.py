"""Proves the integration database is wired: connected, migrated to head (P1 D10–D12).

In CI (REQUIRE_TEST_DB=1) this must show as passed, never skipped.
"""

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[2]


def test_connected_and_at_head(db):
    assert db.execute(text("SELECT 1")).scalar_one() == 1
    head = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini"))).get_current_head()
    assert db.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == head
