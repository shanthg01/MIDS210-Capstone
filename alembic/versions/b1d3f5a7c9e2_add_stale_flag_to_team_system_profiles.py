"""add stale_flag and stale_reason to team_system_profiles

Gate 7 from news-monitoring agent (PR #50): coach_departure tool sets
stale_flag=True on the affected school/season row so fit_score_service
can surface a staleness warning to the coaching staff.

Revision ID: b1d3f5a7c9e2
Revises: c3a9e1f5b847
Create Date: 2026-07-12
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "b1d3f5a7c9e2"
down_revision = "c3a9e1f5b847"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "team_system_profiles",
        sa.Column("stale_flag", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "team_system_profiles",
        sa.Column("stale_reason", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("team_system_profiles", "stale_reason")
    op.drop_column("team_system_profiles", "stale_flag")
