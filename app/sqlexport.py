"""Write the database schema as plain SQL: python -m app.sqlexport

The schema is defined by the Alembic migrations. This turns them into one readable SQL file
(sql/schema.sql) that can be read, reviewed, or applied with psql to an empty database.
A test fails if the committed file no longer matches the migrations.
"""

from io import StringIO
from pathlib import Path

from alembic import command
from alembic.config import Config

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_FILE = ROOT / "sql" / "schema.sql"

HEADER = """\
-- Court Booking Service: database schema
--
-- GENERATED from the Alembic migrations in migrations/versions/. Do not edit by hand.
-- Regenerate after adding a migration:  python -m app.sqlexport
-- Apply to an EMPTY database:           psql -U club -d court_booking -f sql/schema.sql
--
-- A database built from this file is the same as one built with `alembic upgrade head`,
-- and it records the migration version, so Alembic can carry on from it.
-- The important rule is at the end: ex_bookings_no_overlap, which makes double-booking
-- impossible.

"""


def build_schema_sql() -> str:
    """The SQL that `alembic upgrade head` would run, without connecting to any database."""
    buffer = StringIO()
    config = Config(str(ROOT / "alembic.ini"), output_buffer=buffer)
    command.upgrade(config, "head", sql=True)
    # Alembic writes "... RETURNING version_num" after its bookkeeping insert, which would make
    # psql print a stray result table. Nothing needs it, so leave it out.
    body = buffer.getvalue().replace(" RETURNING alembic_version.version_num", "")
    return HEADER + body


def main() -> None:
    SCHEMA_FILE.parent.mkdir(exist_ok=True)
    SCHEMA_FILE.write_text(build_schema_sql())
    print(f"wrote {SCHEMA_FILE.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
