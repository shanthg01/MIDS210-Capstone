"""add_player_projections

Revision ID: e6a2c8f1b734
Revises: d4e8b1f3a927
Create Date: 2026-06-23

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'e6a2c8f1b734'
down_revision: Union[str, Sequence[str], None] = 'd4e8b1f3a927'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'player_projections',
        sa.Column('id', sa.BigInteger(), nullable=False),
        sa.Column('player_id', sa.Integer(), nullable=False),
        sa.Column('school_id', sa.Integer(), nullable=True),
        sa.Column('season', sa.SmallInteger(), nullable=False),
        sa.Column('projection_mode', sa.String(length=20), nullable=False),
        sa.Column('value_per_100', sa.Float(), nullable=False),
        sa.Column('value_ci_lower', sa.Float(), nullable=True),
        sa.Column('value_ci_upper', sa.Float(), nullable=True),
        sa.Column('projected_minutes', sa.Float(), nullable=True),
        sa.Column('projected_usage', sa.Float(), nullable=True),
        sa.Column('projected_box_score', postgresql.JSONB(), nullable=True),
        sa.Column('projected_rates', postgresql.JSONB(), nullable=True),
        sa.Column('skill_states', postgresql.JSONB(), nullable=True),
        sa.Column('skill_percentiles', postgresql.JSONB(), nullable=True),
        sa.Column('uncertainty', postgresql.JSONB(), nullable=True),
        sa.Column('explanation', postgresql.JSONB(), nullable=True),
        sa.Column('model_version', sa.String(length=30), nullable=False),
        sa.Column('computed_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['player_id'], ['players.id']),
        sa.ForeignKeyConstraint(['school_id'], ['schools.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    # school_id is nullable for neutral-mode rows. A plain UniqueConstraint
    # can't dedupe across reruns here — Postgres treats every NULL as
    # distinct, so neutral reruns would insert a fresh row each time instead
    # of updating in place. Two partial unique indexes split neutral vs
    # destination mode instead (see player_projection_state_space_plan.md §18).
    op.create_index(
        'uq_player_projections_neutral',
        'player_projections',
        ['player_id', 'season', 'model_version'],
        unique=True,
        postgresql_where=sa.text('school_id IS NULL'),
    )
    op.create_index(
        'uq_player_projections_destination',
        'player_projections',
        ['player_id', 'school_id', 'season', 'model_version'],
        unique=True,
        postgresql_where=sa.text('school_id IS NOT NULL'),
    )
    op.create_index(
        'ix_player_projections_player_season',
        'player_projections',
        ['player_id', 'season'],
    )


def downgrade() -> None:
    op.drop_index('ix_player_projections_player_season', table_name='player_projections')
    op.drop_index('uq_player_projections_destination', table_name='player_projections')
    op.drop_index('uq_player_projections_neutral', table_name='player_projections')
    op.drop_table('player_projections')
