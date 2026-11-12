# Status report: Court Booking Service

This report breaks the brief into individual tasks, says what is done, and lists everything that
is **missing, incomplete or still pending**. Evidence is a test file or a command you can run.

## 1. Summary

- All 8 functional requirements, all 3 guarantees and all 4 fixed-contract endpoints are built.
- 193 automated tests pass. ruff, `ruff format --check` and strict mypy are clean, with **no inline
  suppressions** anywhere in the code.
- Not finished: the live walkthrough, the mid-month requirement change, confirming the first CI run
  on GitHub, and several product gaps listed in section 4.

## 2. Requirements broken into tasks

### Functional requirements

| ID | Requirement from the brief | Status | How it is done | Evidence |
|---|---|---|---|---|
| F1 | Members browse availability | **Done** | `GET /courts/{id}/availability?date=`: hourly slots, free or unavailable, with price | `test_availability_api.py` (13), `test_day_slots.py` (5) |
| F2 | Book in two steps: reserve briefly, then confirm | **Done** | `POST /holds` (10 minutes), `POST /holds/{id}/confirm` | `test_holds_api.py` (22), `test_confirm_api.py` (12) |
| F3 | An unconfirmed reservation never blocks a slot forever | **Done** | Expired holds are ignored everywhere and cleared inside the next overlapping request. No background job needed | expiry tests in `test_holds_api.py`, `test_confirm_api.py`, `test_availability_api.py` |
| F4 | Cancel: full refund with at least 24 hours' notice, otherwise none | **Done**, refund is recorded only | `POST /bookings/{id}/cancel`; a retry returns the stored result | `test_cancel_api.py` (12), `test_cancel_concurrency.py` (2), 5 refund unit tests |
| F5 | Weekly recurring booking | **Done** | `POST /recurring-bookings`, 1 to 12 weeks, same local time every week | `test_recurring_api.py` (20), `test_recurring_concurrency.py` (7) |
| F6 | Venues price peak and off-peak hours differently | **Done in code, incomplete to configure** | Peak windows per venue, priced hour by hour in venue-local time. Rates and windows can only be set through `app.seed` or SQL | 13 pricing unit tests in `test_policies.py`, price tests in `test_holds_api.py` |
| F7 | A member holds at most 3 active future bookings | **Done** | Unexpired holds count; a whole recurring series counts as one; the member's row is locked so parallel requests cannot exceed the limit | limit tests in `test_holds_api.py`, `test_recurring_api.py`, 2 parallel tests |
| F8 | Staff see court utilization per week | **Done** | `GET /staff/utilization?week=`, booked hours divided by open hours, per court, staff key required | `test_utilization_api.py` (14), `test_week_policies.py` (16) |

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
| Python 3.11 | **Done, with a caveat** | Docker image and CI use 3.11. I develop on 3.12, so the 3.11 run is only confirmed once CI runs |
| FastAPI, SQLAlchemy 2, Alembic, Postgres 15 | **Done** | 3 migrations; `postgres:15` in `docker-compose.yml` |
| pytest | **Done** | 193 tests |
| ruff and mypy clean, no inline suppressions | **Done** | `ruff check`, `ruff format --check` and strict `mypy` all pass; a search for `noqa`, `type: ignore`, `fmt: skip` finds none |
| Everything runs with `docker compose up` | **Done, to be re-checked** | Worked on my laptop; re-run from a clean clone before the final push |

### Deliverables

| Deliverable | Status |
|---|---|
| DESIGN.md (assumptions, questions for the client, rejected alternatives) | **Done**, updated to match the final system (section 13). Review by the mentor: not confirmed |
| Own tests proving each guarantee | **Done** |
| README | **Done** |
| CI workflow | **Written, first GitHub run not yet confirmed** |
| LIMITATIONS.md | **Done** |
| Live walkthrough where I change something on the spot | **Pending** |

## 3. How I verified

| Check | Result |
|---|---|
| `pytest` (full suite) | 193 passed, 5 runs in a row, all identical |
| `ruff check .` | passes |
| `ruff format --check .` | passes |
| `mypy` (strict) | no issues in 50 files |
| Search for inline suppressions | **Found 5 `# fmt: skip` comments in `tests/test_utilization_api.py` and removed them**; none remain |
| Tests use their own throwaway database | They no longer share data with manual testing, which fixed runs that failed once and passed on a rerun |
| Manual checks through the API docs and `curl` | hold, retry, conflict, confirm, cancel, recurring, availability, utilization, member limit, staff key |
| Breaking the code on purpose | For each important rule I removed or changed it and confirmed a test fails. Examples: removing the double-booking constraint (50 bookings), removing the member lock (limit exceeded), saving recurring weeks one by one (partial rows), recomputing the refund on a retry, counting holds in utilization, using UTC weeks |

## 4. Missing, incomplete and pending

### Missing (not built)

| # | Item | Why it matters |
|---|---|---|
| M1 | No endpoints to create venues, courts, members or peak windows | Data can only be created with `app.seed` or SQL. Needed for real use |
| M2 | Staff cannot cancel or book on a member's behalf | Open question 8 in the design |
| M3 | No single call to cancel a whole recurring series | Each week is cancelled separately |
| M4 | No "my bookings" list | Members cannot see what they hold |
| M5 | Refunds are recorded, not paid out | There is no payment system |
| M6 | No real authentication | Identity comes from a header, staff use one shared key |

### Incomplete

| # | Item | What is left |
|---|---|---|
| I1 | CI | Workflow written and its steps pass locally; the first run on GitHub is not yet checked |
| I2 | Python 3.11 | Verified locally on 3.12; CI will confirm 3.11 |
| I3 | Hold expiry check | Verified by automated tests with a controllable clock; not waited out live for 10 minutes |
| I4 | DESIGN.md review | Waiting for the mentor's review comments |

### Pending

| # | Item |
|---|---|
| P1 | Confirm the request and response shapes of the four contract endpoints with the mentor, since the brief leaves them open |
| P2 | Answers needed from the client: public holidays, closed courts, member timezone (questions 9, 11, 13) |
| P3 | The mid-month requirement change |
| P4 | Preparing for the live walkthrough: explaining and changing any line |
| P5 | The Jira task and invitation |

## 5. Next steps, in order

1. Check that the first CI run is green and fix anything it finds.
2. Agree the contract shapes with the mentor (P1).
3. Decide with the mentor whether M1 (admin endpoints) is wanted, and build it if so.
4. Absorb the requirement change when it arrives.
5. Practise the walkthrough.
