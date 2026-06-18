"""add_min_pct_to_player_season_stats

Adds min_pct (Float) to player_season_stats: percentage of team minutes played
sourced from barttorvik's min_per column (0-100 scale). Replaces the broken
minutes_per_game field (which stored 0.0 for ~99.6% of rows due to a null
fallback on the rarely-populated min_per_game BART column).

Revision ID: d3b7e2a1c498
Revises: f2a9c3d7e841
Create Date: 2026-06-18

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd3b7e2a1c498'
down_revision: Union[str, Sequence[str], None] = 'f2a9c3d7e841'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'player_season_stats',
        sa.Column('min_pct', sa.Float(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('player_season_stats', 'min_pct')
