"""add index on player_team_fit_scores.season for fast MAX(season) lookups

fit_score_service.get_current_season() runs SELECT MAX(season) FROM
player_team_fit_scores on every request that doesn't hit its Redis cache.
Without this index that's a full sequential scan over ~10M rows (~200-300s
each); real production incident (2026-07-20) where a broken Redis cache
(REDIS_URL never set on the ECS task, silently falls through per the
cache's fail-open behavior) let 15+ concurrent copies of that scan pile up,
making the dashboard/fit/compare pages unusable for 5+ minutes. This index
makes the query a near-instant backward index scan regardless of whether
the cache is working.

Revision ID: b3f8e21a6c94
Revises: d7f54d0a43bb
Create Date: 2026-07-20
"""

from typing import Sequence, Union

from alembic import op

revision: str = "b3f8e21a6c94"
down_revision: Union[str, Sequence[str], None] = "d7f54d0a43bb"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # CONCURRENTLY can't run inside a transaction — autocommit_block() takes
    # this migration out of alembic's normal transactional DDL wrapper so the
    # index can build without holding a table lock (safe on a live 10M-row
    # table; a plain CREATE INDEX would block reads/writes for the duration).
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_fit_scores_season "
            "ON player_team_fit_scores (season)"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_fit_scores_season")
