"""Destructive reset must clear Alembic state as well as mapped tables."""

from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from pulseops_api.db import Base
from pulseops_api.init_db import clear_schema_for_reseed
from pulseops_api.models import Asset
from pulseops_api.seed.seed_db import seed


def test_clear_schema_for_reseed_removes_stale_revision_and_allows_fresh_seed(session):
    engine = session.get_bind()
    session.close()
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
        connection.execute(
            text("INSERT INTO alembic_version (version_num) VALUES ('c4f18a2d71b0')")
        )

    before = set(inspect(engine).get_table_names())
    assert "assets" in before and "alembic_version" in before

    clear_schema_for_reseed(engine)

    after = set(inspect(engine).get_table_names())
    assert "assets" not in after
    assert "alembic_version" not in after

    # Alembic owns recreation in production. create_all is sufficient here to
    # prove the cleared state can be rebuilt and seeded in the isolated test DB.
    Base.metadata.create_all(engine)
    fresh = Session(engine)
    try:
        seed(fresh)
        assert fresh.query(Asset).count() > 0
    finally:
        fresh.close()
