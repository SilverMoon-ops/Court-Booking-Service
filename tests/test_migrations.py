from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import text

from app.db import engine

ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"


def _has_btree_gist() -> bool:
    with engine.connect() as conn:
        found = conn.execute(
            text("SELECT 1 FROM pg_extension WHERE extname = 'btree_gist'")
        ).scalar()
    return found is not None


def test_migrations_apply_and_roll_back() -> None:
    cfg = Config(str(ALEMBIC_INI))

    command.downgrade(cfg, "base")
    assert not _has_btree_gist()

    command.upgrade(cfg, "head")
    assert _has_btree_gist()

    # Running upgrade again must be safe (nothing to do).
    command.upgrade(cfg, "head")
    assert _has_btree_gist()
