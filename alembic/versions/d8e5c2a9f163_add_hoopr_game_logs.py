"""add_hoopr_game_logs

Revision ID: d8e5c2a9f163
Revises: 7a3e2d1c9b44
Create Date: 2026-06-20

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd8e5c2a9f163'
down_revision: Union[str, Sequence[str], None] = '7a3e2d1c9b44'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'hoopr_games',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('espn_game_id', sa.String(length=20), nullable=False),
        sa.Column('season', sa.SmallInteger(), nullable=False),
        sa.Column('game_date', sa.Date(), nullable=True),
        sa.Column('home_school_id', sa.Integer(), nullable=True),
        sa.Column('away_school_id', sa.Integer(), nullable=True),
        sa.Column('home_espn_team_id', sa.String(length=20), nullable=True),
        sa.Column('away_espn_team_id', sa.String(length=20), nullable=True),
        sa.Column('home_score', sa.Integer(), nullable=True),
        sa.Column('away_score', sa.Integer(), nullable=True),
        sa.Column('neutral_site', sa.Boolean(), nullable=True),
        sa.Column('venue', sa.String(length=200), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['home_school_id'], ['schools.id']),
        sa.ForeignKeyConstraint(['away_school_id'], ['schools.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('espn_game_id', name='uq_hoopr_games_espn_game_id'),
    )
    op.create_index('ix_hoopr_games_season', 'hoopr_games', ['season'])

    op.create_table(
        'hoopr_team_game_logs',
        sa.Column('id', sa.BigInteger(), nullable=False),
        sa.Column('espn_game_id', sa.String(length=20), nullable=False),
        sa.Column('season', sa.SmallInteger(), nullable=False),
        sa.Column('game_date', sa.Date(), nullable=True),
        sa.Column('school_id', sa.Integer(), nullable=True),
        sa.Column('espn_team_id', sa.String(length=20), nullable=False),
        sa.Column('opponent_school_id', sa.Integer(), nullable=True),
        sa.Column('home_away', sa.String(length=10), nullable=True),
        sa.Column('points', sa.Integer(), nullable=True),
        sa.Column('opponent_points', sa.Integer(), nullable=True),
        sa.Column('field_goals_made', sa.Integer(), nullable=True),
        sa.Column('field_goals_attempted', sa.Integer(), nullable=True),
        sa.Column('three_point_field_goals_made', sa.Integer(), nullable=True),
        sa.Column('three_point_field_goals_attempted', sa.Integer(), nullable=True),
        sa.Column('free_throws_made', sa.Integer(), nullable=True),
        sa.Column('free_throws_attempted', sa.Integer(), nullable=True),
        sa.Column('offensive_rebounds', sa.Integer(), nullable=True),
        sa.Column('defensive_rebounds', sa.Integer(), nullable=True),
        sa.Column('total_rebounds', sa.Integer(), nullable=True),
        sa.Column('assists', sa.Integer(), nullable=True),
        sa.Column('steals', sa.Integer(), nullable=True),
        sa.Column('blocks', sa.Integer(), nullable=True),
        sa.Column('turnovers', sa.Integer(), nullable=True),
        sa.Column('fouls', sa.Integer(), nullable=True),
        sa.Column('points_in_paint', sa.Integer(), nullable=True),
        sa.Column('fast_break_points', sa.Integer(), nullable=True),
        sa.Column('turnover_points', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['espn_game_id'], ['hoopr_games.espn_game_id']),
        sa.ForeignKeyConstraint(['school_id'], ['schools.id']),
        sa.ForeignKeyConstraint(['opponent_school_id'], ['schools.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('espn_game_id', 'espn_team_id', name='uq_hoopr_team_game_log'),
    )
    op.create_index('ix_hoopr_team_game_logs_school_season', 'hoopr_team_game_logs', ['school_id', 'season'])

    op.create_table(
        'hoopr_player_game_logs',
        sa.Column('id', sa.BigInteger(), nullable=False),
        sa.Column('espn_game_id', sa.String(length=20), nullable=False),
        sa.Column('season', sa.SmallInteger(), nullable=False),
        sa.Column('game_date', sa.Date(), nullable=True),
        sa.Column('player_id', sa.Integer(), nullable=True),
        sa.Column('espn_athlete_id', sa.String(length=20), nullable=False),
        sa.Column('raw_display_name', sa.String(length=200), nullable=False),
        sa.Column('school_id', sa.Integer(), nullable=True),
        sa.Column('opponent_school_id', sa.Integer(), nullable=True),
        sa.Column('home_away', sa.String(length=10), nullable=True),
        sa.Column('starter', sa.Boolean(), nullable=True),
        sa.Column('minutes', sa.Float(), nullable=True),
        sa.Column('field_goals_made', sa.Integer(), nullable=True),
        sa.Column('field_goals_attempted', sa.Integer(), nullable=True),
        sa.Column('three_point_field_goals_made', sa.Integer(), nullable=True),
        sa.Column('three_point_field_goals_attempted', sa.Integer(), nullable=True),
        sa.Column('free_throws_made', sa.Integer(), nullable=True),
        sa.Column('free_throws_attempted', sa.Integer(), nullable=True),
        sa.Column('offensive_rebounds', sa.Integer(), nullable=True),
        sa.Column('defensive_rebounds', sa.Integer(), nullable=True),
        sa.Column('rebounds', sa.Integer(), nullable=True),
        sa.Column('assists', sa.Integer(), nullable=True),
        sa.Column('steals', sa.Integer(), nullable=True),
        sa.Column('blocks', sa.Integer(), nullable=True),
        sa.Column('turnovers', sa.Integer(), nullable=True),
        sa.Column('fouls', sa.Integer(), nullable=True),
        sa.Column('points', sa.Integer(), nullable=True),
        sa.Column('match_confidence', sa.Float(), nullable=True),
        sa.Column('match_status', sa.String(length=20), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['espn_game_id'], ['hoopr_games.espn_game_id']),
        sa.ForeignKeyConstraint(['player_id'], ['players.id']),
        sa.ForeignKeyConstraint(['school_id'], ['schools.id']),
        sa.ForeignKeyConstraint(['opponent_school_id'], ['schools.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('espn_game_id', 'espn_athlete_id', name='uq_hoopr_player_game_log'),
    )
    op.create_index('ix_hoopr_player_game_logs_player_season', 'hoopr_player_game_logs', ['player_id', 'season'])
    op.create_index('ix_hoopr_player_game_logs_school_season', 'hoopr_player_game_logs', ['school_id', 'season'])


def downgrade() -> None:
    op.drop_index('ix_hoopr_player_game_logs_school_season', table_name='hoopr_player_game_logs')
    op.drop_index('ix_hoopr_player_game_logs_player_season', table_name='hoopr_player_game_logs')
    op.drop_table('hoopr_player_game_logs')
    op.drop_index('ix_hoopr_team_game_logs_school_season', table_name='hoopr_team_game_logs')
    op.drop_table('hoopr_team_game_logs')
    op.drop_index('ix_hoopr_games_season', table_name='hoopr_games')
    op.drop_table('hoopr_games')
