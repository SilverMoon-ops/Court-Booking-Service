"""The plain SQL files are real: they build the same database as the migrations.

A reviewer can read sql/schema.sql and sql/seed.sql, run them with psql, and get a working
system. These tests keep that true: they fail if a migration is added and sql/schema.sql is not
regenerated, or if the seed data stops working.
"""

import os
from collections.abc import Iterator

import psycopg
import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, delete, select, text
from sqlalchemy.engine import make_url

from app import seed
from app.db import SessionLocal
from app.models import Court, Member, PeakWindow, Venue
from app.sqlexport import ROOT, SCHEMA_FILE, build_schema_sql

SEED_FILE = ROOT / "sql" / "seed.sql"
INSPECT_FILE = ROOT / "sql" / "inspect.sql"


def conninfo(database: str) -> str:
    url = make_url(os.environ["DATABASE_URL"]).set(database=database)
    return url.render_as_string(hide_password=False).replace(
        "postgresql+psycopg://", "postgresql://"
    )


@pytest.fixture
def sql_built_db(migrated_db: None) -> Iterator[str]:
    """A brand-new database built ONLY from sql/schema.sql, deleted afterwards."""
    base = make_url(os.environ["DATABASE_URL"]).database or ""
    name = f"{base}_sqlfiles_test"  # the name must end in _test
    admin = create_engine(
        make_url(os.environ["DATABASE_URL"]).set(database="postgres"), isolation_level="AUTOCOMMIT"
    )
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    with psycopg.connect(conninfo(name), autocommit=True) as conn:
        conn.execute(SCHEMA_FILE.read_text())
    yield name
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    admin.dispose()


def run(database: str, script: str) -> list[tuple[object, ...]]:
    """Run a script and return the rows of its last statement that returned any."""
    with psycopg.connect(conninfo(database), autocommit=True) as conn:
        cursor = conn.execute(script)
        rows: list[tuple[object, ...]] = []
        while True:
            if cursor.description is not None:
                rows = cursor.fetchall()
            if not cursor.nextset():
                return rows


def snapshot(database: str) -> dict[str, list[tuple[object, ...]]]:
    """Everything that defines the shape of a database: columns, rules and indexes."""
    queries = {
        "columns": """
            SELECT table_name, column_name, data_type, is_nullable, column_default
            FROM information_schema.columns WHERE table_schema = 'public'
            ORDER BY table_name, ordinal_position""",
        "rules": """
            SELECT conrelid::regclass::text, conname, pg_get_constraintdef(oid)
            FROM pg_constraint WHERE connamespace = 'public'::regnamespace
            ORDER BY 1, 2""",
        "indexes": "SELECT indexdef FROM pg_indexes WHERE schemaname = 'public' ORDER BY indexname",
    }
    return {name: run(database, query) for name, query in queries.items()}


def current_database() -> str:
    return make_url(os.environ["DATABASE_URL"]).database or ""


# ------------------------------------------------------------- schema.sql


def test_schema_sql_is_up_to_date_with_the_migrations() -> None:
    assert (
        SCHEMA_FILE.read_text() == build_schema_sql()
    ), "sql/schema.sql is out of date. Regenerate it with: python -m app.sqlexport"


def test_a_database_built_from_schema_sql_matches_one_built_by_the_migrations(
    sql_built_db: str,
) -> None:
    assert snapshot(sql_built_db) == snapshot(current_database())


def test_alembic_can_carry_on_from_a_database_built_from_sql(sql_built_db: str) -> None:
    head = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini"))).get_current_head()
    assert run(sql_built_db, "SELECT version_num FROM alembic_version") == [(head,)]


def test_the_double_booking_rule_works_on_a_database_built_from_sql(sql_built_db: str) -> None:
    run(sql_built_db, SEED_FILE.read_text())
    book = """
        INSERT INTO bookings (member_id, court_id, start_at, end_at, status, price)
        SELECT m.id, c.id, '{start}', '{end}', 'confirmed', 1
        FROM members m, courts c WHERE m.name = 'Asha' AND c.name = 'Court 1'"""
    run(sql_built_db, book.format(start="2031-01-06 05:00+00", end="2031-01-06 06:00+00"))
    with pytest.raises(psycopg.errors.ExclusionViolation, match="ex_bookings_no_overlap"):
        run(sql_built_db, book.format(start="2031-01-06 05:30+00", end="2031-01-06 06:30+00"))


# --------------------------------------------------------------- seed.sql


def test_seed_sql_creates_the_demo_data_and_never_duplicates_it(sql_built_db: str) -> None:
    for _ in range(3):  # running it again and again must change nothing
        ids = run(sql_built_db, SEED_FILE.read_text())
    assert [(kind, name) for kind, name, _ in ids] == [
        ("court", "Court 1"),
        ("court", "Court 2"),
        ("member", "Asha"),
        ("member", "Ben"),
    ]
    counts = run(
        sql_built_db,
        """SELECT (SELECT count(*) FROM venues), (SELECT count(*) FROM courts),
                  (SELECT count(*) FROM members), (SELECT count(*) FROM peak_windows)""",
    )
    assert counts == [(1, 2, 2, 5)]  # Monday to Friday evenings are peak


def test_the_seed_gives_weekday_evenings_peak_pricing(sql_built_db: str) -> None:
    run(sql_built_db, SEED_FILE.read_text())
    windows = run(sql_built_db, "SELECT weekday, start_hour, end_hour FROM peak_windows ORDER BY 1")
    assert windows == [(day, 18, 22) for day in range(5)]


# ------------------------------------------------------------- inspect.sql


def test_every_query_in_inspect_sql_runs(sql_built_db: str) -> None:
    run(sql_built_db, SEED_FILE.read_text())
    queries = "\n".join(
        line for line in INSPECT_FILE.read_text().splitlines() if not line.startswith("\\")
    )  # psql meta-commands such as \echo are for psql, not for the database
    rows = run(sql_built_db, queries)
    assert rows == []  # the last query reports confirmed bookings, and there are none yet


# ------------------------------------------------------- the Python seed


@pytest.fixture
def clean_demo(migrated_db: None) -> Iterator[None]:
    yield
    with SessionLocal() as db:
        venue_ids = list(db.scalars(select(Venue.id).where(Venue.name == "Demo Venue")))
        db.execute(delete(PeakWindow).where(PeakWindow.venue_id.in_(venue_ids)))
        db.execute(delete(Court).where(Court.venue_id.in_(venue_ids)))
        db.execute(delete(Venue).where(Venue.id.in_(venue_ids)))
        db.execute(delete(Member).where(Member.name.in_(["Asha", "Ben"])))
        db.commit()


def test_the_python_seed_is_safe_to_run_twice(
    clean_demo: None, capsys: pytest.CaptureFixture[str]
) -> None:
    seed.main()
    first = capsys.readouterr().out
    seed.main()
    second = capsys.readouterr().out

    assert first == second  # the same four ids both times
    assert len(first.strip().splitlines()) == 4
    with SessionLocal() as db:
        assert len(list(db.scalars(select(Venue).where(Venue.name == "Demo Venue")))) == 1
        assert len(list(db.scalars(select(Member).where(Member.name.in_(["Asha", "Ben"]))))) == 2
