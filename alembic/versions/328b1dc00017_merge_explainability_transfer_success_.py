"""merge explainability transfer success and news agent heads

Revision ID: 328b1dc00017
Revises: d7f3b2e1a904, e5a8c2d4f901, e9f2a7b3c4d5
Create Date: 2026-07-18 20:27:27.652085

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '328b1dc00017'
down_revision: Union[str, Sequence[str], None] = ('d7f3b2e1a904', 'e5a8c2d4f901', 'e9f2a7b3c4d5')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
