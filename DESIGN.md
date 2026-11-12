# DESIGN.md: Court Booking Service

Status: draft for week-1 review. Nothing is built yet.

## 1. Problem and goals

A community club rents sports courts by the hour across several venues. The main complaint is double-booking. This service must make double-booking **impossible by construction**, not just unlikely.

Three guarantees drive every decision below:

| #   | Guarantee                                                            | Enforced by                                               |
| --- | -------------------------------------------------------------------- | --------------------------------------------------------- |
| G1  | A court is never double-booked, however many requests arrive at once | Postgres exclusion constraint                             |
| G2  | A retried (timed-out) confirm never produces two bookings            | Idempotent state transition on a single row               |
| G3  | A failed multi-step operation leaves no partial state                | One DB transaction per request, no side effects inside it |

Each guarantee has a matching automated test (section 10).

## 2. Scope

In scope: availability, hold, confirm, cancel, weekly recurring booking, peak/off-peak pricing, the 3-booking limit, weekly utilization for staff.

Out of scope (see open questions): real payments, real refunds, emails/notifications, login and passwords, waitlists, public holidays.

## 3. Architecture

```
FastAPI routers  ->  services (transaction boundary)  ->  SQLAlchemy 2 models  ->  Postgres 15
                          |
                          +-> pure policy functions (pricing, cancellation, limits)
```

- **Routers** only parse and validate input and map errors to HTTP codes.
- **Services** own the transaction. One request = one transaction. Nothing is committed outside a service call.
- **Policy functions** (price for a time range, refund for a cancellation, slot alignment) are pure functions with no DB access. They are easy to unit test and cheap to change when requirements change.
- **Clock** is an injected dependency, so tests can control time (hold expiry, the 24-hour rule).

## 4. Data model

All timestamps are `timestamptz` stored in UTC. Money is an integer in minor units (paise), never a float.

- `venues(id, name, timezone, open_hour, close_hour, offpeak_rate, peak_rate)`
- `peak_windows(id, venue_id, weekday, start_hour, end_hour)` hours are venue-local
- `courts(id, venue_id, name)`
- `members(id, name)`
- `recurring_series(id, member_id, court_id, weekday, start_hour, duration_hours, first_date, occurrences)`
- `bookings(id, member_id, court_id, series_id NULL, start_at, end_at, status, price, refund_amount NULL, hold_expires_at NULL, created_at, confirmed_at NULL, cancelled_at NULL)`

`status` is one of `held`, `confirmed`, `cancelled`, `expired`.

**A hold and a booking are the same row.** `POST /holds` creates a row in state `held`. `POST /holds/{id}/confirm` moves the same row to `confirmed`. The hold id and the booking id are therefore the same id. This removes any chance of a hold and a booking disagreeing, and it makes confirm naturally idempotent.

### The double-booking constraint (G1)

```sql
CREATE EXTENSION IF NOT EXISTS btree_gist;

ALTER TABLE bookings ADD CONSTRAINT bookings_no_overlap
  EXCLUDE USING gist (
    court_id WITH =,
    tstzrange(start_at, end_at, '[)') WITH &&
  )
  WHERE (status IN ('held', 'confirmed'));
```

Two live rows for the same court cannot overlap in time. The database rejects the second insert even if 100 requests race. Ranges are half-open (`[)`), so a 10:00-11:00 booking and an 11:00-12:00 booking do not conflict.

Other database checks: duration is exactly 1, 2 or 3 hours, `price >= 0`, `refund_amount` between 0 and `price`, status is one of the four known values, and a `held` row must have an expiry.

**Hour alignment is not a database check.** Alignment is judged in the venue's local time, and some local timezones are not whole hours from UTC (India is UTC+5:30, so 10:00 local is 04:30 UTC). A UTC-based check would reject valid bookings. The application validates alignment in venue-local time instead (`app/policies.py`), and the database enforces only the duration.

## 5. Behaviour decisions

The brief is silent or ambiguous in places. These are my decisions; section 8 lists what I would ask the client.

### 5.1 Time and slots

- Booking granularity is whole hours. A booking is 1 to 3 consecutive hours on one court, within one day, inside the venue's opening hours.
- Hours are aligned in the **venue's local timezone**. API inputs are ISO 8601 with an offset and are converted to UTC.
- A booking must start in the future. A booking that starts exactly now is also rejected.
- Alignment to the hour is checked in the venue's local time, not UTC (see section 4).

### 5.2 Hold, then confirm

- `POST /holds` creates a `held` row with `hold_expires_at = now + 10 minutes` (configurable).
- A hold that has expired **never blocks anything**. See 5.3.
- `POST /holds/{id}/confirm` succeeds only if the row is `held`, unexpired, and owned by the caller.
- Confirm is idempotent (G2). The outcomes are:

| Current state of the row              | Result                                      |
| ------------------------------------- | ------------------------------------------- |
| `held`, not expired                   | becomes `confirmed`, 200                    |
| `confirmed` (a retry)                 | 200, same booking returned, nothing changes |
| `held` but past expiry, or `expired`  | 410 Gone                                    |
| `cancelled`                           | 409                                         |
| not found, or not owned by the caller | 404                                         |

- Confirming is a single guarded statement: `UPDATE ... SET status='confirmed' WHERE id=:id AND member_id=:m AND status='held' AND hold_expires_at > :now`. If it updates 0 rows, a follow-up read decides which row of the table above applies. No read-then-write race exists.
- `POST /holds` is also idempotent: if the same member already has a live hold for the same court and time range, the existing hold is returned instead of a conflict.

### 5.3 Expiry without a background job

An exclusion constraint cannot reference `now()`, so an expired hold would keep blocking the slot unless its status changes. Correctness must not depend on a cleanup job being alive. So:

1. Inside the `POST /holds` transaction, **before inserting**, run:
   `UPDATE bookings SET status='expired' WHERE status='held' AND hold_expires_at <= :now AND court_id=:c AND range overlaps the requested range`.
2. Then insert the new hold. If a live booking still overlaps, the constraint raises, and we return 409.
3. Availability queries ignore any `held` row whose `hold_expires_at` has passed.
4. An optional periodic sweeper may also flip stale rows to `expired` for tidiness and for utilization queries. It is **not** required for correctness.

Race check: two requests try to expire the same stale hold. The second `UPDATE` waits on the row lock, then sees it already expired and changes nothing. Then both inserts race, the constraint lets exactly one win, and the other gets 409.

### 5.4 Cancellation

- `POST /bookings/{id}/cancel` works for the owner (or staff).
- Refund rule: if `start_at - now >= 24h`, refund the full price. Otherwise refund 0. Exactly 24h counts as full refund ("at least 24 hours").
- Cancelling an unconfirmed hold releases it with no refund (nothing was paid).
- Cancelling is idempotent: cancelling an already-cancelled booking returns the same result.
- Cancelling a booking whose start time has passed returns 409.
- The refund is **recorded** (`refund_amount`), not paid out. See open questions.
- The price is snapshotted at hold time, so later price changes never alter refunds.

### 5.5 Weekly recurring bookings

- `POST /recurring-bookings` takes court, weekday/start time, duration, first date, and a number of occurrences (1 to 12).
- It creates all occurrences as `confirmed` bookings in **one transaction**. If any week conflicts, the whole request fails with 409 and no row is written (G3). The error lists the conflicting dates.
- Occurrences keep the same **local wall-clock time** across daylight-saving changes. They are computed in the venue timezone, not by adding 7 x 24 hours.
- Each occurrence is a normal booking row, so the exclusion constraint protects every one of them.
- Recurring bookings skip the hold step because they are created already confirmed. This is an assumption.
- Each occurrence can be cancelled individually under the normal 24-hour rule.

### 5.6 The 3-booking limit

- Active future bookings = rows with `status='confirmed'` and `start_at > now`, **plus live unexpired holds** (otherwise a member could exceed the limit by holding many slots).
- **A recurring series counts as 1** toward the limit. If occurrences each counted, a 4-week series would be impossible, which contradicts the recurring requirement. This is the biggest contradiction in the brief and a top open question.
- The limit check has its own race: two parallel requests both read "2 bookings" and both insert. To prevent it, every hold or series creation first runs `SELECT ... FROM members WHERE id=:m FOR UPDATE`, which serialises one member's requests. The count is then read under that lock.
- Lock order is always member row first, then the booking insert, so deadlocks cannot occur between these paths.

### 5.7 Pricing

- Each venue has an off-peak hourly rate, a peak hourly rate, and peak windows by weekday and local hour.
- Price = sum over each hour in the booking of that hour's rate. A booking that straddles a peak boundary is charged per hour, so no special case is needed.
- Price is computed when the hold is created and stored on the row.

### 5.8 Availability

`GET /courts/{id}/availability?date=YYYY-MM-DD` returns the venue-local day as a list of hourly slots, each with `status` (`free` or `unavailable`) and `price`. It never reveals who booked a slot. Expired holds show as free.

### 5.9 Utilization for staff

`GET /staff/utilization?week=YYYY-Www&venue_id=...` returns, per court, booked hours divided by open hours for that ISO week (Monday start, venue-local). Only `confirmed` bookings count. Holds and cancellations do not.

### 5.10 Identity

No real authentication. Members identify with an `X-Member-Id` header and staff with an `X-Staff-Key` header. Real auth is out of scope.

## 6. Error contract

| Situation                                                     | HTTP                        |
| ------------------------------------------------------------- | --------------------------- |
| Invalid input (unaligned time, past, too long, outside hours) | 422                         |
| Slot already taken                                            | 409 `slot_unavailable`      |
| Member at the 3-booking limit                                 | 409 `booking_limit_reached` |
| Hold expired                                                  | 410 `hold_expired`          |
| Wrong state for the action                                    | 409                         |
| Not found or not yours                                        | 404                         |

## 7. Failure and atomicity (G3)

- Every endpoint runs in exactly one transaction. Any exception rolls everything back.
- The system has no side effects inside a transaction (no payment calls, no emails), so a rollback leaves nothing behind. If side effects are added later, they must use an outbox table written in the same transaction.
- Constraint violations are caught at the service layer and translated to 409. They are never swallowed halfway through a multi-row operation.
- When two inserts collide, Postgres reports either an exclusion violation or, occasionally, a deadlock error for the loser. Both mean the slot was taken, so both are mapped to 409. Neither can ever result in two bookings.
- Isolation level stays at Postgres default (READ COMMITTED). Correctness comes from constraints and row locks, not from relying on a stronger isolation level.

## 8. Open questions for the client

1. Does the 3-booking limit count a whole recurring series as one, or each occurrence? (My assumption: one.)
2. Should unconfirmed holds count toward the limit? (Assumption: yes.)
3. Are refunds actually paid out by this system, or only recorded for staff to process?
4. How long should a hold last? (Assumption: 10 minutes.)
5. Can bookings be longer than 1 hour, and what is the maximum? (Assumption: 1 to 3 hours.)
6. Maximum length of a recurring series? (Assumption: 12 weeks.)
7. What happens to a recurring series when one week conflicts: reject all, or skip that week? (Assumption: reject all.)
8. Can staff cancel or create bookings on behalf of members? Does a staff cancellation always refund in full?
9. Do public holidays or special events change peak pricing or closing?
10. What does "utilization" mean: booked hours over opening hours, or over hours the court was bookable? (Assumption: opening hours.)
11. If a venue closes a court, what happens to existing bookings?
12. Is "3 active bookings" per member across all venues, or per venue? (Assumption: across all.)
13. Is the member's local timezone ever relevant, or only the venue's? (Assumption: venue only.)

## 9. Alternatives considered and rejected

| Alternative                                       | Why rejected                                                                                                                                       |
| ------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------- |
| Check availability in Python, then insert         | Classic race: two requests both see "free". Only a DB constraint closes it.                                                                        |
| Pre-generate one row per slot and lock it         | Needs a huge slot table and a generator job, and complicates variable-length bookings. The exclusion constraint handles arbitrary ranges directly. |
| Redis or application-level distributed locks      | Adds a second system that can fail or expire independently. The DB is already the single source of truth.                                          |
| SERIALIZABLE isolation with retries               | Works, but pushes retry logic into every endpoint and still needs care. Constraints give the same safety more simply.                              |
| Optimistic version columns                        | They detect a conflict on one row but cannot detect overlapping ranges across different rows.                                                      |
| Separate `holds` and `bookings` tables            | Needs copying data on confirm and creates a window where a hold and booking disagree. One row with a status avoids this.                           |
| Cleanup job as the only expiry mechanism          | If the job is down or late, expired holds block slots. This violates "never blocks forever".                                                       |
| Each recurring occurrence counts toward the limit | Makes a weekly booking of more than 3 weeks impossible.                                                                                            |
| Floats for money                                  | Rounding errors. Integer paise is exact.                                                                                                           |
| Adding 7 x 24 hours for weekly repeats            | Shifts the local time across daylight-saving changes.                                                                                              |

## 10. How each guarantee is proven

| Guarantee   | Test                                                                                                                                                                                                                                   |
| ----------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| G1          | Fire 50 concurrent `POST /holds` at the same court and slot against real Postgres. Assert exactly one 201 and 49 409s, and exactly one live row. Add overlapping-but-not-identical ranges, and adjacent ranges that must both succeed. |
| G1 + expiry | Create a hold, advance the clock past expiry, and assert another member can book the same slot.                                                                                                                                        |
| G2          | Confirm the same hold several times, including concurrently. Assert all return the same booking and exactly one booking row exists. Also retry `POST /holds` and assert one hold.                                                      |
| G3          | Create a recurring series where week 3 conflicts. Assert zero rows were written. Inject a failure mid-service and assert full rollback.                                                                                                |
| Limit       | Fire concurrent holds from one member at 2 existing bookings. Assert the member never ends up with more than 3.                                                                                                                        |
| Cancel      | Boundary tests at 24h, 24h minus one second, and past start.                                                                                                                                                                           |
| Pricing     | Peak/off-peak boundary and daylight-saving cases as unit tests.                                                                                                                                                                        |

Tests run against real Postgres (the same Docker image as production), because SQLite cannot enforce an exclusion constraint.

## 11. Known risks (preview of LIMITATIONS.md)

- The exclusion constraint exists only in migration 0003 and is not declared on the SQLAlchemy model, because SQLAlchemy's `ExcludeConstraint` has no type hints and the project requires strict mypy with no suppressions. The tests prove it is present.
- A weekly series that lands on a local time skipped by daylight saving is refused as a whole. A local time that happens twice uses its first occurrence.
- A single app server clock decides expiry. With several app servers, clock skew could shift expiry by a small amount.
- No authentication, so identity headers can be forged.
- Refunds are only recorded.
- The service does not itself prevent a member abusing many accounts.

## 12. Plan

- Week 1: this document, reviewed.
- Weeks 2-3: Docker and Alembic baseline, models and the constraint, availability, hold, confirm, cancel, recurring, pricing, limit, utilization, guarantee tests, ruff, mypy, CI, README.
- Week 4: absorb the mid-month requirement change (policy code is isolated to keep this cheap), write LIMITATIONS.md, prepare the live walkthrough.

## 13. As built: what changed since this design was approved

This section records where the finished system differs from, or adds detail to, the design above.

**Holds, confirm and cancel**

- A repeated `POST /holds` for the identical slot returns the existing hold with `200` instead of `201`.
- Confirm is one guarded `UPDATE` (still `held`, unexpired, not yet started, owned by the caller). If it changes nothing, a read decides: already confirmed returns the same booking, expired or already-started returns `410`, cancelled returns `409`, not found or not yours returns `404`.
- Cancel locks the row (`SELECT ... FOR UPDATE`) and then decides. A retry returns the **stored** result and never recalculates the refund, because the 24-hour window may have closed in the meantime. Cancelling a booking that has already started returns `409`; an expired hold returns `410`. Cancelling an unconfirmed hold records a refund of `0`.
- Cancel and confirm can race on the same hold. Both orders end consistently: confirmed then cancelled with a full refund, or cancelled first and the confirm refused.

**Availability**

- `GET /courts/{id}/availability?date=` needs no member header, because browsing is open. Slots are shown in the venue's local time. Hours that have already started are `unavailable`. The response never says who holds a slot. A test checks that availability agrees with what `POST /holds` actually does for every hour.

**Recurring bookings**

- `POST /recurring-bookings` takes the first booking plus `occurrences` (1 to 12). All weeks are validated, checked and inserted in one transaction and committed once. If any week is taken the whole request fails with `409`, and the message lists the taken dates.
- A series counts as **one** of the member's three bookings (answers open question 1).
- An identical request from the same member returns the existing series (`200`). This is a heuristic based on the request contents, not an idempotency key.
- A series that lands on a local hour skipped by daylight saving is refused as a whole.

**Staff utilization**

- `GET /staff/utilization?week=2030-W02&venue_id=` returns, per court, `open_hours`, `booked_hours` and `utilization`. Only confirmed bookings count. The week runs Monday to Sunday in the **venue's own timezone**, and open hours account for daylight saving. With no `week`, each venue reports its own current week.
- Staff access uses a shared key in `X-Staff-Key` and **fails closed**: no configured key means `503`, a missing key `401`, a wrong key `403`. This answers question 8 only in part. Staff cancelling on a member's behalf is still not built.

**Concurrency details found while building**

- When two inserts collide, Postgres reports either an exclusion violation or, occasionally, a deadlock for the loser. Both are mapped to `409` through one helper (`services/conflicts.py`).
- Every operation that checks the member's limit first locks the member's row, before inserting bookings, so the lock order is the same everywhere.

**Testing**

- Tests build a fresh database named `<database>_test` on every run, so they do not depend on leftover state.

**Status of the open questions**

| #   | Question                                    | Status                                    |
| --- | ------------------------------------------- | ----------------------------------------- |
| 1   | Does a series count as one booking or many? | Assumed one; implemented                  |
| 2   | Do holds count towards the limit?           | Assumed yes; implemented                  |
| 3   | Are refunds paid out or only recorded?      | Recorded only                             |
| 4   | Hold duration                               | 10 minutes, configurable                  |
| 5   | Maximum booking length                      | 3 hours                                   |
| 6   | Maximum series length                       | 12 weeks                                  |
| 7   | One week taken: reject all or skip it?      | Reject all                                |
| 8   | Staff on behalf of members                  | Staff report only; staff cancel not built |
| 10  | What does utilization mean?                 | Booked hours over opening hours           |
| 12  | Limit per venue or overall?                 | Overall                                   |

Still open: 9 (public holidays), 11 (a closed court) and 13 (member timezone).

**Alternatives rejected during the build**

- A client-supplied idempotency key table for `POST /holds` and series: not needed because a hold and its booking are one row, and a repeated request is matched by its contents.
- A background job that expires holds: correctness must not depend on a job being alive. Expired holds are cleared inside the next overlapping request.
- Declaring the exclusion constraint on the SQLAlchemy model: its `ExcludeConstraint` has no type hints and strict mypy with no suppressions rejects it. The constraint lives in migration `0003`, and tests prove it exists.
