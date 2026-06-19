"""add_cluster_memberships

Revision ID: 9c8b7a6d5e4f
Revises: b5d2e9f4
Create Date: 2026-06-18

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = '9c8b7a6d5e4f'
down_revision: Union[str, Sequence[str], None] = 'b5d2e9f4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'player_archetypes',
        sa.Column('archetype_memberships', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        'team_system_profiles',
        sa.Column('offense_memberships', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        'team_system_profiles',
        sa.Column('defense_memberships', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        'team_system_profiles',
        sa.Column('system_memberships', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('team_system_profiles', 'system_memberships')
    op.drop_column('team_system_profiles', 'defense_memberships')
    op.drop_column('team_system_profiles', 'offense_memberships')
    op.drop_column('player_archetypes', 'archetype_memberships')
