-- Court Booking Service: queries for looking at the data and the rules
--
--   docker compose exec -T db psql -U club -d court_booking < sql/inspect.sql
-- or run any query on its own in psql.

\echo '== Tables'
SELECT table_name FROM information_schema.tables
WHERE table_schema = 'public' ORDER BY table_name;

\echo '== The rule that makes double-booking impossible (an exclusion constraint)'
SELECT conname, pg_get_constraintdef(oid) AS definition
FROM pg_constraint WHERE contype = 'x';

\echo '== Every rule the database enforces (CHECK, UNIQUE, foreign keys)'
SELECT conrelid::regclass AS "table", conname,
       CASE contype WHEN 'c' THEN 'check' WHEN 'u' THEN 'unique' WHEN 'f' THEN 'foreign key'
                    WHEN 'x' THEN 'exclusion' WHEN 'p' THEN 'primary key' END AS kind,
       pg_get_constraintdef(oid) AS definition
FROM pg_constraint
WHERE connamespace = 'public'::regnamespace
ORDER BY conrelid::regclass::text, contype, conname;

\echo '== Venues, their courts and peak windows'
SELECT v.name AS venue, v.timezone, v.open_hour, v.close_hour, v.offpeak_rate, v.peak_rate,
       c.name AS court
FROM venues v LEFT JOIN courts c ON c.venue_id = v.id
ORDER BY v.name, c.name;

SELECT v.name AS venue, p.weekday, p.start_hour, p.end_hour
FROM peak_windows p JOIN venues v ON v.id = p.venue_id
ORDER BY v.name, p.weekday, p.start_hour;

\echo '== Members'
SELECT id, name FROM members ORDER BY name;

\echo '== Bookings, in venue-local time (a hold and a booking are the same row)'
SELECT b.status, c.name AS court, m.name AS member,
       b.start_at AT TIME ZONE v.timezone AS local_start,
       b.end_at   AT TIME ZONE v.timezone AS local_end,
       b.price, b.refund_amount, b.hold_expires_at, b.series_id IS NOT NULL AS in_series
FROM bookings b
JOIN courts c ON c.id = b.court_id
JOIN venues v ON v.id = c.venue_id
JOIN members m ON m.id = b.member_id
ORDER BY b.start_at;

\echo '== Live holds right now (unconfirmed and not yet expired)'
SELECT c.name AS court, m.name AS member, b.hold_expires_at
FROM bookings b JOIN courts c ON c.id = b.court_id JOIN members m ON m.id = b.member_id
WHERE b.status = 'held' AND b.hold_expires_at > now();

\echo '== Confirmed hours per court per ISO week (utilization, in plain SQL)'
SELECT to_char(b.start_at AT TIME ZONE v.timezone, 'IYYY-"W"IW') AS week,
       v.name AS venue, c.name AS court,
       sum(extract(epoch FROM (b.end_at - b.start_at)) / 3600) AS booked_hours
FROM bookings b
JOIN courts c ON c.id = b.court_id
JOIN venues v ON v.id = c.venue_id
WHERE b.status = 'confirmed'
GROUP BY 1, 2, 3
ORDER BY 1, 2, 3;
