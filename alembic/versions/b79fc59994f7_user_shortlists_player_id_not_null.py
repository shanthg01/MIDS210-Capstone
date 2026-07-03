"""user_shortlists_player_id_not_null

Schema-drift fix found while validating the player_id migration:
user_shortlists.player_id is `nullable=False` in db/models.py but the live
DB never actually had that constraint. add_to_shortlist() always supplies
player_id from the URL path today, so nothing app-level can produce NULL —
but 2 pre-existing orphan rows (player_id IS NULL, dated 2026-06-05,
predating this fix) had to be cleaned up before SET NOT NULL could apply.
They're unrecoverable junk, not real shortlist entries (no player to
reference), so deletion is correct, not just convenient.

Revision ID: b79fc59994f7
Revises: b4f7d17378d7
Create Date: 2026-06-26 12:47:15.593837

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b79fc59994f7'
down_revision: Union[str, Sequence[str], None] = 'b4f7d17378d7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("DELETE FROM user_shortlists WHERE player_id IS NULL")
    op.alter_column("user_shortlists", "player_id", nullable=False)


def downgrade() -> None:
    op.alter_column("user_shortlists", "player_id", nullable=True)
