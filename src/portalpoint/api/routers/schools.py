from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from portalpoint.api.deps import CurrentUser, DbSession
from portalpoint.api.schemas.school import RosterGapResponse
from portalpoint.db.models import RosterStateFeatures, User

router = APIRouter(prefix="/api/schools", tags=["schools"])


@router.get("/roster-gap", response_model=RosterGapResponse)
async def get_roster_gap(current_user: CurrentUser, db: DbSession):
    """The caller's own program's open-minutes roster picture — first read
    consumer of roster_state_features (previously write-only). Resolves
    school_id from the caller rather than taking it as a path param, since
    every current use case ("my biggest hole") is self-referential."""
    school_id = (
        await db.execute(select(User.school_id).where(User.id == current_user))
    ).scalar_one_or_none()
    if school_id is None:
        raise HTTPException(status_code=404, detail="No school associated with this user")

    stmt = (
        select(RosterStateFeatures)
        .where(RosterStateFeatures.school_id == school_id)
        .order_by(RosterStateFeatures.season.desc(), RosterStateFeatures.updated_at.desc())
        .limit(1)
    )
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail=f"No roster snapshot found for school {school_id}")

    open_minutes = row.open_minutes_by_position or {}
    suggested_position, suggested_open_minutes = (
        max(open_minutes.items(), key=lambda kv: kv[1]) if open_minutes else (None, None)
    )

    return RosterGapResponse(
        school_id=school_id,
        season=row.season,
        open_minutes_by_position=open_minutes,
        open_usage_by_position=row.open_usage_by_position,
        suggested_position=suggested_position,
        suggested_open_minutes=suggested_open_minutes,
    )
