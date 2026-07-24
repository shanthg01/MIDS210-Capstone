"""add calibrated fit scores

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
    op.add_column(
        "player_team_fit_scores", sa.Column("calibrated_program_fit", sa.Float(), nullable=True)
    )
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

    # Program Fit stays descoped and stays named program_fit/weight_program —
    # fit-cal-v1 only changes the shared weight split (scheme 30%->25%,
    # gap 20%->30%, program 25%->20%), not the component identity.
    for table in ("user_preferences", "user_preference_profiles"):
        op.alter_column(table, "weight_gap", server_default=sa.text("0.30"))
        op.alter_column(table, "weight_scheme", server_default=sa.text("0.25"))
        op.alter_column(table, "weight_program", server_default=sa.text("0.20"))


def downgrade() -> None:
    for table in ("user_preferences", "user_preference_profiles"):
        op.alter_column(table, "weight_gap", server_default=sa.text("0.20"))
        op.alter_column(table, "weight_scheme", server_default=sa.text("0.30"))
        op.alter_column(table, "weight_program", server_default=sa.text("0.25"))

    op.drop_column("player_team_fit_scores", "calibration_version")
    op.drop_column("player_team_fit_scores", "data_quality_flags")
    op.drop_column("player_team_fit_scores", "component_confidences")
    op.drop_column("player_team_fit_scores", "overall_confidence")
    op.drop_column("player_team_fit_scores", "calibrated_program_fit")
    op.drop_column("player_team_fit_scores", "calibrated_role_fit")
    op.drop_column("player_team_fit_scores", "calibrated_gap_match")
    op.drop_column("player_team_fit_scores", "calibrated_scheme_fit")
