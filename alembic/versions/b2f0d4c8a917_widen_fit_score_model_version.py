"""widen_fit_score_model_version

Revision ID: b2f0d4c8a917
Revises: a8c4f2d9b631
Create Date: 2026-06-27

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b2f0d4c8a917"
down_revision: Union[str, Sequence[str], None] = "a8c4f2d9b631"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "player_team_fit_scores",
        "model_version",
        existing_type=sa.String(length=20),
        type_=sa.String(length=40),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "player_team_fit_scores",
        "model_version",
        existing_type=sa.String(length=40),
        type_=sa.String(length=20),
        existing_nullable=False,
    )
