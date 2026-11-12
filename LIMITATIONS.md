# LIMITATIONS.md: how the system can still fail

This lists what the system does not do, and the ways it can still go wrong, honestly.
What each guarantee does and does not cover is stated precisely.

## What the guarantees do and do not cover

- **No double-booking** is enforced by the database itself, so it holds for every writer that
  inserts into `bookings`, not only this API. It depends on the constraint existing. See
  "Constraint is only in the migration" below.
- **Safe retries** cover `confirm`, `cancel`, `POST /holds` and `POST /recurring-bookings`.
  They do not cover a client that sends a *different* request.
- **No partial state** holds because each request is one database transaction. It does **not**
  cover side effects outside the database. There are none today (no payments, no emails).
  Adding any needs an outbox, or they could happen without the booking.

## Security

- **No real authentication.** Anyone who knows or guesses a member id can act as that member by
  sending `X-Member-Id`. Member ids are random UUIDs, which makes guessing hard, but the header is
  not proof of identity.
- **Staff access is one shared secret.** Anyone with `STAFF_API_KEY` is staff. There are no
  individual accounts, no audit trail of who looked at what, and no rotation. The key is sent in a
  header, so it must only be used over HTTPS, which this project does not set up.
- **No rate limiting or abuse protection.** A client can send requests as fast as the server allows.

## Time and clocks

- **Expiry and refunds use the application server's clock.** With several app servers, small
  clock differences could make a hold expire a little early or late, or move a refund across the
  24-hour boundary. The fix would be to take "now" from the database.
- **Daylight saving edge cases.** A weekly series that lands on a local hour that does not exist is
  refused as a whole. A local hour that happens twice (clocks going back) uses its first occurrence.
  Changing a venue's timezone later is not handled.
- **Bookings are hour-aligned, 1 to 3 hours, within one local day.** Nothing can cross midnight,
  even at a venue open all night.

## Data and consistency

- **Expired holds stay `held` in the table** until a later request touches an overlapping slot.
  Every query compares the expiry time, so behaviour is correct, but anyone reading the raw table
  sees stale `held` rows. A cleanup job would tidy this but is not needed for correctness.
- **The exclusion constraint is only in migration `0003`.** It is not declared on the SQLAlchemy
  model, because SQLAlchemy's `ExcludeConstraint` has no type hints and strict mypy with no
  suppressions rejects it. If someone built the schema from the models instead of the migrations,
  the constraint would be missing and double-booking would become possible. A test would notice, but
  only if run against that database.
- **Deadlocks are reported as `409`, not retried.** Under heavy contention Postgres may stop one
  transaction with a deadlock error. The client receives `slot_unavailable` and can try again. The
  server does not retry on its behalf.
- **The retry check for recurring bookings is a heuristic.** It matches an identical request from
  the same member. It is not an idempotency key, so a member cannot deliberately create two
  identical series while the first is still confirmed.
- **Price is fixed when the hold is made.** If a venue changes its rates before the member
  confirms, the hold keeps its original price.

## Product gaps

- **No endpoints to manage venues, courts, members or peak windows.** They are created through
  `app.seed` or SQL. Real use needs administration endpoints.
- **Refunds are recorded, not paid.** `refund_amount` says what is owed. Nothing moves money.
- **Staff cannot cancel or create bookings on a member's behalf.**
- **A series cannot be cancelled in one call.** Each week is cancelled separately.
- **A recurring series counts as one booking.** A member can therefore hold up to 12 weekly
  bookings plus two more. If the club wants a stricter rule, it is one function to change.
- **No "my bookings" endpoint and no pagination.** The availability endpoint returns one day.
- **Availability is a snapshot.** A slot shown as free can be taken a moment later. Only the hold
  request decides.

## Reporting

- **Utilization counts confirmed bookings over open hours.** It includes future confirmed
  bookings, so a future week shows planned use. Holds, cancellations and expired holds never count.
- **It is computed on request.** The bookings of the week are read into memory, which is fine for
  a club but would need a database aggregate for a very large dataset.

## Operations and scale

- **One Postgres instance.** No replicas, no backups, and data lives in a Docker volume.
- **Default connection pool is small** (5, with 10 extra). Under a large burst, requests wait for a
  connection and can time out.
- **No structured logging, metrics or alerts.**
- **Concurrency tests are statistical.** They fire many simultaneous requests and were repeated
  many times, and each important rule was shown to fail when broken. That is strong evidence, not a
  formal proof. The double-booking guarantee itself rests on the database constraint, not on the
  tests.
