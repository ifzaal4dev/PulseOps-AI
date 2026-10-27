"""Alembic migrations, run for real.

The rest of the suite builds its schema with ``Base.metadata.create_all``,
while every deployed database is created by Alembic. These tests make sure the
two agree and that each migration also works on a database that already holds
data.
"""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from pulseops_api import config as app_config
from pulseops_api.db import Base

API_DIR = Path(__file__).resolve().parents[1]


@pytest.fixture()
def migrate(tmp_path, monkeypatch):
    """Returns (engine, run) where run(command_name, revision) drives Alembic
    against a throwaway file-backed SQLite database."""
    url = f"sqlite:///{(tmp_path / 'migrations.db').as_posix()}"
    # alembic/env.py reads the URL from the settings object.
    monkeypatch.setattr(app_config.settings, "database_url", url)
    cfg = Config(str(API_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_DIR / "alembic"))
    engine = create_engine(url)

    def run(name: str, revision: str) -> None:
        getattr(command, name)(cfg, revision)

    yield engine, run
    engine.dispose()


def test_upgrade_head_matches_the_models(migrate):
    engine, run = migrate
    run("upgrade", "head")

    inspector = inspect(engine)
    tables = set(inspector.get_table_names()) - {"alembic_version"}
    assert tables == set(Base.metadata.tables)
    for name, table in Base.metadata.tables.items():
        db_columns = {column["name"] for column in inspector.get_columns(name)}
        assert {column.name for column in table.columns} <= db_columns, name


def test_downgrade_and_upgrade_round_trip(migrate):
    engine, run = migrate
    run("upgrade", "head")
    run("downgrade", "-1")
    run("upgrade", "head")
    assert "agent_runs" in inspect(engine).get_table_names()


def test_policy_columns_migrate_existing_predictions(migrate):
    """9f7332f169c7 adds NOT NULL columns to model_predictions; it has to work
    on a table that already holds rows."""
    engine, run = migrate
    run("upgrade", "4165c46cec25")
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO model_predictions (asset_id, window_end, predicted_at, risk, "
                "threshold, alert, shadow, model_source, feature_version, operating_mode, "
                "explanation_json) VALUES (1, '2020-06-05 11:00:00', '2020-06-05 11:00:01', "
                "0.5, 0.49, 1, 1, 'metropt3_iforest_v1', 'metropt3_fw_v1', 'loaded', '{}')"
            )
        )

    run("upgrade", "head")

    with engine.connect() as connection:
        mode, version = connection.execute(
            text("SELECT policy_mode, policy_version FROM model_predictions")
        ).one()
    assert (mode, version) == ("shadow", "")
