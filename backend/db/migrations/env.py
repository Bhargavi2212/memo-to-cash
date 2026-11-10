"""Alembic environment: same Settings as the app, but the direct (unpooled) Neon URL."""

from alembic import context
from sqlalchemy import create_engine, pool

from backend.core.config import settings, sqlalchemy_url
from backend.db.session import connect_args

target_metadata = None  # models arrive in P1 (backend/db/models.py)


def _url() -> str:
    if settings.database_url_unpooled is None:
        raise RuntimeError("DATABASE_URL_UNPOOLED is not set (migrations need the direct URL)")
    return sqlalchemy_url(settings.database_url_unpooled.get_secret_value())


def run_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_online() -> None:
    url = _url()
    engine = create_engine(url, poolclass=pool.NullPool, connect_args=connect_args(url))
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_offline()
else:
    run_online()
