"""add calibrated fit scores and team impact preferences

Revision ID: a6c1f9e2d4b8
Revises: 328b1dc00017
Create Date: 2026-07-19
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a6c1f9e2d4b8"
down_revision: Union[str, Sequence[str], None] = "328b1dc00017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "player_team_fit_scores", sa.Column("calibrated_scheme_fit", sa.Float(), nullable=True)
    )
    op.add_column(
        "player_team_fit_scores", sa.Column("calibrated_gap_match", sa.Float(), nullable=True)
    )
    op.add_column(
        "player_team_fit_scores", sa.Column("calibrated_role_fit", sa.Float(), nullable=True)
    )
    op.add_column("player_team_fit_scores", sa.Column("team_impact_fit", sa.Float(), nullable=True))
    op.add_column(
        "player_team_fit_scores", sa.Column("overall_confidence", sa.Float(), nullable=True)
    )
    op.add_column(
        "player_team_fit_scores",
        sa.Column("component_confidences", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "player_team_fit_scores",
        sa.Column("data_quality_flags", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "player_team_fit_scores", sa.Column("calibration_version", sa.String(40), nullable=True)
    )
    op.add_column(
        "player_team_fit_scores",
        sa.Column("weight_team_impact", sa.Float(), nullable=False, server_default=sa.text("0.20")),
    )

    # Program Fit was descoped. Preserve existing user intent by carrying the
    # fourth slider's value forward under the real Team Rating component.
    op.alter_column("user_preferences", "weight_program", new_column_name="weight_team_impact")
    op.alter_column(
        "user_preferences", "importance_program_fit", new_column_name="importance_team_impact"
    )
    op.alter_column(
        "user_preference_profiles", "weight_program", new_column_name="weight_team_impact"
    )
    op.alter_column(
        "user_preference_profiles",
        "importance_program_fit",
        new_column_name="importance_team_impact",
    )
    for table in ("user_preferences", "user_preference_profiles"):
        op.alter_column(table, "weight_gap", server_default=sa.text("0.30"))
        op.alter_column(table, "weight_scheme", server_default=sa.text("0.25"))
        op.alter_column(table, "weight_team_impact", server_default=sa.text("0.20"))


def downgrade() -> None:
    for table in ("user_preferences", "user_preference_profiles"):
        op.alter_column(table, "weight_gap", server_default=sa.text("0.20"))
        op.alter_column(table, "weight_scheme", server_default=sa.text("0.30"))
        op.alter_column(table, "weight_team_impact", server_default=sa.text("0.25"))
    op.alter_column(
        "user_preference_profiles",
        "importance_team_impact",
        new_column_name="importance_program_fit",
    )
    op.alter_column(
        "user_preference_profiles", "weight_team_impact", new_column_name="weight_program"
    )
    op.alter_column(
        "user_preferences", "importance_team_impact", new_column_name="importance_program_fit"
    )
    op.alter_column("user_preferences", "weight_team_impact", new_column_name="weight_program")

    op.drop_column("player_team_fit_scores", "weight_team_impact")
    op.drop_column("player_team_fit_scores", "calibration_version")
    op.drop_column("player_team_fit_scores", "data_quality_flags")
    op.drop_column("player_team_fit_scores", "component_confidences")
    op.drop_column("player_team_fit_scores", "overall_confidence")
    op.drop_column("player_team_fit_scores", "team_impact_fit")
    op.drop_column("player_team_fit_scores", "calibrated_role_fit")
    op.drop_column("player_team_fit_scores", "calibrated_gap_match")
    op.drop_column("player_team_fit_scores", "calibrated_scheme_fit")
