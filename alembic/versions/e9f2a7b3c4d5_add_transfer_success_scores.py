"""add transfer_success_scores table

Revision ID: e9f2a7b3c4d5
Revises: 40beacdccf1e
Create Date: 2026-07-17

Stores empirical Bayes success probability for active portal candidates
(player × destination school × season), scored by the transfer-success-eb-v1
pipeline. One row per (player_id, to_school_id, season, model_version).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "e9f2a7b3c4d5"
down_revision: Union[str, Sequence[str], None] = "40beacdccf1e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "transfer_success_scores",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("player_id", sa.BigInteger(), sa.ForeignKey("players.id"), nullable=False),
        sa.Column("to_school_id", sa.Integer(), sa.ForeignKey("schools.id"), nullable=False),
        sa.Column("season", sa.SmallInteger(), nullable=False),
        sa.Column("player_cluster", sa.SmallInteger(), nullable=True),
        sa.Column("team_offense_cluster_id", sa.SmallInteger(), nullable=True),
        sa.Column("team_defense_cluster_id", sa.SmallInteger(), nullable=True),
        sa.Column("team_cluster_label", sa.String(120), nullable=True),
        sa.Column("success_probability", sa.Float(), nullable=False),
        sa.Column("success_tier", sa.String(20), nullable=True),
        sa.Column("cell_n", sa.Float(), nullable=True),
        sa.Column("shrinkage_w", sa.Float(), nullable=True),
        sa.Column("cluster_success_rate", sa.Float(), nullable=True),
        sa.Column("cell_success_rate", sa.Float(), nullable=True),
        sa.Column("explanation", sa.Text(), nullable=True),
        sa.Column("similar_transfers", JSONB(), nullable=True),
        sa.Column("model_version", sa.String(50), nullable=False),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_unique_constraint(
        "uq_transfer_success_score",
        "transfer_success_scores",
        ["player_id", "to_school_id", "season", "model_version"],
    )
    op.create_index(
        "ix_transfer_success_player_season",
        "transfer_success_scores",
        ["player_id", "season"],
    )
    op.create_index(
        "ix_transfer_success_school_season",
        "transfer_success_scores",
        ["to_school_id", "season"],
    )


def downgrade() -> None:
    op.drop_index("ix_transfer_success_school_season", table_name="transfer_success_scores")
    op.drop_index("ix_transfer_success_player_season", table_name="transfer_success_scores")
    op.drop_constraint("uq_transfer_success_score", "transfer_success_scores", type_="unique")
    op.drop_table("transfer_success_scores")
