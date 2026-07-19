"""program_events_dedup_indexes

News-agent schema catch-up for DBs on the 40beacdccf1e line that skipped the
2547054ae5cb branch: creates program_events tables, then partial unique indexes
so INSERT ... ON CONFLICT DO NOTHING is valid for idempotent re-runs.

Revision ID: c9e2a1f4b8d3
Revises: 40beacdccf1e
Create Date: 2026-07-16
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c9e2a1f4b8d3"
down_revision = "40beacdccf1e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # coaches.tenure_end / departure_date require table-owner privileges on shared RDS.
    # portalpoint_app cannot ALTER coaches; an admin applies 2547054ae5cb coach DDL separately.

    conn = op.get_bind()
    if not conn.dialect.has_table(conn, "program_events"):
        op.create_table(
            "program_events",
            sa.Column("id", sa.BigInteger(), nullable=False, autoincrement=True),
            sa.Column("event_type", sa.String(50), nullable=False),
            sa.Column("school_id", sa.Integer(), sa.ForeignKey("schools.id"), nullable=True),
            sa.Column("player_id", sa.BigInteger(), sa.ForeignKey("players.id"), nullable=True),
            sa.Column("coach_id", sa.Integer(), sa.ForeignKey("coaches.id"), nullable=True),
            sa.Column("event_date", sa.Date(), nullable=True),
            sa.Column("source", sa.String(50), nullable=False),
            sa.Column("source_url", sa.Text(), nullable=True),
            sa.Column("raw_text", sa.Text(), nullable=True),
            sa.Column("confidence", sa.Float(), nullable=True),
            sa.Column("match_status", sa.String(20), nullable=False, server_default="matched"),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_program_events_school_date",
            "program_events",
            ["school_id", "event_date"],
        )
        op.create_index("ix_program_events_player", "program_events", ["player_id"])

    if not conn.dialect.has_table(conn, "program_events_review_queue"):
        op.create_table(
            "program_events_review_queue",
            sa.Column("id", sa.BigInteger(), nullable=False, autoincrement=True),
            sa.Column("event_type", sa.String(50), nullable=False),
            sa.Column("school_id", sa.Integer(), sa.ForeignKey("schools.id"), nullable=True),
            sa.Column("player_id", sa.BigInteger(), sa.ForeignKey("players.id"), nullable=True),
            sa.Column("coach_id", sa.Integer(), sa.ForeignKey("coaches.id"), nullable=True),
            sa.Column("event_date", sa.Date(), nullable=True),
            sa.Column("source", sa.String(50), nullable=False),
            sa.Column("source_url", sa.Text(), nullable=True),
            sa.Column("raw_text", sa.Text(), nullable=True),
            sa.Column("confidence", sa.Float(), nullable=True),
            sa.Column("review_reason", sa.Text(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_peq_school_date",
            "program_events_review_queue",
            ["school_id", "event_date"],
        )

    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_program_events_transfer_entry
        ON program_events (event_type, source, player_id, event_date)
        WHERE event_type = 'transfer_entry' AND player_id IS NOT NULL
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_program_events_coach_departed
        ON program_events (event_type, source, school_id, event_date)
        WHERE event_type = 'coach_departed' AND school_id IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_program_events_coach_departed")
    op.execute("DROP INDEX IF EXISTS uq_program_events_transfer_entry")

    conn = op.get_bind()
    if conn.dialect.has_table(conn, "program_events_review_queue"):
        op.drop_index("ix_peq_school_date", table_name="program_events_review_queue")
        op.drop_table("program_events_review_queue")

    if conn.dialect.has_table(conn, "program_events"):
        op.drop_index("ix_program_events_player", table_name="program_events")
        op.drop_index("ix_program_events_school_date", table_name="program_events")
        op.drop_table("program_events")
