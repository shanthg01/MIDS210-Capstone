"""add is_admin to users

PR #64 review (Ajay): the news-monitoring agent run endpoint
(POST /api/agent/news-monitoring/run) had no authorization beyond a valid
JWT — any authenticated user could launch unlimited global Tavily/Gemini
runs. There was no admin/role concept anywhere in this codebase to gate on.
Adds the first one, defaulting False for every existing user; whoever
operates the agent's manual trigger needs this flipped true directly in the
DB (no self-service admin promotion — deliberately out of scope here).

Revision ID: d2f6a8c1b3e7
Revises: 328b1dc00017
Create Date: 2026-07-20
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "d2f6a8c1b3e7"
down_revision = "328b1dc00017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("is_admin", sa.Boolean(), nullable=False, server_default="false"),
    )


def downgrade() -> None:
    op.drop_column("users", "is_admin")
