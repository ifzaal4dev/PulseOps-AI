"""Database engine/session setup (SQLAlchemy 2.0).

PostgreSQL is the container database (TIMESTAMPTZ, partial unique indexes, row locking for the
execution CAS guard); SQLite remains fully supported for fast unit tests and
zero-setup local runs. All schema changes go through Alembic (apps/api/alembic).
"""

from datetime import UTC

from sqlalchemy import DateTime, TypeDecorator, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    pass


class TZDateTime(TypeDecorator):
    """Timezone-aware UTC datetime, portable across SQLite and PostgreSQL.

    Convention: everything is UTC.
      * bind: aware datetimes are normalized to UTC and stored naive
        (PostgreSQL sessions are pinned to UTC — see _make_engine — so the
        TIMESTAMPTZ column stores the exact instant; SQLite stores a plain
        UTC string).
      * result: values always come back as aware UTC datetimes.
    Legacy naive inputs are treated as UTC by convention.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return value
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value, dialect):
        if value is None:
            return value
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


def _make_engine(url: str):
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    engine = create_engine(url, connect_args=connect_args)

    if url.startswith("sqlite"):
        # Enforce FK constraints in SQLite (off by default).
        @event.listens_for(engine, "connect")
        def _fk_pragma(dbapi_conn, _record):
            dbapi_conn.execute("PRAGMA foreign_keys=ON")

    elif url.startswith("postgresql"):
        # Pin sessions to UTC so naive-UTC binds into TIMESTAMPTZ are exact.
        @event.listens_for(engine, "connect")
        def _utc_session(dbapi_conn, _record):
            cur = dbapi_conn.cursor()
            cur.execute("SET TIME ZONE 'UTC'")
            cur.close()

    return engine


engine = _make_engine(settings.database_url)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_session():
    """FastAPI dependency: yields a request-scoped session."""
    session: Session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def create_all() -> None:
    """Create tables directly from the models. Convenient for SQLite tests and
    first-run local dev; containers/deployments use `alembic upgrade head`
    (see pulseops_api/init_db.py)."""
    from . import models  # noqa: F401  (register mappings)

    Base.metadata.create_all(engine)
