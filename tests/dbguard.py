"""Pick the integration-test database URL and refuse anything that looks like dev/production.

P1 D10: TEST_DATABASE_URL comes from the shell env, else that single key from `.env`.
It must never point at the same database as `.env`'s DATABASE_URL / DATABASE_URL_UNPOOLED
(Neon pooled and direct hosts differ only by `-pooler`, so hosts are compared without it).
"""

from collections.abc import Mapping
from urllib.parse import urlsplit


class UnsafeTestDatabaseError(RuntimeError):
    pass


def _target(url: str) -> tuple[str, str]:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower().replace("-pooler", "")
    return host, parts.path.lstrip("/")


def pick_test_db_url(environ: Mapping[str, str], dotenv: Mapping[str, str | None]) -> str | None:
    url = (environ.get("TEST_DATABASE_URL") or dotenv.get("TEST_DATABASE_URL") or "").strip()
    if not url:
        return None
    for key in ("DATABASE_URL", "DATABASE_URL_UNPOOLED"):
        other = (dotenv.get(key) or "").strip()
        if other and _target(other) == _target(url):
            raise UnsafeTestDatabaseError(
                f"TEST_DATABASE_URL points at the same database as .env {key} — "
                "use the Neon `test` branch (tests drop and recreate its schema)"
            )
    return url
