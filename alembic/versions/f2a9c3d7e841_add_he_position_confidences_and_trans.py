"""add_he_position_confidences_and_trans

Adds:
  - hoop_explorer_player_stats: 5 pos_confidence_* columns (posConfidences from HE CSV)
  - hoop_explorer_team_stats: 8 transition/scramble rate+PPP columns

Revision ID: f2a9c3d7e841
Revises: e47b1d6a9c52
Create Date: 2026-06-16

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f2a9c3d7e841'
down_revision: Union[str, Sequence[str], None] = 'e47b1d6a9c52'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- hoop_explorer_player_stats: position confidence distributions ---
    op.add_column('hoop_explorer_player_stats', sa.Column('pos_confidence_pg', sa.Float(), nullable=True))
    op.add_column('hoop_explorer_player_stats', sa.Column('pos_confidence_sg', sa.Float(), nullable=True))
    op.add_column('hoop_explorer_player_stats', sa.Column('pos_confidence_sf', sa.Float(), nullable=True))
    op.add_column('hoop_explorer_player_stats', sa.Column('pos_confidence_pf', sa.Float(), nullable=True))
    op.add_column('hoop_explorer_player_stats', sa.Column('pos_confidence_c',  sa.Float(), nullable=True))

    # --- hoop_explorer_team_stats: transition and scramble rates + PPP ---
    op.add_column('hoop_explorer_team_stats', sa.Column('off_trans_pct',    sa.Float(), nullable=True))
    op.add_column('hoop_explorer_team_stats', sa.Column('off_trans_ppp',    sa.Float(), nullable=True))
    op.add_column('hoop_explorer_team_stats', sa.Column('def_trans_pct',    sa.Float(), nullable=True))
    op.add_column('hoop_explorer_team_stats', sa.Column('def_trans_ppp',    sa.Float(), nullable=True))
    op.add_column('hoop_explorer_team_stats', sa.Column('off_scramble_pct', sa.Float(), nullable=True))
    op.add_column('hoop_explorer_team_stats', sa.Column('off_scramble_ppp', sa.Float(), nullable=True))
    op.add_column('hoop_explorer_team_stats', sa.Column('def_scramble_pct', sa.Float(), nullable=True))
    op.add_column('hoop_explorer_team_stats', sa.Column('def_scramble_ppp', sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column('hoop_explorer_player_stats', 'pos_confidence_pg')
    op.drop_column('hoop_explorer_player_stats', 'pos_confidence_sg')
    op.drop_column('hoop_explorer_player_stats', 'pos_confidence_sf')
    op.drop_column('hoop_explorer_player_stats', 'pos_confidence_pf')
    op.drop_column('hoop_explorer_player_stats', 'pos_confidence_c')

    op.drop_column('hoop_explorer_team_stats', 'off_trans_pct')
    op.drop_column('hoop_explorer_team_stats', 'off_trans_ppp')
    op.drop_column('hoop_explorer_team_stats', 'def_trans_pct')
    op.drop_column('hoop_explorer_team_stats', 'def_trans_ppp')
    op.drop_column('hoop_explorer_team_stats', 'off_scramble_pct')
    op.drop_column('hoop_explorer_team_stats', 'off_scramble_ppp')
    op.drop_column('hoop_explorer_team_stats', 'def_scramble_pct')
    op.drop_column('hoop_explorer_team_stats', 'def_scramble_ppp')
