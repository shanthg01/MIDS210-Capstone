"""add_user_preference_profiles

Revision ID: e081c25c38c4
Revises: f1c4a8d3e570
Create Date: 2026-06-26 11:15:20.077423

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'e081c25c38c4'
down_revision: Union[str, Sequence[str], None] = 'f1c4a8d3e570'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'user_preference_profiles',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('importance_scheme_fit', sa.SmallInteger(), nullable=False, server_default='7'),
        sa.Column('importance_role_fit', sa.SmallInteger(), nullable=False, server_default='5'),
        sa.Column('importance_gap_match', sa.SmallInteger(), nullable=False, server_default='5'),
        sa.Column('importance_program_fit', sa.SmallInteger(), nullable=False, server_default='5'),
        sa.Column('weight_gap', sa.Float(), nullable=False, server_default='0.20'),
        sa.Column('weight_scheme', sa.Float(), nullable=False, server_default='0.30'),
        sa.Column('weight_role', sa.Float(), nullable=False, server_default='0.25'),
        sa.Column('weight_program', sa.Float(), nullable=False, server_default='0.25'),
        sa.Column('filters', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'name', name='uq_user_preference_profiles_user_name'),
    )
    op.create_index(
        'ix_user_preference_profiles_user_id', 'user_preference_profiles', ['user_id'],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_user_preference_profiles_user_id', table_name='user_preference_profiles')
    op.drop_table('user_preference_profiles')
