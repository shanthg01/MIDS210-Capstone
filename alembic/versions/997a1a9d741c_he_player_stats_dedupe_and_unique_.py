"""he_player_stats_dedupe_and_unique_constraint

Revision ID: 997a1a9d741c
Revises: c3a9e1f5b847
Create Date: 2026-07-12 12:58:11.031539

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '997a1a9d741c'
down_revision: Union[str, Sequence[str], None] = 'c3a9e1f5b847'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Data-fix + guard rail for Issue #53.

    hoop_explorer_player_stats.player_id is assigned by fuzzy name-matching during
    ingestion and only ever had a plain index, not a unique constraint, on
    (player_id, season) — two different real players (e.g. "TJ Johnson"/"RJ Johnson",
    "Matthew Mayer"/"Matt Moyer") could collide onto the same player_id for a season,
    which fanned out downstream joins (playing_time.py INFERENCE_SQL) into duplicate
    (player_id, school_id, season, model_version) rows and crashed the upsert.

    Step 1 nulls player_id on every row except one deterministic "keeper" per
    (player_id, season) group that currently has more than one row — can't tell from
    SQL alone which row is the *correct* match, so this is a safe stopgap; a rerun of
    the reinforced ingest_hoop_explorer.py matcher (token-wise fuzzy match + in-batch
    collision guard) will re-resolve the nulled rows against their real player_id.

    Step 2 adds a partial unique index (player_id nullable for unmatched rows, so a
    plain UniqueConstraint can't express this — same reasoning as player_projections'
    split neutral/destination indexes) so this class of collision can never silently
    duplicate again; it now fails loudly at ingest time instead, where
    ingest_hoop_explorer.py's _upsert() catches it and retries with player_id=None.
    """
    op.execute(
        """
        WITH keepers AS (
            SELECT DISTINCT ON (player_id, season) id
            FROM hoop_explorer_player_stats
            WHERE player_id IS NOT NULL
            ORDER BY player_id, season, id
        )
        UPDATE hoop_explorer_player_stats
        SET player_id = NULL
        WHERE player_id IS NOT NULL
          AND id NOT IN (SELECT id FROM keepers)
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_he_player_stats_player_season
        ON hoop_explorer_player_stats (player_id, season)
        WHERE player_id IS NOT NULL
        """
    )


def downgrade() -> None:
    """Drop the guard-rail index. The player_id nulling in upgrade() is a data fix,
    not schema — not reversed here, consistent with other data-fix migrations in
    this repo."""
    op.execute("DROP INDEX IF EXISTS uq_he_player_stats_player_season")
