"""player_id_step1_add_backfill

Step 1 of 3 for CLAUDE.md Process Improvement TODO #1 (deterministic
player_id). Adds a parallel BigInteger column everywhere player_id is
used and backfills it via derive_player_id() in Python — explicitly not a
SQL-side SHA-256 reimplementation, so the backfill and the application's
future inserts (scripts/ingest_barttorvik.py) can never drift out of sync.

Backfill is server-side per dependent table (single UPDATE ... FROM join),
not a Python loop over rows — player_team_fit_scores alone is ~9.7M rows.

Revision ID: 78cdd571612d
Revises: f1c4a8d3e570
Create Date: 2026-06-26 11:52:53.889972

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from portalpoint.db.player_ids import derive_player_id


# revision identifiers, used by Alembic.
revision: str = '78cdd571612d'
down_revision: Union[str, Sequence[str], None] = 'f1c4a8d3e570'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Every table FK'd to players.id, per `grep ForeignKey("players.id") db/models.py` —
# 18 tables, not the 6 listed in CLAUDE.md's original TODO text (written before
# Player Projection, Recommendations, Predictions, NIL Valuation, Roster Baseline,
# Roster Snapshot/Depth Chart, and User Shortlist existed).
DEPENDENT_TABLES = [
    "player_school_seasons", "player_season_stats", "player_archetypes",
    "hoop_explorer_player_stats", "hoopr_player_season_stats", "hoopr_player_game_logs",
    "roster_depth_charts", "transfers", "transfer_portal_events", "roster_snapshot_players",
    "roster_baseline_members", "nil_valuations", "player_team_fit_scores", "predictions",
    "player_projections", "recommendations", "team_rating_projections", "user_shortlists",
]


def upgrade() -> None:
    bind = op.get_bind()

    op.add_column("players", sa.Column("new_id", sa.BigInteger(), nullable=True))

    rows = bind.execute(sa.text("SELECT id, barttorvik_id FROM players")).fetchall()
    missing = [r.id for r in rows if not r.barttorvik_id]
    if missing:
        raise RuntimeError(
            f"{len(missing)} players have no barttorvik_id (ids: {missing[:10]}...) — "
            "the 100% coverage this migration depends on (CLAUDE.md TODO #1) is no longer "
            "true, aborting before any data is touched."
        )
    if rows:
        bind.execute(
            sa.text("UPDATE players SET new_id = :new_id WHERE id = :old_id"),
            [{"old_id": r.id, "new_id": derive_player_id(r.barttorvik_id)} for r in rows],
        )

    for table in DEPENDENT_TABLES:
        op.add_column(table, sa.Column("new_player_id", sa.BigInteger(), nullable=True))
        op.execute(
            f"UPDATE {table} t SET new_player_id = p.new_id "
            f"FROM players p WHERE p.id = t.player_id"
        )


def downgrade() -> None:
    for table in DEPENDENT_TABLES:
        op.drop_column(table, "new_player_id")
    op.drop_column("players", "new_id")
