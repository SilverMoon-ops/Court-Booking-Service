"""Test setup that must run before any `app` module is imported.

The tests never touch your development database. On every run they drop and recreate a
throwaway database named `<your database>_test` and point the app at it. So leftover
data, a constraint dropped by hand, a half-applied migration, or a database that is
still starting up can no longer make tests fail for reasons that have nothing to do with
the code.

Set TEST_DATABASE_URL to use a specific database instead (its name must end in `_test`).
"""

import os
import time

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import OperationalError

from app.config import Settings


def _test_database_url() -> URL:
    explicit = os.environ.get("TEST_DATABASE_URL")
    if explicit:
        return make_url(explicit)
    dev = make_url(Settings().database_url)
    name = dev.database or "court_booking"
    return dev.set(database=name if name.endswith("_test") else f"{name}_test")


def _recreate(url: URL) -> None:
    name = url.database or ""
    if not name.endswith("_test"):
        raise RuntimeError(f"refusing to drop database {name!r}: its name must end in '_test'")

    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        deadline = time.monotonic() + 30
        while True:  # a database that is still starting up is not a test failure
            try:
                with admin.connect() as conn:
                    conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
                    conn.execute(text(f'CREATE DATABASE "{name}"'))
                return
            except OperationalError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(1)
    finally:
        admin.dispose()


_url = _test_database_url()
_recreate(_url)
os.environ["DATABASE_URL"] = _url.render_as_string(hide_password=False)
