"""One-shot, NON-DESTRUCTIVE database initialization.

    python -m pulseops_api.init_db            # migrate to head; seed only if empty
    python -m pulseops_api.init_db --reseed   # explicit destructive reset + seed

This is what containers run at startup: it never drops data on its own.
(The old behavior — drop + reseed on every start — destroyed persistent state;
use --reseed or `python -m pulseops_api.seed.seed_db` when you WANT a reset.)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import select, text

from .db import Base, SessionLocal, engine
from .models import Asset
from .seed.seed_db import seed

API_DIR = Path(__file__).resolve().parent.parent


def migrate_to_head() -> None:
    cfg = Config(str(API_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_DIR / "alembic"))
    command.upgrade(cfg, "head")


def clear_schema_for_reseed(db_engine=engine) -> None:
    """Destructively clear app tables *and* Alembic's revision marker.

    ``Base.metadata.drop_all`` does not know about ``alembic_version``.  If
    that marker is left at ``head``, the subsequent upgrade is a no-op and
    startup fails on the first missing application table.  Reseed is already
    an explicit destructive operation, so clearing the stale marker is part
    of the same contract and lets Alembic rebuild from the initial revision.
    """
    Base.metadata.drop_all(db_engine)
    with db_engine.begin() as connection:
        connection.execute(text("DROP TABLE IF EXISTS alembic_version"))


def reset_and_seed() -> dict:
    """Shared deterministic reset path used by CLI and the local demo API."""
    clear_schema_for_reseed()
    migrate_to_head()
    session = SessionLocal()
    try:
        return seed(session)
    finally:
        session.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--reseed",
        action="store_true",
        help="DESTRUCTIVE: drop all tables, re-migrate, and seed fresh data",
    )
    args = parser.parse_args(argv)

    if args.reseed:
        print(f"[init_db] RESEED requested — dropping all tables at {engine.url} ...")
        ids = reset_and_seed()
        print(f"[init_db] seeded fresh demo data: {ids}")
        return 0

    print(f"[init_db] migrating {engine.url} to head ...")
    migrate_to_head()

    session = SessionLocal()
    try:
        already_seeded = session.scalars(select(Asset).limit(1)).first() is not None
        if already_seeded and not args.reseed:
            print(
                "[init_db] database already contains data — leaving it untouched "
                "(run with --reseed to reset)."
            )
            return 0
        ids = seed(session)
        print(f"[init_db] seeded fresh demo data: {ids}")
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    sys.exit(main())
