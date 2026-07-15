"""merge heads

Revision ID: 40beacdccf1e
Revises: 997a1a9d741c, b1d3f5a7c9e2
Create Date: 2026-07-15 12:16:55.378636

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '40beacdccf1e'
down_revision: Union[str, Sequence[str], None] = ('997a1a9d741c', 'b1d3f5a7c9e2')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
