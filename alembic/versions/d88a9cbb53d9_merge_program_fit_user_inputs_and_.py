"""merge program_fit_user_inputs and cluster_explanations heads

Revision ID: d88a9cbb53d9
Revises: a1b2c3d4e5f6, c7d4e9f1a203
Create Date: 2026-07-24 09:59:30.285334

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd88a9cbb53d9'
down_revision: Union[str, Sequence[str], None] = ('a1b2c3d4e5f6', 'c7d4e9f1a203')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
