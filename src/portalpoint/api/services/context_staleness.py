from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from portalpoint.api.schemas.common import ContextStaleness
from portalpoint.db.models import TeamSystemProfile


AFFECTED_MODELS = ["scheme_fit", "gap_match", "team_rating_projection", "playing_time"]


def context_staleness_payload(is_stale: bool, reason: str | None) -> ContextStaleness:
    return ContextStaleness(
        is_stale=is_stale,
        reason=reason if is_stale else None,
        affected_models=list(AFFECTED_MODELS) if is_stale else [],
    )


async def get_context_staleness(
    db: AsyncSession,
    school_id: int,
    season: int,
) -> ContextStaleness:
    row = (
        await db.execute(
            select(TeamSystemProfile.stale_flag, TeamSystemProfile.stale_reason).where(
                TeamSystemProfile.school_id == school_id,
                TeamSystemProfile.season == season,
            )
        )
    ).first()
    return context_staleness_payload(
        bool(row.stale_flag) if row else False,
        row.stale_reason if row else None,
    )
