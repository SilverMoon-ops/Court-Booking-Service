# Court Booking Service

A backend for a community club that rents sports courts by the hour across several venues.
The main complaint it solves is **double-booking**, so the design makes double-booking
impossible by construction, not just unlikely.

**Stack:** Python 3.11, FastAPI, SQLAlchemy 2 + Alembic, Postgres 15, pytest, ruff, mypy (strict).

## Quick start

```bash
cp .env.example .env
docker compose up --build
```

- API: http://localhost:8000 — interactive docs: http://localhost:8000/docs
- Migrations run automatically when the API container starts.
- Demo data (a venue, two courts, two members) and the ids to use:
  ```bash
  docker compose exec api python -m app.seed
  ```

## Using the API

There are no logins yet. Members send `X-Member-Id: <their id>`; staff send `X-Staff-Key: <key>`
(the value of `STAFF_API_KEY` in `.env`). Times must include an offset, for example
`2031-01-06T18:00:00+05:30`. Use any date in the future.

```bash
# 1. See the day's slots and prices
curl "localhost:8000/courts/$COURT/availability?date=2031-01-06"

# 2. Reserve a slot for 10 minutes (a "hold")
curl -X POST localhost:8000/holds -H "X-Member-Id: $ASHA" -H "Content-Type: application/json" \
  -d '{"court_id":"'$COURT'","start_at":"2031-01-06T18:00:00+05:30","end_at":"2031-01-06T19:00:00+05:30"}'

# 3. Confirm it (safe to repeat)
curl -X POST localhost:8000/holds/$HOLD/confirm -H "X-Member-Id: $ASHA"

# 4. Cancel it: full refund with at least 24 hours' notice, otherwise none
curl -X POST localhost:8000/bookings/$HOLD/cancel -H "X-Member-Id: $ASHA"

# 5. Book the same time every week for 4 weeks, all or nothing
curl -X POST localhost:8000/recurring-bookings -H "X-Member-Id: $ASHA" -H "Content-Type: application/json" \
  -d '{"court_id":"'$COURT'","start_at":"2031-01-13T18:00:00+05:30","end_at":"2031-01-13T19:00:00+05:30","occurrences":4}'

# 6. Staff: utilization of each court for one week
curl "localhost:8000/staff/utilization?week=2031-W03" -H "X-Staff-Key: dev-staff-key"
```

| Endpoint | Purpose |
|---|---|
| `GET /courts/{id}/availability?date=` | Hourly slots for a day: free or unavailable, with price |
| `POST /holds` | Reserve a slot for 10 minutes. Repeating the same request returns the same hold (`200`) |
| `POST /holds/{id}/confirm` | Turn a hold into a booking. Safe to repeat |
| `POST /bookings/{id}/cancel` | Cancel a booking or release a hold. Safe to repeat |
| `POST /recurring-bookings` | Weekly booking, 1 to 12 weeks, all weeks or none |
| `GET /staff/utilization?week=&venue_id=` | Booked hours ÷ open hours per court (staff only) |

Errors always look like `{"error": {"code": "...", "message": "..."}}`:

| Status | Codes |
|---|---|
| 401 | `member_required`, `staff_required` |
| 403 | `forbidden` |
| 404 | `not_found` |
| 409 | `slot_unavailable`, `booking_limit_reached`, `invalid_state` |
| 410 | `hold_expired` |
| 422 | `invalid_slot`, `invalid_week` (and standard validation errors) |
| 503 | `staff_not_configured` |

## The rules, as implemented

- A booking lasts 1 to 3 whole hours, starts on the hour in the **venue's local time**, stays inside
  opening hours, does not cross midnight, and starts in the future.
- A hold lasts 10 minutes (`HOLD_TTL_MINUTES`). An expired hold never blocks a slot.
- A member may have at most **3 active future bookings**. Unexpired holds count. A whole recurring
  series counts as **one**.
- Each hour is priced at the venue's peak or off-peak rate, using the venue's local time.
- Cancelling with at least 24 hours' notice refunds the full price, otherwise nothing. The refund is
  recorded, not paid out. Releasing an unconfirmed hold refunds nothing (nothing was paid).
- Weekly series keep the same local clock time across daylight-saving changes.

## How the three guarantees are enforced and proven

| Guarantee | How | Proof |
|---|---|---|
| A court is never double-booked, however many requests arrive at once | A Postgres exclusion constraint (`ex_bookings_no_overlap`, migration `0003`) rejects overlapping active bookings on the same court | `tests/test_no_double_booking.py`: 50 simultaneous inserts, exactly 1 wins. `tests/test_holds_concurrency.py`: the same through the real API |
| A retried confirm never creates two bookings | A hold and its booking are the **same row**. Confirm is one guarded `UPDATE`, so a repeat finds it already confirmed and returns the same booking | `tests/test_confirm_api.py`, `tests/test_confirm_concurrency.py`: 20 simultaneous confirms, one booking |
| A failed multi-step operation leaves no partial state | Every request is one transaction committed once. A recurring series inserts all weeks and commits at the end | `tests/test_recurring_concurrency.py`: clashing series, a crash just before the commit, and a series racing single holds. The loser always has zero rows |

Every important rule was also checked by deliberately breaking it and confirming a test fails.

## Configuration

| Variable | Meaning | Default |
|---|---|---|
| `DATABASE_URL` | Postgres connection string | local development database |
| `HOLD_TTL_MINUTES` | How long a hold lasts | `10` |
| `STAFF_API_KEY` | Secret for staff endpoints. **Empty switches staff access off** (`503`) | empty |
| `TEST_DATABASE_URL` | Optional: a specific test database (its name must end in `_test`) | derived |

## Development

```bash
python -m venv .venv && source .venv/bin/activate     # on Windows Git Bash: .venv/Scripts/activate
pip install -r requirements-dev.txt
docker compose up -d db                               # Postgres on localhost:5432

ruff check . && ruff format --check .                 # lint and format
mypy                                                  # strict types, no suppressions
pytest                                                # the whole suite
alembic upgrade head                                  # apply migrations to the dev database
alembic revision --autogenerate -m "describe change"  # create a migration after changing a model
```

**Tests use their own database.** On every run they drop and recreate `<database>_test`, so they
never touch your development data and never depend on leftover state.

```
app/
  main.py          app wiring and error handlers
  models.py        SQLAlchemy tables
  policies.py      pure business rules: pricing, refunds, slot checks, weeks (no database)
  routers/         HTTP endpoints
  services/        the logic of each operation; each owns its transaction
  seed.py          demo data
migrations/        Alembic migrations (0003 holds the double-booking constraint)
tests/             unit, API and concurrency tests
DESIGN.md          assumptions, open questions, rejected alternatives
LIMITATIONS.md     how the system can still fail
```

## Notes on how this was built

1. **Design first** (`DESIGN.md`): the brief is intentionally incomplete, so assumptions, questions
   for the client and rejected alternatives were written down before coding.
2. **Foundation:** Docker Compose with Postgres, FastAPI skeleton, an injectable clock so tests control time.
3. **Schema:** Alembic migrations and models, with rules enforced by CHECK constraints in the database.
4. **The double-booking constraint** and a test that fires 50 simultaneous requests. The test was shown
   to fail when the constraint is removed.
5. **Business rules as pure functions** (`policies.py`), so each rule is easy to test and change.
6. **Holds, confirm, cancel, availability, recurring bookings, utilization**, one at a time. Each came
   with tests that attack it concurrently, and each was verified by breaking the code on purpose.
7. **Reliability of the tests themselves:** a flaky test run was traced to tests sharing the
   development database, and fixed by giving the tests a fresh database each run.

See `LIMITATIONS.md` for what this system does not do and how it can still fail.
