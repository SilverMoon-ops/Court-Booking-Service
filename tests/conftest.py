from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.orm import Session

from app.db import engine

ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"


@pytest.fixture(scope="session")
def migrated_db() -> None:
    """Bring the real Postgres schema up to date once per test run."""
    command.upgrade(Config(str(ALEMBIC_INI)), "head")


@pytest.fixture
def session(migrated_db: None) -> Iterator[Session]:
    """A session whose work is rolled back after each test.

    Even if the code under test calls commit(), it only releases a savepoint,
    so tests never leave data behind.
    """
    connection = engine.connect()
    outer = connection.begin()
    with Session(bind=connection, join_transaction_mode="create_savepoint") as db:
        yield db
    outer.rollback()
    connection.close()
