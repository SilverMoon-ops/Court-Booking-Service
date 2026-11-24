# Status report: Court Booking Service

This report breaks the brief into individual tasks, says what is done, and lists everything that
is **missing, incomplete or still pending**. Evidence is a test file or a command you can run.

## 1. Summary

- All 8 functional requirements, all 3 guarantees and all 4 fixed-contract endpoints are built.
- 260 automated tests pass. ruff, `ruff format --check` and strict mypy are clean, with **no inline
  suppressions** anywhere in the code.
- CI passes on GitHub (run #2): lint, format, strict mypy and all 260 tests on Python 3.11, and
  `docker compose up` from a clean checkout.
- Not finished: the live walkthrough, the mid-month requirement change, and several product gaps
  listed in section 4.

## 2. Requirements broken into tasks

### Functional requirements

| ID | Requirement from the brief | Status | How it is done | Evidence |
|---|---|---|---|---|
| F1 | Members browse availability | **Done** | `GET /courts/{id}/availability?date=`: hourly slots, free or unavailable, with price | `test_availability_api.py` (13), `test_day_slots.py` (5) |
| F2 | Book in two steps: reserve briefly, then confirm | **Done** | `POST /holds` (10 minutes), `POST /holds/{id}/confirm` | `test_holds_api.py` (22), `test_confirm_api.py` (12) |
| F3 | An unconfirmed reservation never blocks a slot forever | **Done** | Expired holds are ignored everywhere and cleared inside the next overlapping request. No background job needed | expiry tests in `test_holds_api.py`, `test_confirm_api.py`, `test_availability_api.py` |
| F4 | Cancel: full refund with at least 24 hours' notice, otherwise none | **Done**, refund is recorded only | `POST /bookings/{id}/cancel`; a retry returns the stored result | `test_cancel_api.py` (12), `test_cancel_concurrency.py` (2), 5 refund unit tests |
| F5 | Weekly recurring booking | **Done** | `POST /recurring-bookings`, 1 to 12 weeks, same local time every week | `test_recurring_api.py` (20), `test_recurring_concurrency.py` (7) |
| F6 | Venues price peak and off-peak hours differently | **Done** | Peak windows per venue, priced hour by hour in venue-local time. Staff set rates and windows through `POST /venues` and `PUT /venues/{id}/pricing` | 13 pricing unit tests in `test_policies.py`, price tests in `test_holds_api.py`, pricing tests in `test_admin_api.py` |
| F7 | A member holds at most 3 active future bookings | **Done** | Unexpired holds count; a whole recurring series counts as one; the member's row is locked so parallel requests cannot exceed the limit | limit tests in `test_holds_api.py`, `test_recurring_api.py`, 2 parallel tests |
| F8 | Staff see court utilization per week | **Done** | `GET /staff/utilization?week=`, booked hours divided by open hours, per court, staff key required | `test_utilization_api.py` (14), `test_week_policies.py` (16) |
| A1 | Administration (closes gap M1): create venues, courts and members; change pricing | **Done** | Staff-only `POST /venues`, `GET /venues`, `PUT /venues/{id}/pricing`, `POST /venues/{id}/courts`, `POST /members`. Names are unique in the database. A pricing update locks the venue so two updates are never mixed | `test_admin_api.py` (41), `test_admin_concurrency.py` (3) |
| A2 | The database setup visible as plain SQL | **Done** | `sql/schema.sql` (generated from the migrations), `sql/seed.sql` (idempotent), `sql/inspect.sql`; also `python -m app.seed` and the staff API | `test_sql_files.py` (8): a database built from the SQL matches the migrations, Alembic continues from it, the double-booking rule works on it, the seed never duplicates |
| A3 | Members can find courts and their own bookings (closes gap M4) | **Done** | `GET /courts` (public) and `GET /bookings` (own bookings and holds, status filter) | `test_member_endpoints_api.py` (15) |

### Guarantees (must be proven)

| ID | Guarantee | Status | How it is enforced | How it is proven |
|---|---|---|---|---|
| G1 | A court is never double-booked, however many requests arrive at once | **Done** | Postgres exclusion constraint `ex_bookings_no_overlap` (migration `0003`) | `test_no_double_booking.py`: 50 simultaneous inserts, exactly 1 wins. `test_holds_concurrency.py`: 20 members race through the real API. The test **fails with 50 bookings when the constraint is removed** |
| G2 | Retrying a timed-out confirm never creates two bookings | **Done** | A hold and its booking are the same row. Confirm is one guarded `UPDATE`; a repeat returns the same booking | `test_confirm_api.py`; `test_confirm_concurrency.py`: 20 simultaneous confirms, one booking, one `confirmed_at` |
| G3 | A failed multi-step operation leaves no partial state | **Done** | Every request is one transaction committed once; a recurring series inserts every week and commits at the end | `test_recurring_concurrency.py`: clashing series (loser always has 0 rows), a crash just before the commit, a series racing single holds |

### Fixed contract

| Endpoint | Status |
|---|---|
| `POST /holds` | **Done** |
| `POST /holds/{id}/confirm` | **Done** |
| `POST /bookings/{id}/cancel` | **Done** |
| `GET /courts/{id}/availability` | **Done** |

Caveat: the brief does not specify request or response shapes. I chose them (member identified by
the `X-Member-Id` header; JSON bodies as shown in the README). See open item P1.

### Stack and quality rules

| Requirement | Status | Evidence |
|---|---|---|
| Python 3.11 | **Done** | Docker image and CI use 3.11, and CI run #2 passed on it. I develop on 3.12 locally |
| FastAPI, SQLAlchemy 2, Alembic, Postgres 15 | **Done** | 3 migrations; `postgres:15` in `docker-compose.yml` |
| pytest | **Done** | 260 tests |
| ruff and mypy clean, no inline suppressions | **Done** | `ruff check`, `ruff format --check` and strict `mypy` all pass; a search for `noqa`, `type: ignore`, `fmt: skip` finds none |
| Everything runs with `docker compose up` | **Fix ready, verify in CI** | A fresh clone has no `.env` (it is not in git) and the old compose file required one, so `docker compose up` would fail for a reviewer. Compose now has a default for every setting and CI runs it without a `.env`. Done once that CI run is green |

### Deliverables

| Deliverable | Status |
|---|---|
| DESIGN.md (assumptions, questions for the client, rejected alternatives) | **Done**, updated to match the final system (section 13). Review by the mentor: not confirmed |
| Own tests proving each guarantee | **Done** |
| README | **Done** |
| CI workflow | **Done**: passing on GitHub (run #2: lint, format, types, 260 tests, and `docker compose up`) |
| LIMITATIONS.md | **Done** |
| Live walkthrough where I change something on the spot | **Pending** |

## 3. How I verified

| Check | Result |
|---|---|
| `pytest` (full suite) | 260 passed, 5 runs in a row, all identical |
| `ruff check .` | passes |
| `ruff format --check .` | passes |
| `mypy` (strict) | no issues in 50 files |
| Search for inline suppressions | **Found 5 `# fmt: skip` comments in `tests/test_utilization_api.py` and removed them**; none remain |
| Tests use their own throwaway database | They no longer share data with manual testing, which fixed runs that failed once and passed on a rerun |
| GitHub Actions CI | Run #2 passed both jobs (Python 3.11, Postgres 15). Run #1 failed in the docker job because the readiness check gave up on the first reset connection while the API was still starting (curl exit code 56). I reproduced it and fixed it with `--retry-all-errors` |
| Manual checks through the API docs and `curl` | hold, retry, conflict, confirm, cancel, recurring, availability, utilization, member limit, staff key |
| Breaking the code on purpose | For each important rule I removed or changed it and confirmed a test fails. Examples: removing the double-booking constraint (50 bookings), removing the member lock (limit exceeded), saving recurring weeks one by one (partial rows), recomputing the refund on a retry, counting holds in utilization, using UTC weeks |

## 4. Missing, incomplete and pending

### Missing (not built)

M1 (endpoints to create venues, courts, members and peak windows) and M4 (a "my bookings" list) were built and moved to section 2 as A1 and A3.

| # | Item | Why it matters |
|---|---|---|
| M2 | Staff cannot cancel or book on a member's behalf | Open question 8 in the design |
| M3 | No single call to cancel a whole recurring series | Each week is cancelled separately |
| M5 | Refunds are recorded, not paid out | There is no payment system |
| M6 | No real authentication | Identity comes from a header, staff use one shared key |

### Incomplete

| # | Item | What is left |
|---|---|---|
| I1 | Hold expiry check | Verified by automated tests with a controllable clock; not waited out live for 10 minutes |
| I2 | DESIGN.md review | Waiting for the mentor's review comments |

### Pending

| # | Item |
|---|---|
| P1 | Confirm the request and response shapes of the four contract endpoints with the mentor, since the brief leaves them open |
| P2 | Answers needed from the client: public holidays, closed courts, member timezone (questions 9, 11, 13) |
| P3 | The mid-month requirement change |
| P4 | Preparing for the live walkthrough: explaining and changing any line |
| P5 | The Jira task and invitation |

## 5. Next steps, in order

1. Agree the contract shapes with the mentor (P1).
2. Absorb the requirement change when it arrives.
3. Practise the walkthrough.
