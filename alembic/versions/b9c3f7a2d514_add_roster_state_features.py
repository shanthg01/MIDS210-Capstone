"""add_roster_state_features

Revision ID: b9c3f7a2d514
Revises: f4a7c1e9b026
Create Date: 2026-06-21

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'b9c3f7a2d514'
down_revision: Union[str, Sequence[str], None] = 'f4a7c1e9b026'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'roster_state_features',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('snapshot_id', sa.Integer(), nullable=False),
        sa.Column('school_id', sa.Integer(), nullable=False),
        sa.Column('season', sa.SmallInteger(), nullable=False),
        sa.Column('returning_minutes_by_position', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('departing_minutes_by_position', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('incoming_transfer_minutes_by_position', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('open_minutes_by_position', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('open_usage_by_position', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('returning_production', sa.Float(), nullable=True),
        sa.Column('returning_player_impact', sa.Float(), nullable=True),
        sa.Column('class_balance', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('returning_archetype_counts', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('departing_archetype_counts', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('incoming_archetype_counts', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['snapshot_id'], ['roster_snapshots.id']),
        sa.ForeignKeyConstraint(['school_id'], ['schools.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('snapshot_id', name='uq_roster_state_features_snapshot'),
    )


def downgrade() -> None:
    op.drop_table('roster_state_features')
