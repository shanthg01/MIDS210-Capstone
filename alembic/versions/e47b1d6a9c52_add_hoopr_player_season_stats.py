"""add_hoopr_player_season_stats

Revision ID: e47b1d6a9c52
Revises: c1e8f4a2b5d3
Create Date: 2026-06-16

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e47b1d6a9c52'
down_revision: Union[str, Sequence[str], None] = 'c1e8f4a2b5d3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'hoopr_player_season_stats',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('player_id', sa.Integer(), nullable=True),
        sa.Column('school_id', sa.Integer(), nullable=True),
        sa.Column('season', sa.SmallInteger(), nullable=False),
        sa.Column('espn_athlete_id', sa.String(length=20), nullable=False),
        sa.Column('raw_display_name', sa.String(length=200), nullable=False),
        sa.Column('espn_team_name', sa.String(length=200), nullable=False),
        sa.Column('match_confidence', sa.Float(), nullable=True),
        # Shot type profile (mirrors hoopr_team_season_stats)
        sa.Column('pbp_rim_pct', sa.Float(), nullable=True),
        sa.Column('pbp_three_pct', sa.Float(), nullable=True),
        sa.Column('pbp_mid_pct', sa.Float(), nullable=True),
        # Spatial shot zones (5-zone half-court; sum to ~1.0)
        sa.Column('pbp_zone1_restricted_pct', sa.Float(), nullable=True),
        sa.Column('pbp_zone2_mid_pct', sa.Float(), nullable=True),
        sa.Column('pbp_zone3_corner3_pct', sa.Float(), nullable=True),
        sa.Column('pbp_zone4_straight3_pct', sa.Float(), nullable=True),
        sa.Column('pbp_zone5_wing3_pct', sa.Float(), nullable=True),
        # Possession outcome rates
        sa.Column('pbp_turnover_rate', sa.Float(), nullable=True),
        sa.Column('pbp_transition_rate', sa.Float(), nullable=True),
        # Player-only additions
        sa.Column('pbp_clutch_ts_pct', sa.Float(), nullable=True),
        sa.Column('pbp_assist_rate', sa.Float(), nullable=True),
        # Coverage metadata
        sa.Column('shot_attempts_tracked', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('games_tracked', sa.SmallInteger(), nullable=False, server_default='0'),
        sa.Column('possessions_tracked', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['player_id'], ['players.id']),
        sa.ForeignKeyConstraint(['school_id'], ['schools.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('espn_athlete_id', 'season', name='uq_hoopr_player_stats'),
    )
    op.create_index('ix_hoopr_player_stats_player_season', 'hoopr_player_season_stats', ['player_id', 'season'])


def downgrade() -> None:
    op.drop_index('ix_hoopr_player_stats_player_season', table_name='hoopr_player_season_stats')
    op.drop_table('hoopr_player_season_stats')
