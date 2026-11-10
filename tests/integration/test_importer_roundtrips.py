"""R6: the importer must not do a query per row (demo start latency)."""

from sqlalchemy import event

from backend.core.dates import today_ny
from backend.db.session import engine
from backend.services.demo import build_csv
from backend.services.importer import import_csv


def _count_statements(fn):
    n = [0]

    def before(*_a, **_k):
        n[0] += 1

    event.listen(engine, "before_cursor_execute", before)
    try:
        fn()
    finally:
        event.remove(engine, "before_cursor_execute", before)
    return n[0]


def test_demo_copy_uses_few_statements(db):
    text = build_csv(today_ny(), "buyer@example.com")
    n = _count_statements(lambda: import_csv(db, text, demo_session="s1"))
    assert n <= 8, n


def test_real_reimport_uses_few_statements(db):
    text = build_csv(today_ny(), "buyer@example.com")
    import_csv(db, text)
    n = _count_statements(lambda: import_csv(db, text))
    assert n <= 10, n
