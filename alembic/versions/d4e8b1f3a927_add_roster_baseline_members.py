"""add_roster_baseline_members

Revision ID: d4e8b1f3a927
Revises: c7f1a9d3e652
Create Date: 2026-06-23

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd4e8b1f3a927'
down_revision: Union[str, Sequence[str], None] = 'c7f1a9d3e652'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'roster_baseline_members',
        sa.Column('id', sa.BigInteger(), nullable=False),
        sa.Column('player_id', sa.Integer(), nullable=False),
        sa.Column('school_id', sa.Integer(), nullable=False),
        sa.Column('season', sa.SmallInteger(), nullable=False),
        sa.Column('baseline_status', sa.String(length=30), nullable=False),
        sa.Column('computed_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['player_id'], ['players.id']),
        sa.ForeignKeyConstraint(['school_id'], ['schools.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('player_id', 'school_id', 'season', name='uq_roster_baseline_member'),
    )
    op.create_index(
        'ix_roster_baseline_school_season',
        'roster_baseline_members',
        ['school_id', 'season'],
    )


def downgrade() -> None:
    op.drop_index('ix_roster_baseline_school_season', table_name='roster_baseline_members')
    op.drop_table('roster_baseline_members')
