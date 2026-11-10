"""Token-safe logging (P2 D14).

Statement tokens are the buyer's key. Uvicorn's access log prints request paths, so a
filter rewrites `/api/statement/<token>` to `/api/statement/<redacted>` before anything is
written. App code logs `services.statement.token_ref(token)` (a short hash) instead.
"""

import logging
import re

_TOKEN_PATH = re.compile(r"(/api/statement/)[^/?\s\"]+")


def redact(text: str) -> str:
    return _TOKEN_PATH.sub(r"\1<redacted>", text)


class RedactTokenFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(redact(a) if isinstance(a, str) else a for a in record.args)
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)
        return True


def install() -> None:
    """Attach the filter to uvicorn's access logger (idempotent)."""
    access = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, RedactTokenFilter) for f in access.filters):
        access.addFilter(RedactTokenFilter())
