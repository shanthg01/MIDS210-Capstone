"""add_season_to_player_team_fit_scores

Adds season (Integer) to player_team_fit_scores and rebuilds uq_fit_score
to include season, enabling multi-season historical fit score storage.
Existing rows are truncated — M3 notebook must be re-run to repopulate.

Revision ID: b5d2e9f4
Revises: d3b7e2a1c498
Create Date: 2026-06-18

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b5d2e9f4'
down_revision: Union[str, Sequence[str], None] = 'd3b7e2a1c498'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add season column (nullable first — table truncated below so it doesn't matter)
    op.add_column(
        'player_team_fit_scores',
        sa.Column('season', sa.Integer(), nullable=True),
    )
    # Truncate stale rows — existing records have no season and violate new constraint
    op.execute('TRUNCATE TABLE player_team_fit_scores RESTART IDENTITY')
    # Now season can be NOT NULL (table is empty)
    op.alter_column('player_team_fit_scores', 'season', nullable=False)
    # Drop the old two-column unique constraint
    op.drop_constraint('uq_fit_score', 'player_team_fit_scores', type_='unique')
    # New constraint includes season — allows same player×school across different seasons
    op.create_unique_constraint(
        'uq_fit_score',
        'player_team_fit_scores',
        ['player_id', 'school_id', 'season'],
    )


def downgrade() -> None:
    op.drop_constraint('uq_fit_score', 'player_team_fit_scores', type_='unique')
    op.create_unique_constraint('uq_fit_score', 'player_team_fit_scores', ['player_id', 'school_id'])
    op.drop_column('player_team_fit_scores', 'season')
