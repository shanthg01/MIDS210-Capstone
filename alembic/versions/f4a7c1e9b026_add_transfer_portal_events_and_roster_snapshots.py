"""add_transfer_portal_events_and_roster_snapshots

Revision ID: f4a7c1e9b026
Revises: d8e5c2a9f163
Create Date: 2026-06-20

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f4a7c1e9b026'
down_revision: Union[str, Sequence[str], None] = 'd8e5c2a9f163'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_unique_constraint('uq_transfers_player_season', 'transfers', ['player_id', 'season'])

    op.create_table(
        'transfer_portal_events',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('season', sa.SmallInteger(), nullable=False),
        sa.Column('source', sa.String(length=20), nullable=False),
        sa.Column('source_player_key', sa.String(length=50), nullable=False),
        sa.Column('player_id', sa.Integer(), nullable=True),
        sa.Column('raw_player_name', sa.String(length=200), nullable=False),
        sa.Column('match_confidence', sa.Float(), nullable=True),
        sa.Column('match_status', sa.String(length=20), nullable=False),
        sa.Column('from_school_id', sa.Integer(), nullable=True),
        sa.Column('to_school_id', sa.Integer(), nullable=True),
        sa.Column('from_institution_raw', sa.String(length=200), nullable=True),
        sa.Column('to_institution_raw', sa.String(length=200), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('portal_entry_date', sa.Date(), nullable=True),
        sa.Column('commitment_date', sa.Date(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['player_id'], ['players.id']),
        sa.ForeignKeyConstraint(['from_school_id'], ['schools.id']),
        sa.ForeignKeyConstraint(['to_school_id'], ['schools.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('source', 'source_player_key', 'season', name='uq_transfer_portal_event'),
    )

    op.create_table(
        'roster_snapshots',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('school_id', sa.Integer(), nullable=False),
        sa.Column('season', sa.SmallInteger(), nullable=False),
        sa.Column('snapshot_date', sa.Date(), nullable=False),
        sa.Column('source', sa.String(length=30), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['school_id'], ['schools.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('school_id', 'snapshot_date', name='uq_roster_snapshot'),
    )

    op.create_table(
        'roster_snapshot_players',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('snapshot_id', sa.Integer(), nullable=False),
        sa.Column('player_id', sa.Integer(), nullable=True),
        sa.Column('raw_player_name', sa.String(length=200), nullable=False),
        sa.Column('class_year', sa.String(length=10), nullable=True),
        sa.Column('height', sa.String(length=10), nullable=True),
        sa.Column('min_pct', sa.Float(), nullable=True),
        sa.Column('ortg', sa.Float(), nullable=True),
        sa.Column('usage_rate', sa.Float(), nullable=True),
        sa.Column('returning_status', sa.String(length=20), nullable=False),
        sa.Column('transfer_source_school_id', sa.Integer(), nullable=True),
        sa.Column('match_confidence', sa.Float(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['snapshot_id'], ['roster_snapshots.id']),
        sa.ForeignKeyConstraint(['player_id'], ['players.id']),
        sa.ForeignKeyConstraint(['transfer_source_school_id'], ['schools.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('snapshot_id', 'raw_player_name', name='uq_roster_snapshot_player'),
    )


def downgrade() -> None:
    op.drop_table('roster_snapshot_players')
    op.drop_table('roster_snapshots')
    op.drop_table('transfer_portal_events')
    op.drop_constraint('uq_transfers_player_season', 'transfers', type_='unique')
