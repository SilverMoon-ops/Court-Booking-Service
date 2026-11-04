import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.db import engine
from app.models import Booking, Court, Member, Venue

ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"
CONCURRENT_REQUESTS = 50


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


@dataclass
class CommittedCourt:
    """A venue, a court and many members that really exist in the database."""

    court_id: uuid.UUID
    member_ids: list[uuid.UUID]


@pytest.fixture
def committed_court(migrated_db: None) -> Iterator[CommittedCourt]:
    """Data that is actually committed, for tests that use many connections.

    Concurrency tests cannot use the rolled-back `session` fixture because other
    connections would not see its uncommitted rows. This fixture commits real rows
    and deletes them afterwards.
    """
    suffix = uuid.uuid4().hex[:8]
    with Session(engine) as db:
        venue = Venue(
            name=f"Test venue {suffix}",
            timezone="Asia/Kolkata",
            open_hour=6,
            close_hour=22,
            offpeak_rate=20000,
            peak_rate=40000,
        )
        db.add(venue)
        db.flush()
        court = Court(venue_id=venue.id, name="Court 1")
        members = [Member(name=f"Member {i}") for i in range(CONCURRENT_REQUESTS)]
        db.add(court)
        db.add_all(members)
        db.commit()
        data = CommittedCourt(court_id=court.id, member_ids=[m.id for m in members])
        venue_id = venue.id

    yield data

    with Session(engine) as db:
        db.execute(delete(Booking).where(Booking.court_id == data.court_id))
        db.execute(delete(Court).where(Court.id == data.court_id))
        db.execute(delete(Venue).where(Venue.id == venue_id))
        db.execute(delete(Member).where(Member.id.in_(data.member_ids)))
        db.commit()
