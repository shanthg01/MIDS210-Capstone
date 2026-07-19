"""merge news agent catchup heads

Revision ID: d7f3b2e1a904
Revises: f8db53b163a8, c9e2a1f4b8d3
Create Date: 2026-07-16
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "d7f3b2e1a904"
down_revision: Union[str, Sequence[str], None] = ("f8db53b163a8", "c9e2a1f4b8d3")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
