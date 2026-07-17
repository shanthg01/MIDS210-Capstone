"""program_events_tables

Adds:
  - coaches.tenure_end, coaches.departure_date
  - program_events table (news-monitoring agent generic event log)
  - program_events_review_queue table (sub-threshold / pending-review events)

These tables are the write target for the news-monitoring agent
(scripts/run_news_monitoring.py + notebooks/agents/news_monitor_agent_v2.ipynb)
so the agent feeds the existing pipeline rather than bypassing it.

Revision ID: 2547054ae5cb
Revises: f7c0a7862876
Create Date: 2026-07-11
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "2547054ae5cb"
down_revision = "f7c0a7862876"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # coaches.tenure_end / departure_date need table-owner on shared RDS (admin-only DDL).
    conn = op.get_bind()
    program_events = conn.execute(
        sa.text("SELECT to_regclass('public.program_events')")
    ).scalar()
    if program_events is None:
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

    review_queue = conn.execute(
        sa.text("SELECT to_regclass('public.program_events_review_queue')")
    ).scalar()
    if review_queue is None:
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

def downgrade() -> None:
    op.drop_index("ix_peq_school_date", table_name="program_events_review_queue")
    op.drop_table("program_events_review_queue")

    op.drop_index("ix_program_events_player", table_name="program_events")
    op.drop_index("ix_program_events_school_date", table_name="program_events")
    op.drop_table("program_events")
