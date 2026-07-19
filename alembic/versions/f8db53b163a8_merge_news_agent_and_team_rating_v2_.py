"""merge news agent and team rating v2 heads

Revision ID: f8db53b163a8
Revises: e11d8f65109c, c3a9e1f5b847
Create Date: 2026-07-12 21:57:00.357065

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f8db53b163a8'
down_revision: Union[str, Sequence[str], None] = ('e11d8f65109c', 'c3a9e1f5b847')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
