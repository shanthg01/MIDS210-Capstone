"""add playing-time explanation payload

Revision ID: e5a8c2d4f901
Revises: 40beacdccf1e
Create Date: 2026-07-17
"""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "e5a8c2d4f901"
down_revision = "40beacdccf1e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "playing_time_projections",
        sa.Column("explanation", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("playing_time_projections", "explanation")
