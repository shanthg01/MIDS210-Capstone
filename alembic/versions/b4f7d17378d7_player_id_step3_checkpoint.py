"""player_id_step3_drop_old_and_rename

Step 3 of 3 for CLAUDE.md Process Improvement TODO #1 (deterministic
player_id) — the one genuinely irreversible step. Steps 1-2 left the
original id/player_id columns and values fully intact specifically so
they could be inspected/rolled back to with zero data loss; this step
drops them for real and renames new_id -> id, new_player_id -> player_id
everywhere. FK/unique-constraint/index names were already finalized in
step 2 (pointing at the new_* columns under their final names) — renaming
a column doesn't invalidate the constraints already defined on it, so no
constraint work is needed here, only the drop + rename.

Downgrade note: this revision's downgrade restores the *schema* (old
columns re-added) but not the original SERIAL id *values* — those were
real auto-increment integers with no recoverable record of their
mapping once dropped. That data loss is the explicit, accepted purpose of
this step; if you need to undo it, restore from a backup taken before
running this revision rather than relying on `alembic downgrade`.

Revision ID: b4f7d17378d7
Revises: f7c0a7862876
Create Date: 2026-06-26 11:53:15.829831

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b4f7d17378d7'
down_revision: Union[str, Sequence[str], None] = 'f7c0a7862876'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


DEPENDENT_TABLES = [
    "player_school_seasons", "player_season_stats", "player_archetypes",
    "hoop_explorer_player_stats", "hoopr_player_season_stats", "hoopr_player_game_logs",
    "roster_depth_charts", "transfers", "transfer_portal_events", "roster_snapshot_players",
    "roster_baseline_members", "nil_valuations", "player_team_fit_scores", "predictions",
    "player_projections", "recommendations", "team_rating_projections", "user_shortlists",
]

def upgrade() -> None:
    for table in DEPENDENT_TABLES:
        op.drop_column(table, "player_id")
        op.alter_column(table, "new_player_id", new_column_name="player_id")

    op.drop_column("players", "id")
    op.alter_column("players", "new_id", new_column_name="id")


def downgrade() -> None:
    op.alter_column("players", "id", new_column_name="new_id")
    op.add_column("players", sa.Column("id", sa.Integer(), nullable=True))

    # nullable=True regardless of the table's original constraint — there's no
    # data to populate this re-added column with, so a NOT NULL default would
    # fail outright on any table with existing rows. Accepted limitation of
    # this lossy downgrade (see module docstring).
    for table in DEPENDENT_TABLES:
        op.alter_column(table, "player_id", new_column_name="new_player_id")
        op.add_column(table, sa.Column("player_id", sa.Integer(), nullable=True))
