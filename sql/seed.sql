-- Court Booking Service: demo data as plain SQL
--
-- One venue, two courts, two members, and weekday-evening peak pricing.
-- Safe to run more than once (it never creates duplicates).
-- It does the same as `python -m app.seed`. Neither is needed any more: staff can create all
-- of this through the API (POST /venues, /venues/{id}/courts, /members).
--
-- Apply (after sql/schema.sql or `alembic upgrade head`):
--   psql -U club -d court_booking -f sql/seed.sql
--   docker compose exec -T db psql -U club -d court_booking < sql/seed.sql

-- A venue open 06:00-22:00 India time. Rates are in paise per hour: 20000 = Rs 200.00.
INSERT INTO venues (name, timezone, open_hour, close_hour, offpeak_rate, peak_rate)
VALUES ('Demo Venue', 'Asia/Kolkata', 6, 22, 20000, 40000)
ON CONFLICT (name) DO NOTHING;

-- Peak pricing Monday to Friday (weekday 0 = Monday), 18:00 to 22:00 venue time.
INSERT INTO peak_windows (venue_id, weekday, start_hour, end_hour)
SELECT v.id, d, 18, 22
FROM venues v
CROSS JOIN generate_series(0, 4) AS d
WHERE v.name = 'Demo Venue'
  AND NOT EXISTS (SELECT 1 FROM peak_windows p WHERE p.venue_id = v.id);

INSERT INTO courts (venue_id, name)
SELECT v.id, c.name
FROM venues v
CROSS JOIN (VALUES ('Court 1'), ('Court 2')) AS c(name)
WHERE v.name = 'Demo Venue'
ON CONFLICT (venue_id, name) DO NOTHING;

INSERT INTO members (name)
SELECT m.name
FROM (VALUES ('Asha'), ('Ben')) AS m(name)
WHERE NOT EXISTS (SELECT 1 FROM members x WHERE x.name = m.name);

-- The ids to use with the API (court_id in bodies, member id as the X-Member-Id header).
SELECT 'court' AS kind, c.name, c.id FROM courts c JOIN venues v ON v.id = c.venue_id
WHERE v.name = 'Demo Venue'
UNION ALL
SELECT 'member', name, id FROM members WHERE name IN ('Asha', 'Ben')
ORDER BY 1, 2;
