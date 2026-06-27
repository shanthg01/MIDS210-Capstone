"""add_playing_time_projections

Revision ID: a8c4f2d9b631
Revises: 942e107c5382
Create Date: 2026-06-27

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "a8c4f2d9b631"
down_revision: Union[str, Sequence[str], None] = "942e107c5382"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "playing_time_projections",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("player_id", sa.BigInteger(), nullable=False),
        sa.Column("school_id", sa.Integer(), nullable=False),
        sa.Column("season", sa.SmallInteger(), nullable=False),
        sa.Column("roster_snapshot_id", sa.Integer(), nullable=True),
        sa.Column("expected_minutes", sa.Float(), nullable=False),
        sa.Column("expected_minutes_share", sa.Float(), nullable=False),
        sa.Column("minutes_ci_lower", sa.Float(), nullable=False),
        sa.Column("minutes_ci_upper", sa.Float(), nullable=False),
        sa.Column("expected_usage", sa.Float(), nullable=False),
        sa.Column("usage_role", sa.String(length=40), nullable=False),
        sa.Column("usage_role_confidence", sa.Float(), nullable=False),
        sa.Column("starter_probability", sa.Float(), nullable=True),
        sa.Column("rotation_probability", sa.Float(), nullable=True),
        sa.Column("displaced_minutes", postgresql.JSONB(), nullable=True),
        sa.Column("opportunity_drivers", postgresql.JSONB(), nullable=True),
        sa.Column("data_quality_flags", postgresql.JSONB(), nullable=True),
        sa.Column("scenario_overrides", postgresql.JSONB(), nullable=True),
        sa.Column("role_fit", sa.Float(), nullable=False),
        sa.Column("model_version", sa.String(length=40), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["player_id"], ["players.id"]),
        sa.ForeignKeyConstraint(["school_id"], ["schools.id"]),
        sa.ForeignKeyConstraint(["roster_snapshot_id"], ["roster_snapshots.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "player_id",
            "school_id",
            "season",
            "model_version",
            name="uq_playing_time_projection",
        ),
    )
    op.create_index(
        "ix_playing_time_player_season",
        "playing_time_projections",
        ["player_id", "season"],
    )
    op.create_index(
        "ix_playing_time_school_season",
        "playing_time_projections",
        ["school_id", "season"],
    )
    op.create_index(
        "ix_playing_time_season_role_fit",
        "playing_time_projections",
        ["season", "role_fit"],
    )


def downgrade() -> None:
    op.drop_index("ix_playing_time_season_role_fit", table_name="playing_time_projections")
    op.drop_index("ix_playing_time_school_season", table_name="playing_time_projections")
    op.drop_index("ix_playing_time_player_season", table_name="playing_time_projections")
    op.drop_table("playing_time_projections")
