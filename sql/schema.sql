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

BEGIN;

CREATE TABLE alembic_version (
    version_num VARCHAR(32) NOT NULL, 
    CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)
);

-- Running upgrade  -> 0001

CREATE EXTENSION IF NOT EXISTS btree_gist;

INSERT INTO alembic_version (version_num) VALUES ('0001');

-- Running upgrade 0001 -> 0002

CREATE TABLE members (
    id UUID DEFAULT gen_random_uuid() NOT NULL, 
    name VARCHAR(100) NOT NULL, 
    CONSTRAINT pk_members PRIMARY KEY (id)
);

CREATE TABLE venues (
    id UUID DEFAULT gen_random_uuid() NOT NULL, 
    name VARCHAR(100) NOT NULL, 
    timezone VARCHAR(64) NOT NULL, 
    open_hour SMALLINT NOT NULL, 
    close_hour SMALLINT NOT NULL, 
    offpeak_rate INTEGER NOT NULL, 
    peak_rate INTEGER NOT NULL, 
    CONSTRAINT pk_venues PRIMARY KEY (id), 
    CONSTRAINT ck_venues_closes_after_open CHECK (close_hour > open_hour), 
    CONSTRAINT ck_venues_close_hour_range CHECK (close_hour >= 1 AND close_hour <= 24), 
    CONSTRAINT ck_venues_timezone_not_empty CHECK (length(timezone) > 0), 
    CONSTRAINT ck_venues_rates_not_negative CHECK (offpeak_rate >= 0 AND peak_rate >= 0), 
    CONSTRAINT ck_venues_open_hour_range CHECK (open_hour >= 0 AND open_hour <= 23), 
    CONSTRAINT uq_venues_name UNIQUE (name)
);

CREATE TABLE courts (
    id UUID DEFAULT gen_random_uuid() NOT NULL, 
    venue_id UUID NOT NULL, 
    name VARCHAR(100) NOT NULL, 
    CONSTRAINT pk_courts PRIMARY KEY (id), 
    CONSTRAINT fk_courts_venue_id_venues FOREIGN KEY(venue_id) REFERENCES venues (id), 
    CONSTRAINT uq_courts_venue_id_name UNIQUE (venue_id, name)
);

CREATE INDEX ix_courts_venue_id ON courts (venue_id);

CREATE TABLE peak_windows (
    id UUID DEFAULT gen_random_uuid() NOT NULL, 
    venue_id UUID NOT NULL, 
    weekday SMALLINT NOT NULL, 
    start_hour SMALLINT NOT NULL, 
    end_hour SMALLINT NOT NULL, 
    CONSTRAINT pk_peak_windows PRIMARY KEY (id), 
    CONSTRAINT ck_peak_windows_ends_after_start CHECK (end_hour > start_hour), 
    CONSTRAINT ck_peak_windows_end_hour_range CHECK (end_hour >= 1 AND end_hour <= 24), 
    CONSTRAINT ck_peak_windows_start_hour_range CHECK (start_hour >= 0 AND start_hour <= 23), 
    CONSTRAINT ck_peak_windows_weekday_range CHECK (weekday >= 0 AND weekday <= 6), 
    CONSTRAINT fk_peak_windows_venue_id_venues FOREIGN KEY(venue_id) REFERENCES venues (id)
);

CREATE INDEX ix_peak_windows_venue_id ON peak_windows (venue_id);

CREATE TABLE recurring_series (
    id UUID DEFAULT gen_random_uuid() NOT NULL, 
    member_id UUID NOT NULL, 
    court_id UUID NOT NULL, 
    weekday SMALLINT NOT NULL, 
    start_hour SMALLINT NOT NULL, 
    duration_hours SMALLINT NOT NULL, 
    first_date DATE NOT NULL, 
    occurrences SMALLINT NOT NULL, 
    CONSTRAINT pk_recurring_series PRIMARY KEY (id), 
    CONSTRAINT ck_recurring_series_duration_range CHECK (duration_hours >= 1 AND duration_hours <= 3), 
    CONSTRAINT ck_recurring_series_occurrences_range CHECK (occurrences >= 1 AND occurrences <= 12), 
    CONSTRAINT ck_recurring_series_start_hour_range CHECK (start_hour >= 0 AND start_hour <= 23), 
    CONSTRAINT ck_recurring_series_weekday_range CHECK (weekday >= 0 AND weekday <= 6), 
    CONSTRAINT fk_recurring_series_court_id_courts FOREIGN KEY(court_id) REFERENCES courts (id), 
    CONSTRAINT fk_recurring_series_member_id_members FOREIGN KEY(member_id) REFERENCES members (id)
);

CREATE INDEX ix_recurring_series_member_id ON recurring_series (member_id);

CREATE TABLE bookings (
    id UUID DEFAULT gen_random_uuid() NOT NULL, 
    member_id UUID NOT NULL, 
    court_id UUID NOT NULL, 
    series_id UUID, 
    start_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    end_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    status VARCHAR(16) NOT NULL, 
    price INTEGER NOT NULL, 
    refund_amount INTEGER, 
    hold_expires_at TIMESTAMP WITH TIME ZONE, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    confirmed_at TIMESTAMP WITH TIME ZONE, 
    cancelled_at TIMESTAMP WITH TIME ZONE, 
    CONSTRAINT pk_bookings PRIMARY KEY (id), 
    CONSTRAINT ck_bookings_duration_one_to_three_hours CHECK ((end_at - start_at) IN (interval '1 hour', interval '2 hours', interval '3 hours')), 
    CONSTRAINT ck_bookings_held_has_expiry CHECK (status <> 'held' OR hold_expires_at IS NOT NULL), 
    CONSTRAINT ck_bookings_status_valid CHECK (status IN ('held', 'confirmed', 'cancelled', 'expired')), 
    CONSTRAINT ck_bookings_price_not_negative CHECK (price >= 0), 
    CONSTRAINT ck_bookings_refund_within_price CHECK (refund_amount IS NULL OR (refund_amount >= 0 AND refund_amount <= price)), 
    CONSTRAINT fk_bookings_court_id_courts FOREIGN KEY(court_id) REFERENCES courts (id), 
    CONSTRAINT fk_bookings_member_id_members FOREIGN KEY(member_id) REFERENCES members (id), 
    CONSTRAINT fk_bookings_series_id_recurring_series FOREIGN KEY(series_id) REFERENCES recurring_series (id)
);

CREATE INDEX ix_bookings_member_status_start ON bookings (member_id, status, start_at);

CREATE INDEX ix_bookings_series_id ON bookings (series_id);

UPDATE alembic_version SET version_num='0002' WHERE alembic_version.version_num = '0001';

-- Running upgrade 0002 -> 0003

ALTER TABLE bookings
        ADD CONSTRAINT ex_bookings_no_overlap
        EXCLUDE USING gist (
            court_id WITH =,
            tstzrange(start_at, end_at, '[)') WITH &&
        )
        WHERE (status IN ('held', 'confirmed'));

UPDATE alembic_version SET version_num='0003' WHERE alembic_version.version_num = '0002';

COMMIT;

