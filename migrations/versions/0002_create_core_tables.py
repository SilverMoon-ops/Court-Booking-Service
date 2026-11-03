"""create core tables

Revision ID: 0002
Revises: 0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "members",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_members")),
    )
    op.create_table(
        "venues",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("open_hour", sa.SmallInteger(), nullable=False),
        sa.Column("close_hour", sa.SmallInteger(), nullable=False),
        sa.Column("offpeak_rate", sa.Integer(), nullable=False),
        sa.Column("peak_rate", sa.Integer(), nullable=False),
        sa.CheckConstraint("close_hour > open_hour", name=op.f("ck_venues_closes_after_open")),
        sa.CheckConstraint(
            "close_hour >= 1 AND close_hour <= 24", name=op.f("ck_venues_close_hour_range")
        ),
        sa.CheckConstraint("length(timezone) > 0", name=op.f("ck_venues_timezone_not_empty")),
        sa.CheckConstraint(
            "offpeak_rate >= 0 AND peak_rate >= 0", name=op.f("ck_venues_rates_not_negative")
        ),
        sa.CheckConstraint(
            "open_hour >= 0 AND open_hour <= 23", name=op.f("ck_venues_open_hour_range")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_venues")),
        sa.UniqueConstraint("name", name=op.f("uq_venues_name")),
    )
    op.create_table(
        "courts",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("venue_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.ForeignKeyConstraint(
            ["venue_id"], ["venues.id"], name=op.f("fk_courts_venue_id_venues")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_courts")),
        sa.UniqueConstraint("venue_id", "name", name=op.f("uq_courts_venue_id_name")),
    )
    op.create_index(op.f("ix_courts_venue_id"), "courts", ["venue_id"], unique=False)
    op.create_table(
        "peak_windows",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("venue_id", sa.Uuid(), nullable=False),
        sa.Column("weekday", sa.SmallInteger(), nullable=False),
        sa.Column("start_hour", sa.SmallInteger(), nullable=False),
        sa.Column("end_hour", sa.SmallInteger(), nullable=False),
        sa.CheckConstraint("end_hour > start_hour", name=op.f("ck_peak_windows_ends_after_start")),
        sa.CheckConstraint(
            "end_hour >= 1 AND end_hour <= 24", name=op.f("ck_peak_windows_end_hour_range")
        ),
        sa.CheckConstraint(
            "start_hour >= 0 AND start_hour <= 23", name=op.f("ck_peak_windows_start_hour_range")
        ),
        sa.CheckConstraint(
            "weekday >= 0 AND weekday <= 6", name=op.f("ck_peak_windows_weekday_range")
        ),
        sa.ForeignKeyConstraint(
            ["venue_id"], ["venues.id"], name=op.f("fk_peak_windows_venue_id_venues")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_peak_windows")),
    )
    op.create_index(op.f("ix_peak_windows_venue_id"), "peak_windows", ["venue_id"], unique=False)
    op.create_table(
        "recurring_series",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("member_id", sa.Uuid(), nullable=False),
        sa.Column("court_id", sa.Uuid(), nullable=False),
        sa.Column("weekday", sa.SmallInteger(), nullable=False),
        sa.Column("start_hour", sa.SmallInteger(), nullable=False),
        sa.Column("duration_hours", sa.SmallInteger(), nullable=False),
        sa.Column("first_date", sa.Date(), nullable=False),
        sa.Column("occurrences", sa.SmallInteger(), nullable=False),
        sa.CheckConstraint(
            "duration_hours >= 1 AND duration_hours <= 3",
            name=op.f("ck_recurring_series_duration_range"),
        ),
        sa.CheckConstraint(
            "occurrences >= 1 AND occurrences <= 12",
            name=op.f("ck_recurring_series_occurrences_range"),
        ),
        sa.CheckConstraint(
            "start_hour >= 0 AND start_hour <= 23",
            name=op.f("ck_recurring_series_start_hour_range"),
        ),
        sa.CheckConstraint(
            "weekday >= 0 AND weekday <= 6", name=op.f("ck_recurring_series_weekday_range")
        ),
        sa.ForeignKeyConstraint(
            ["court_id"], ["courts.id"], name=op.f("fk_recurring_series_court_id_courts")
        ),
        sa.ForeignKeyConstraint(
            ["member_id"], ["members.id"], name=op.f("fk_recurring_series_member_id_members")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_recurring_series")),
    )
    op.create_index(
        op.f("ix_recurring_series_member_id"), "recurring_series", ["member_id"], unique=False
    )
    op.create_table(
        "bookings",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("member_id", sa.Uuid(), nullable=False),
        sa.Column("court_id", sa.Uuid(), nullable=False),
        sa.Column("series_id", sa.Uuid(), nullable=True),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("price", sa.Integer(), nullable=False),
        sa.Column("refund_amount", sa.Integer(), nullable=True),
        sa.Column("hold_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(end_at - start_at) IN (interval '1 hour', interval '2 hours', interval '3 hours')",
            name=op.f("ck_bookings_duration_one_to_three_hours"),
        ),
        sa.CheckConstraint(
            "status <> 'held' OR hold_expires_at IS NOT NULL",
            name=op.f("ck_bookings_held_has_expiry"),
        ),
        sa.CheckConstraint(
            "status IN ('held', 'confirmed', 'cancelled', 'expired')",
            name=op.f("ck_bookings_status_valid"),
        ),
        sa.CheckConstraint("price >= 0", name=op.f("ck_bookings_price_not_negative")),
        sa.CheckConstraint(
            "refund_amount IS NULL OR (refund_amount >= 0 AND refund_amount <= price)",
            name=op.f("ck_bookings_refund_within_price"),
        ),
        sa.ForeignKeyConstraint(
            ["court_id"], ["courts.id"], name=op.f("fk_bookings_court_id_courts")
        ),
        sa.ForeignKeyConstraint(
            ["member_id"], ["members.id"], name=op.f("fk_bookings_member_id_members")
        ),
        sa.ForeignKeyConstraint(
            ["series_id"],
            ["recurring_series.id"],
            name=op.f("fk_bookings_series_id_recurring_series"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_bookings")),
    )
    op.create_index(
        "ix_bookings_member_status_start",
        "bookings",
        ["member_id", "status", "start_at"],
        unique=False,
    )
    op.create_index(op.f("ix_bookings_series_id"), "bookings", ["series_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_bookings_series_id"), table_name="bookings")
    op.drop_index("ix_bookings_member_status_start", table_name="bookings")
    op.drop_table("bookings")
    op.drop_index(op.f("ix_recurring_series_member_id"), table_name="recurring_series")
    op.drop_table("recurring_series")
    op.drop_index(op.f("ix_peak_windows_venue_id"), table_name="peak_windows")
    op.drop_table("peak_windows")
    op.drop_index(op.f("ix_courts_venue_id"), table_name="courts")
    op.drop_table("courts")
    op.drop_table("venues")
    op.drop_table("members")
