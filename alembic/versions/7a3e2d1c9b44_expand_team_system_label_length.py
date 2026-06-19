"""expand_team_system_label_length

Revision ID: 7a3e2d1c9b44
Revises: 2f6a1c9d8b30
Create Date: 2026-06-18

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '7a3e2d1c9b44'
down_revision: Union[str, Sequence[str], None] = '2f6a1c9d8b30'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        'team_system_profiles',
        'system_label',
        existing_type=sa.String(length=50),
        type_=sa.String(length=100),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        'team_system_profiles',
        'system_label',
        existing_type=sa.String(length=100),
        type_=sa.String(length=50),
        existing_nullable=False,
    )
