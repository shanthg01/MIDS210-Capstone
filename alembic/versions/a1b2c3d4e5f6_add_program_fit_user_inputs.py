"""add program_fit_user_inputs table

Lets a user manually grade the qualitative "off the court" fit of one
player x school pair (0-100), separate from the still-descoped Program Fit
calculator (docs/models/program_fit_model_plan.md). Per-user, substitutes
into personalized_fit only — player_team_fit_scores.program_fit stays the
shared neutral 50.0 placeholder.

Revision ID: a1b2c3d4e5f6
Revises: b3f8e21a6c94
Create Date: 2026-07-21
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "b3f8e21a6c94"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "program_fit_user_inputs",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("player_id", sa.BigInteger(), sa.ForeignKey("players.id"), nullable=False),
        sa.Column("school_id", sa.Integer(), sa.ForeignKey("schools.id"), nullable=False),
        sa.Column("season", sa.Integer(), nullable=False),
        sa.Column("qualitative_score", sa.Float(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint(
            "user_id", "player_id", "school_id", "season", name="uq_program_fit_user_input"
        ),
    )
    # No separate index needed — the unique constraint above already creates
    # one covering (user_id, player_id, school_id, season), the only lookup shape used.


def downgrade() -> None:
    op.drop_table("program_fit_user_inputs")
