"""expand_barttorvik_player_team_fields

Revision ID: 4d2553a387cc
Revises: 4f15ed03ddbf
Create Date: 2026-06-14 18:03:35.324193

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4d2553a387cc'
down_revision: Union[str, Sequence[str], None] = '4f15ed03ddbf'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # player_season_stats: barttorvik advanced fields previously labeled but not stored
    op.add_column('player_season_stats', sa.Column('offensive_rating', sa.Float(), nullable=True))
    op.add_column('player_season_stats', sa.Column('defensive_rating', sa.Float(), nullable=True))
    op.add_column('player_season_stats', sa.Column('efg_pct', sa.Float(), nullable=True))
    op.add_column('player_season_stats', sa.Column('off_reb_pct', sa.Float(), nullable=True))
    op.add_column('player_season_stats', sa.Column('def_reb_pct', sa.Float(), nullable=True))
    op.add_column('player_season_stats', sa.Column('tov_pct', sa.Float(), nullable=True))
    op.add_column('player_season_stats', sa.Column('free_throw_rate', sa.Float(), nullable=True))
    op.add_column('player_season_stats', sa.Column('ft_pct', sa.Float(), nullable=True))
    op.add_column('player_season_stats', sa.Column('fg2_pct', sa.Float(), nullable=True))
    op.add_column('player_season_stats', sa.Column('fg3_pct', sa.Float(), nullable=True))
    op.add_column('player_season_stats', sa.Column('block_pct', sa.Float(), nullable=True))
    op.add_column('player_season_stats', sa.Column('steal_pct', sa.Float(), nullable=True))
    op.add_column('player_season_stats', sa.Column('rim_pct', sa.Float(), nullable=True))
    op.add_column('player_season_stats', sa.Column('mid_pct', sa.Float(), nullable=True))
    op.add_column('player_season_stats', sa.Column('dunk_made', sa.SmallInteger(), nullable=True))
    op.add_column('player_season_stats', sa.Column('dunk_att', sa.SmallInteger(), nullable=True))
    op.add_column('player_season_stats', sa.Column('barttorvik_role', sa.String(length=50), nullable=True))
    op.add_column('player_season_stats', sa.Column('barttorvik_role_metric', sa.Float(), nullable=True))
    op.add_column('player_season_stats', sa.Column('rsci', sa.Float(), nullable=True))
    op.add_column('player_season_stats', sa.Column('birth_date', sa.Date(), nullable=True))
    # team_season_stats: defensive four factors (never written before due to teamname bug),
    # shooting splits, and team metadata from barttorvik team_results endpoint
    op.add_column('team_season_stats', sa.Column('efg_pct_def', sa.Float(), nullable=True))
    op.add_column('team_season_stats', sa.Column('tov_rate_def', sa.Float(), nullable=True))
    op.add_column('team_season_stats', sa.Column('drb_rate', sa.Float(), nullable=True))
    op.add_column('team_season_stats', sa.Column('ft_rate_def', sa.Float(), nullable=True))
    op.add_column('team_season_stats', sa.Column('three_pct_off', sa.Float(), nullable=True))
    op.add_column('team_season_stats', sa.Column('three_pct_def', sa.Float(), nullable=True))
    op.add_column('team_season_stats', sa.Column('two_pct_off', sa.Float(), nullable=True))
    op.add_column('team_season_stats', sa.Column('assist_rate_opp', sa.Float(), nullable=True))
    op.add_column('team_season_stats', sa.Column('national_rank', sa.SmallInteger(), nullable=True))
    op.add_column('team_season_stats', sa.Column('wab', sa.Float(), nullable=True))
    op.add_column('team_season_stats', sa.Column('sos', sa.Float(), nullable=True))
    op.add_column('team_season_stats', sa.Column('ncsos', sa.Float(), nullable=True))
    op.add_column('team_season_stats', sa.Column('conf_wins', sa.SmallInteger(), nullable=True))
    op.add_column('team_season_stats', sa.Column('conf_losses', sa.SmallInteger(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('team_season_stats', 'conf_losses')
    op.drop_column('team_season_stats', 'conf_wins')
    op.drop_column('team_season_stats', 'ncsos')
    op.drop_column('team_season_stats', 'sos')
    op.drop_column('team_season_stats', 'wab')
    op.drop_column('team_season_stats', 'national_rank')
    op.drop_column('team_season_stats', 'assist_rate_opp')
    op.drop_column('team_season_stats', 'two_pct_off')
    op.drop_column('team_season_stats', 'three_pct_def')
    op.drop_column('team_season_stats', 'three_pct_off')
    op.drop_column('team_season_stats', 'ft_rate_def')
    op.drop_column('team_season_stats', 'drb_rate')
    op.drop_column('team_season_stats', 'tov_rate_def')
    op.drop_column('team_season_stats', 'efg_pct_def')
    op.drop_column('player_season_stats', 'birth_date')
    op.drop_column('player_season_stats', 'rsci')
    op.drop_column('player_season_stats', 'barttorvik_role_metric')
    op.drop_column('player_season_stats', 'barttorvik_role')
    op.drop_column('player_season_stats', 'dunk_att')
    op.drop_column('player_season_stats', 'dunk_made')
    op.drop_column('player_season_stats', 'mid_pct')
    op.drop_column('player_season_stats', 'rim_pct')
    op.drop_column('player_season_stats', 'steal_pct')
    op.drop_column('player_season_stats', 'block_pct')
    op.drop_column('player_season_stats', 'fg3_pct')
    op.drop_column('player_season_stats', 'fg2_pct')
    op.drop_column('player_season_stats', 'ft_pct')
    op.drop_column('player_season_stats', 'free_throw_rate')
    op.drop_column('player_season_stats', 'tov_pct')
    op.drop_column('player_season_stats', 'def_reb_pct')
    op.drop_column('player_season_stats', 'off_reb_pct')
    op.drop_column('player_season_stats', 'efg_pct')
    op.drop_column('player_season_stats', 'defensive_rating')
    op.drop_column('player_season_stats', 'offensive_rating')
