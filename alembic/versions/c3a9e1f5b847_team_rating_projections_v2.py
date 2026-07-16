"""team_rating_projections_v2 — add season + breakdown columns, fix unique constraint

Revision ID: c3a9e1f5b847
Revises: b2f0d4c8a917
Create Date: 2026-07-02

Changes:
  - Add season (smallint NOT NULL) — the table had no season column; the old
    UniqueConstraint(player_id, school_id) can't store multi-season results.
  - Drop uq_team_rating_projection and recreate as (player_id, school_id, season).
  - Add breakdown columns: baseline_adj_o, baseline_adj_d, projected_adj_o,
    projected_adj_d, candidate_usage_role, explanation jsonb, minutes_distribution jsonb.
  - Widen model_version from varchar(20) → varchar(40) (matches other output tables).
  - Add ix_team_rating_school_season index for program-facing queries.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c3a9e1f5b847"
down_revision: Union[str, Sequence[str], None] = "b2f0d4c8a917"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add season with a temporary default so existing rows (none expected in
    #    prod, but safe to handle) get a value before we set NOT NULL.
    op.add_column(
        "team_rating_projections",
        sa.Column("season", sa.SmallInteger(), nullable=True, server_default="2027"),
    )
    op.execute("UPDATE team_rating_projections SET season = 2027 WHERE season IS NULL")
    op.alter_column("team_rating_projections", "season", nullable=False, server_default=None)

    # 2. Add new breakdown columns (all nullable — populated by model write).
    op.add_column("team_rating_projections", sa.Column("baseline_adj_o", sa.Float(), nullable=True))
    op.add_column("team_rating_projections", sa.Column("baseline_adj_d", sa.Float(), nullable=True))
    op.add_column("team_rating_projections", sa.Column("projected_adj_o", sa.Float(), nullable=True))
    op.add_column("team_rating_projections", sa.Column("projected_adj_d", sa.Float(), nullable=True))
    op.add_column("team_rating_projections", sa.Column("candidate_usage_role", sa.String(40), nullable=True))
    op.add_column("team_rating_projections", sa.Column("explanation", sa.dialects.postgresql.JSONB(), nullable=True))
    op.add_column("team_rating_projections", sa.Column("minutes_distribution", sa.dialects.postgresql.JSONB(), nullable=True))

    # 3. Widen model_version.
    op.alter_column(
        "team_rating_projections",
        "model_version",
        type_=sa.String(40),
        existing_type=sa.String(20),
        existing_nullable=False,
    )

    # 4. Fix unique constraint: drop old (player_id, school_id), add (player_id, school_id, season).
    op.drop_constraint("uq_team_rating_projection", "team_rating_projections", type_="unique")
    op.create_unique_constraint(
        "uq_team_rating_projection",
        "team_rating_projections",
        ["player_id", "school_id", "season"],
    )

    # 5. Index for program-facing query (school_id, season).
    op.create_index(
        "ix_team_rating_school_season",
        "team_rating_projections",
        ["school_id", "season"],
    )


def downgrade() -> None:
    op.drop_index("ix_team_rating_school_season", table_name="team_rating_projections")

    op.drop_constraint("uq_team_rating_projection", "team_rating_projections", type_="unique")
    op.create_unique_constraint(
        "uq_team_rating_projection",
        "team_rating_projections",
        ["player_id", "school_id"],
    )

    op.alter_column(
        "team_rating_projections",
        "model_version",
        type_=sa.String(20),
        existing_type=sa.String(40),
        existing_nullable=False,
    )

    op.drop_column("team_rating_projections", "minutes_distribution")
    op.drop_column("team_rating_projections", "explanation")
    op.drop_column("team_rating_projections", "candidate_usage_role")
    op.drop_column("team_rating_projections", "projected_adj_d")
    op.drop_column("team_rating_projections", "projected_adj_o")
    op.drop_column("team_rating_projections", "baseline_adj_d")
    op.drop_column("team_rating_projections", "baseline_adj_o")
    op.drop_column("team_rating_projections", "season")
