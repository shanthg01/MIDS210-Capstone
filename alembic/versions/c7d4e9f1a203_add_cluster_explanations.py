"""add cluster explanation payloads

Revision ID: c7d4e9f1a203
Revises: b3f8e21a6c94
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "c7d4e9f1a203"
down_revision = "b3f8e21a6c94"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "player_archetypes",
        sa.Column("explanation", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "team_system_profiles",
        sa.Column("explanation", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("team_system_profiles", "explanation")
    op.drop_column("player_archetypes", "explanation")
