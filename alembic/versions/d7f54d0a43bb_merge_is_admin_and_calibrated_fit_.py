"""merge is_admin and calibrated fit scores heads

Revision ID: d7f54d0a43bb
Revises: a6c1f9e2d4b8, d2f6a8c1b3e7
Create Date: 2026-07-20 09:51:54.395509

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd7f54d0a43bb'
down_revision: Union[str, Sequence[str], None] = ('a6c1f9e2d4b8', 'd2f6a8c1b3e7')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
