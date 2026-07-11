"""merge_news_agent_and_main_heads

Revision ID: e11d8f65109c
Revises: b1d3f5a7c9e2, b2f0d4c8a917
Create Date: 2026-07-11 17:44:31.611370

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e11d8f65109c'
down_revision: Union[str, Sequence[str], None] = ('b1d3f5a7c9e2', 'b2f0d4c8a917')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
