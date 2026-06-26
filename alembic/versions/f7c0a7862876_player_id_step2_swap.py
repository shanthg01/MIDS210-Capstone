"""player_id_step2_swap

Step 2 of 3 for CLAUDE.md Process Improvement TODO #1 (deterministic
player_id). Moves every FK/unique-constraint/index off the old
id/player_id columns onto the new BigInteger ones added in step 1 — the
old columns and their original values are left untouched (not dropped)
specifically so this revision's downgrade is fully lossless. Step 3 is
the one genuinely irreversible step ("drop old column", per the TODO's
own phrasing) — kept separate on purpose so a rollback between step 2 and
step 3 never loses data.

Revision ID: f7c0a7862876
Revises: 78cdd571612d
Create Date: 2026-06-26 11:53:13.727599

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f7c0a7862876'
down_revision: Union[str, Sequence[str], None] = '78cdd571612d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# (kind, constraint/index name, columns, [partial-index WHERE clause])
# Mirrors each table's __table_args__ in db/models.py exactly — these are
# the only constraints/indexes that include player_id anywhere in the schema.
EXTRA_CONSTRAINTS: dict[str, list[tuple]] = {
    "player_school_seasons": [("unique", "uq_player_school_season", ["player_id", "school_id", "season"])],
    "player_season_stats": [
        ("unique", "uq_player_season_stats", ["player_id", "school_id", "season"]),
        ("index", "ix_player_season_stats_player_season", ["player_id", "season"]),
    ],
    "player_archetypes": [("unique", "uq_player_archetype_season", ["player_id", "season"])],
    "hoop_explorer_player_stats": [("index", "ix_he_player_stats_player_season", ["player_id", "season"])],
    "hoopr_player_season_stats": [("index", "ix_hoopr_player_stats_player_season", ["player_id", "season"])],
    "hoopr_player_game_logs": [("index", "ix_hoopr_player_game_logs_player_season", ["player_id", "season"])],
    "roster_depth_charts": [],
    "transfers": [
        ("index", "ix_transfers_player_season", ["player_id", "season"]),
        ("unique", "uq_transfers_player_season", ["player_id", "season"]),
    ],
    "transfer_portal_events": [],
    "roster_snapshot_players": [],
    "roster_baseline_members": [("unique", "uq_roster_baseline_member", ["player_id", "school_id", "season"])],
    "nil_valuations": [("unique", "uq_nil_valuation", ["player_id", "school_id", "season"])],
    "player_team_fit_scores": [("unique", "uq_fit_score", ["player_id", "school_id", "season"])],
    "predictions": [("unique", "uq_prediction", ["player_id", "school_id"])],
    "player_projections": [
        ("partial_unique", "uq_player_projections_neutral", ["player_id", "season", "model_version"], "school_id IS NULL"),
        ("partial_unique", "uq_player_projections_destination", ["player_id", "school_id", "season", "model_version"], "school_id IS NOT NULL"),
        ("index", "ix_player_projections_player_season", ["player_id", "season"]),
    ],
    "recommendations": [("index", "ix_recommendations_user_player", ["user_id", "player_id"])],
    "team_rating_projections": [("unique", "uq_team_rating_projection", ["player_id", "school_id"])],
    "user_shortlists": [("unique", "uq_user_shortlist", ["user_id", "player_id"])],
}

# The 12 tables whose player_id is NOT NULL today (vs. 6 nullable ones) — verified
# against information_schema.columns directly, not assumed from models.py: a real
# schema-drift bug was found this way (user_shortlists.player_id is `nullable=False`
# in models.py but actually nullable in the live DB; the model is wrong, not this set).
NOT_NULL_TABLES = {
    "player_school_seasons", "player_season_stats", "player_archetypes", "roster_depth_charts",
    "transfers", "roster_baseline_members", "nil_valuations", "player_team_fit_scores",
    "predictions", "player_projections", "recommendations", "team_rating_projections",
}

DEPENDENT_TABLES = list(EXTRA_CONSTRAINTS)


def _remap(cols: list[str]) -> list[str]:
    return ["new_player_id" if c == "player_id" else c for c in cols]


def _drop_extra(table: str, spec: tuple) -> None:
    kind, name = spec[0], spec[1]
    if kind == "unique":
        op.drop_constraint(name, table, type_="unique")
    else:  # index, partial_unique
        op.drop_index(name, table_name=table)


def _create_extra(table: str, spec: tuple, *, on_new_columns: bool) -> None:
    kind, name, cols = spec[0], spec[1], spec[2]
    cols = _remap(cols) if on_new_columns else cols
    if kind == "unique":
        op.create_unique_constraint(name, table, cols)
    elif kind == "index":
        op.create_index(name, table, cols)
    elif kind == "partial_unique":
        op.create_index(name, table, cols, unique=True, postgresql_where=sa.text(spec[3]))


def upgrade() -> None:
    bind = op.get_bind()

    missing = bind.execute(sa.text("SELECT count(*) FROM players WHERE new_id IS NULL")).scalar()
    if missing:
        raise RuntimeError(f"{missing} players still have new_id IS NULL — step 1 backfill incomplete, aborting")

    for table in NOT_NULL_TABLES:
        bad = bind.execute(sa.text(f"SELECT count(*) FROM {table} WHERE new_player_id IS NULL")).scalar()
        if bad:
            raise RuntimeError(
                f"{table}: {bad} rows have new_player_id IS NULL but player_id is NOT NULL — "
                "an orphan FK (player_id pointing at a since-deleted player), aborting"
            )

    # Pass 1: drop every dependent table's FK + extra constraints/indexes
    # referencing old player_id — must happen before players_pkey can be
    # dropped (Postgres refuses to drop a PK still referenced by other
    # tables' FK constraints).
    for table in DEPENDENT_TABLES:
        for spec in EXTRA_CONSTRAINTS[table]:
            _drop_extra(table, spec)
        op.drop_constraint(f"{table}_player_id_fkey", table, type_="foreignkey")

    # players PK: old 'id' -> new 'new_id'. Old column/values left untouched.
    op.drop_constraint("players_pkey", "players", type_="primary")
    op.create_primary_key("players_pkey", "players", ["new_id"])

    # Pass 2: point every dependent table at the new columns.
    for table in DEPENDENT_TABLES:
        if table in NOT_NULL_TABLES:
            op.alter_column(table, "new_player_id", nullable=False)
        op.create_foreign_key(
            f"{table}_player_id_fkey", table, "players", ["new_player_id"], ["new_id"],
        )
        for spec in EXTRA_CONSTRAINTS[table]:
            _create_extra(table, spec, on_new_columns=True)


def downgrade() -> None:
    # Mirror of upgrade(): drop new-column constraints/FKs first, swap the
    # PK back, then recreate the original FKs/constraints on the old
    # columns — fully lossless, since step 2 never touches old column data.
    for table in DEPENDENT_TABLES:
        for spec in EXTRA_CONSTRAINTS[table]:
            _drop_extra(table, spec)
        op.drop_constraint(f"{table}_player_id_fkey", table, type_="foreignkey")

    op.drop_constraint("players_pkey", "players", type_="primary")
    op.create_primary_key("players_pkey", "players", ["id"])

    for table in DEPENDENT_TABLES:
        if table in NOT_NULL_TABLES:
            op.alter_column(table, "new_player_id", nullable=True)
        op.create_foreign_key(
            f"{table}_player_id_fkey", table, "players", ["player_id"], ["id"],
        )
        for spec in EXTRA_CONSTRAINTS[table]:
            _create_extra(table, spec, on_new_columns=False)
