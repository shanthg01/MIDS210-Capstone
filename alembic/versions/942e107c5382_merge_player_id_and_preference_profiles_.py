"""merge_player_id_and_preference_profiles_heads

Revision ID: 942e107c5382
Revises: e081c25c38c4, b79fc59994f7
Create Date: 2026-06-26 16:49:51.103168

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '942e107c5382'
down_revision: Union[str, Sequence[str], None] = ('e081c25c38c4', 'b79fc59994f7')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
