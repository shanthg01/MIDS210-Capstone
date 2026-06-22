"""add_is_portal_candidate_to_fit_scores

Revision ID: c7f1a9d3e652
Revises: b9c3f7a2d514
Create Date: 2026-06-21

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c7f1a9d3e652'
down_revision: Union[str, Sequence[str], None] = 'b9c3f7a2d514'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'player_team_fit_scores',
        sa.Column('is_portal_candidate', sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index(
        'ix_fit_scores_school_season_candidate',
        'player_team_fit_scores',
        ['school_id', 'season', 'is_portal_candidate'],
    )


def downgrade() -> None:
    op.drop_index('ix_fit_scores_school_season_candidate', table_name='player_team_fit_scores')
    op.drop_column('player_team_fit_scores', 'is_portal_candidate')
