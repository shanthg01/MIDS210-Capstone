"""add_hoopr_team_season_stats

Revision ID: c1e8f4a2b5d3
Revises: a3f7b2c9e1d0
Create Date: 2026-06-15

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c1e8f4a2b5d3'
down_revision: Union[str, Sequence[str], None] = 'a3f7b2c9e1d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'hoopr_team_season_stats',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('school_id', sa.Integer(), nullable=True),
        sa.Column('season', sa.SmallInteger(), nullable=False),
        sa.Column('espn_team_id', sa.String(length=20), nullable=True),
        sa.Column('espn_team_name', sa.String(length=200), nullable=False),
        # Tempo
        sa.Column('pbp_possession_sec', sa.Float(), nullable=True),
        # Shot type profile (sum to ~1.0)
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
        # Coverage metadata
        sa.Column('games_tracked', sa.SmallInteger(), nullable=False, server_default='0'),
        sa.Column('possessions_tracked', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['school_id'], ['schools.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('school_id', 'season', name='uq_hoopr_team_stats'),
    )
    op.create_index('ix_hoopr_team_stats_school_season', 'hoopr_team_season_stats', ['school_id', 'season'])


def downgrade() -> None:
    op.drop_index('ix_hoopr_team_stats_school_season', table_name='hoopr_team_season_stats')
    op.drop_table('hoopr_team_season_stats')
