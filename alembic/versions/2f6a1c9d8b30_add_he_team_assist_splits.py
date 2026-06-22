"""add_he_team_assist_splits

Revision ID: 2f6a1c9d8b30
Revises: 9c8b7a6d5e4f
Create Date: 2026-06-18

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '2f6a1c9d8b30'
down_revision: Union[str, Sequence[str], None] = '9c8b7a6d5e4f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


TEAM_ASSIST_SPLIT_COLUMNS = (
    'off_ast_rim',
    'off_ast_mid',
    'off_ast_threep',
    'def_ast_rim',
    'def_ast_mid',
    'def_ast_threep',
)


def upgrade() -> None:
    for column_name in TEAM_ASSIST_SPLIT_COLUMNS:
        op.add_column('hoop_explorer_team_stats', sa.Column(column_name, sa.Float(), nullable=True))


def downgrade() -> None:
    for column_name in reversed(TEAM_ASSIST_SPLIT_COLUMNS):
        op.drop_column('hoop_explorer_team_stats', column_name)
