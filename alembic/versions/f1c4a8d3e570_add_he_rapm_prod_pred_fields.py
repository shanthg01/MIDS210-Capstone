"""add_he_rapm_prod_pred_fields

Revision ID: f1c4a8d3e570
Revises: e6a2c8f1b734
Create Date: 2026-06-23

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f1c4a8d3e570'
down_revision: Union[str, Sequence[str], None] = 'e6a2c8f1b734'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # These columns exist in the raw Hoop Explorer source CSVs but were never
    # mapped by ingest_hoop_explorer.py's player-row mapping — confirmed against
    # data/hoop_explorer/all_player_stats_*.csv headers (2026-06-23). Migration
    # only adds the columns; ingest_hoop_explorer.py --all-seasons must be
    # re-run afterward to populate them (same gotcha as migration 2f6a1c9d8b30).
    op.add_column('hoop_explorer_player_stats', sa.Column('off_adj_rapm_prod', sa.Float(), nullable=True))
    op.add_column('hoop_explorer_player_stats', sa.Column('def_adj_prod_rapm', sa.Float(), nullable=True))
    op.add_column('hoop_explorer_player_stats', sa.Column('adj_rapm_prod_margin', sa.Float(), nullable=True))
    op.add_column('hoop_explorer_player_stats', sa.Column('off_adj_rapm_pred', sa.Float(), nullable=True))
    op.add_column('hoop_explorer_player_stats', sa.Column('def_adj_rapm_pred', sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column('hoop_explorer_player_stats', 'def_adj_rapm_pred')
    op.drop_column('hoop_explorer_player_stats', 'off_adj_rapm_pred')
    op.drop_column('hoop_explorer_player_stats', 'adj_rapm_prod_margin')
    op.drop_column('hoop_explorer_player_stats', 'def_adj_prod_rapm')
    op.drop_column('hoop_explorer_player_stats', 'off_adj_rapm_prod')
