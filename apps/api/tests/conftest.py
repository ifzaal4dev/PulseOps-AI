"""Shared fixtures: an isolated database seeded with the synthetic facility,
independent of the app's configured database.

Default: in-memory SQLite (fast). Set PULSEOPS_TEST_DATABASE_URL to a
PostgreSQL URL to run the identical suite against real Postgres, e.g.:

    PULSEOPS_TEST_DATABASE_URL=postgresql+psycopg://pulseops:pulseops@localhost:5433/pulseops \
        python -m pytest tests/ -v
"""

import os

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from pulseops_api.db import Base
from pulseops_api.seed.seed_db import seed
from pulseops_api.services import environment

TEST_DB_URL = os.environ.get("PULSEOPS_TEST_DATABASE_URL", "sqlite://")


@pytest.fixture(autouse=True)
def _clean_environment_overrides():
    """Outcome-profile overrides are in-process; never leak between tests."""
    environment.clear_overrides()
    yield
    environment.clear_overrides()


@pytest.fixture()
def session():
    if TEST_DB_URL.startswith("sqlite"):
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
    else:
        engine = create_engine(TEST_DB_URL)
        if TEST_DB_URL.startswith("postgresql"):

            @event.listens_for(engine, "connect")
            def _utc_session(dbapi_conn, _record):
                cur = dbapi_conn.cursor()
                cur.execute("SET TIME ZONE 'UTC'")
                cur.close()

        Base.metadata.drop_all(engine)  # isolated per-test schema on a shared server
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    s = TestSession()
    try:
        yield s
    finally:
        s.close()
        if not TEST_DB_URL.startswith("sqlite"):
            Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture()
def seeded(session):
    """Seeded session; returns (session, ids) with chiller3_id / pump7_id / incident_id."""
    ids = seed(session)
    return session, ids
