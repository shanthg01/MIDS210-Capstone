"""add_hoop_explorer_tables

Revision ID: a3f7b2c9e1d0
Revises: 4d2553a387cc
Create Date: 2026-06-14

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a3f7b2c9e1d0'
down_revision: Union[str, Sequence[str], None] = '4d2553a387cc'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'hoop_explorer_team_stats',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('school_id', sa.Integer(), nullable=True),
        sa.Column('season', sa.SmallInteger(), nullable=False),
        sa.Column('he_team_id', sa.String(length=20), nullable=True),
        sa.Column('he_team_name', sa.String(length=200), nullable=False),
        sa.Column('conf', sa.String(length=100), nullable=True),
        sa.Column('wins', sa.SmallInteger(), nullable=True),
        sa.Column('losses', sa.SmallInteger(), nullable=True),
        sa.Column('wab', sa.Float(), nullable=True),
        sa.Column('power', sa.Float(), nullable=True),
        sa.Column('off_adj_ppp', sa.Float(), nullable=True),
        sa.Column('def_adj_ppp', sa.Float(), nullable=True),
        sa.Column('adj_net', sa.Float(), nullable=True),
        sa.Column('tempo', sa.Float(), nullable=True),
        sa.Column('off_efg', sa.Float(), nullable=True),
        sa.Column('off_to', sa.Float(), nullable=True),
        sa.Column('off_ftr', sa.Float(), nullable=True),
        sa.Column('off_orb', sa.Float(), nullable=True),
        sa.Column('def_efg', sa.Float(), nullable=True),
        sa.Column('def_to', sa.Float(), nullable=True),
        sa.Column('def_ftr', sa.Float(), nullable=True),
        sa.Column('def_orb', sa.Float(), nullable=True),
        sa.Column('off_threepr', sa.Float(), nullable=True),
        sa.Column('off_twoprimr', sa.Float(), nullable=True),
        sa.Column('off_twopmidr', sa.Float(), nullable=True),
        sa.Column('def_threepr', sa.Float(), nullable=True),
        sa.Column('def_twoprimr', sa.Float(), nullable=True),
        sa.Column('def_twopmidr', sa.Float(), nullable=True),
        sa.Column('off_assist', sa.Float(), nullable=True),
        sa.Column('def_assist', sa.Float(), nullable=True),
        sa.Column('off_style_rim_attack_pct', sa.Float(), nullable=True),
        sa.Column('off_style_attack_kick_pct', sa.Float(), nullable=True),
        sa.Column('off_style_dribble_jumper_pct', sa.Float(), nullable=True),
        sa.Column('off_style_mid_range_pct', sa.Float(), nullable=True),
        sa.Column('off_style_perimeter_cut_pct', sa.Float(), nullable=True),
        sa.Column('off_style_big_cut_roll_pct', sa.Float(), nullable=True),
        sa.Column('off_style_post_up_pct', sa.Float(), nullable=True),
        sa.Column('off_style_post_kick_pct', sa.Float(), nullable=True),
        sa.Column('off_style_pick_pop_pct', sa.Float(), nullable=True),
        sa.Column('off_style_high_low_pct', sa.Float(), nullable=True),
        sa.Column('off_style_reb_scramble_pct', sa.Float(), nullable=True),
        sa.Column('off_style_transition_pct', sa.Float(), nullable=True),
        sa.Column('def_style_rim_attack_pct', sa.Float(), nullable=True),
        sa.Column('def_style_attack_kick_pct', sa.Float(), nullable=True),
        sa.Column('def_style_dribble_jumper_pct', sa.Float(), nullable=True),
        sa.Column('def_style_mid_range_pct', sa.Float(), nullable=True),
        sa.Column('def_style_perimeter_cut_pct', sa.Float(), nullable=True),
        sa.Column('def_style_big_cut_roll_pct', sa.Float(), nullable=True),
        sa.Column('def_style_post_up_pct', sa.Float(), nullable=True),
        sa.Column('def_style_post_kick_pct', sa.Float(), nullable=True),
        sa.Column('def_style_pick_pop_pct', sa.Float(), nullable=True),
        sa.Column('def_style_high_low_pct', sa.Float(), nullable=True),
        sa.Column('def_style_reb_scramble_pct', sa.Float(), nullable=True),
        sa.Column('def_style_transition_pct', sa.Float(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['school_id'], ['schools.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('he_team_name', 'season', name='uq_he_team_stats'),
    )
    op.create_index('ix_he_team_stats_school_season', 'hoop_explorer_team_stats', ['school_id', 'season'])

    op.create_table(
        'hoop_explorer_player_stats',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('player_id', sa.Integer(), nullable=True),
        sa.Column('school_id', sa.Integer(), nullable=True),
        sa.Column('season', sa.SmallInteger(), nullable=False),
        sa.Column('he_player_code', sa.String(length=50), nullable=False),
        sa.Column('he_ncaa_id', sa.String(length=20), nullable=True),
        sa.Column('he_team_name', sa.String(length=200), nullable=False),
        sa.Column('player_name', sa.String(length=200), nullable=True),
        sa.Column('pos_class', sa.String(length=10), nullable=True),
        sa.Column('year_class', sa.String(length=10), nullable=True),
        sa.Column('height', sa.String(length=10), nullable=True),
        sa.Column('conf', sa.String(length=100), nullable=True),
        sa.Column('transfer_src', sa.String(length=200), nullable=True),
        sa.Column('transfer_dest', sa.String(length=200), nullable=True),
        sa.Column('off_team_poss_pct', sa.Float(), nullable=True),
        sa.Column('adj_rtg_margin', sa.Float(), nullable=True),
        sa.Column('adj_rapm_margin', sa.Float(), nullable=True),
        sa.Column('off_adj_rapm', sa.Float(), nullable=True),
        sa.Column('def_adj_rapm', sa.Float(), nullable=True),
        sa.Column('adj_rapm_margin_pred', sa.Float(), nullable=True),
        sa.Column('off_usage', sa.Float(), nullable=True),
        sa.Column('off_assist', sa.Float(), nullable=True),
        sa.Column('off_efg', sa.Float(), nullable=True),
        sa.Column('off_to', sa.Float(), nullable=True),
        sa.Column('off_ftr', sa.Float(), nullable=True),
        sa.Column('off_threepr', sa.Float(), nullable=True),
        sa.Column('off_twoprimr', sa.Float(), nullable=True),
        sa.Column('off_twopmidr', sa.Float(), nullable=True),
        sa.Column('off_threep', sa.Float(), nullable=True),
        sa.Column('off_twoprim', sa.Float(), nullable=True),
        sa.Column('off_twopmid', sa.Float(), nullable=True),
        sa.Column('off_ft', sa.Float(), nullable=True),
        sa.Column('off_orb', sa.Float(), nullable=True),
        sa.Column('def_orb', sa.Float(), nullable=True),
        sa.Column('def_stl', sa.Float(), nullable=True),
        sa.Column('def_blk', sa.Float(), nullable=True),
        sa.Column('off_style_rim_attack_pct', sa.Float(), nullable=True),
        sa.Column('off_style_attack_kick_pct', sa.Float(), nullable=True),
        sa.Column('off_style_perimeter_sniper_pct', sa.Float(), nullable=True),
        sa.Column('off_style_dribble_jumper_pct', sa.Float(), nullable=True),
        sa.Column('off_style_mid_range_pct', sa.Float(), nullable=True),
        sa.Column('off_style_hits_cutter_pct', sa.Float(), nullable=True),
        sa.Column('off_style_perimeter_cut_pct', sa.Float(), nullable=True),
        sa.Column('off_style_pnr_passer_pct', sa.Float(), nullable=True),
        sa.Column('off_style_big_cut_roll_pct', sa.Float(), nullable=True),
        sa.Column('off_style_post_up_pct', sa.Float(), nullable=True),
        sa.Column('off_style_post_kick_pct', sa.Float(), nullable=True),
        sa.Column('off_style_pick_pop_pct', sa.Float(), nullable=True),
        sa.Column('off_style_high_low_pct', sa.Float(), nullable=True),
        sa.Column('off_style_reb_scramble_pct', sa.Float(), nullable=True),
        sa.Column('off_style_transition_pct', sa.Float(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['player_id'], ['players.id']),
        sa.ForeignKeyConstraint(['school_id'], ['schools.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('he_player_code', 'season', name='uq_he_player_stats'),
    )
    op.create_index('ix_he_player_stats_player_season', 'hoop_explorer_player_stats', ['player_id', 'season'])


def downgrade() -> None:
    op.drop_index('ix_he_player_stats_player_season', table_name='hoop_explorer_player_stats')
    op.drop_table('hoop_explorer_player_stats')
    op.drop_index('ix_he_team_stats_school_season', table_name='hoop_explorer_team_stats')
    op.drop_table('hoop_explorer_team_stats')
