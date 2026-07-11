"""team_system_profile_stale_flag

Adds stale_flag and stale_reason columns to team_system_profiles.

Set by the news-monitoring agent's coach_departure tool when a coaching change
is detected — signals that the school's M2 team_system_profile may no longer
reflect the current system (new staff haven't been profiled yet).

Consumed by fit_score_service.get_fit_score() to surface a
scheme_fit_stale warning on FitScoreResponse.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "b1d3f5a7c9e2"
down_revision = "2547054ae5cb"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "team_system_profiles",
        sa.Column(
            "stale_flag",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "team_system_profiles",
        sa.Column("stale_reason", sa.String(100), nullable=True),
    )
    op.create_index(
        "ix_team_system_profiles_stale",
        "team_system_profiles",
        ["school_id", "stale_flag"],
        postgresql_where=sa.text("stale_flag = true"),
    )


def downgrade() -> None:
    op.drop_index("ix_team_system_profiles_stale", table_name="team_system_profiles")
    op.drop_column("team_system_profiles", "stale_reason")
    op.drop_column("team_system_profiles", "stale_flag")
