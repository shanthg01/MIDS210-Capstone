from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from portalpoint.api.deps import CurrentUser, DbSession
from portalpoint.api.schemas.school import (
    RosterGapResponse,
    SchoolListItem,
    SchoolListResponse,
    TeamSystemProfileResponse,
)
from portalpoint.db.models import RosterStateFeatures, School, TeamSystemProfile, User
from portalpoint.modeling.team_clustering import DEFENSE_LABELS, DEFENSE_UNAVAILABLE_LABEL, OFFENSE_LABELS

router = APIRouter(prefix="/api/schools", tags=["schools"])


# Public — used by the signup picker, before a user has a token.
@router.get("", response_model=SchoolListResponse)
async def list_schools(db: DbSession):
    rows = (
        await db.execute(select(School.id, School.name, School.conference).order_by(School.name))
    ).all()
    return SchoolListResponse(
        schools=[SchoolListItem(school_id=r.id, name=r.name, conference=r.conference) for r in rows]
    )


@router.get("/system-profile", response_model=TeamSystemProfileResponse)
async def get_system_profile(current_user: CurrentUser, db: DbSession):
    """The caller's own program's team-system archetype (Model #2) — same
    self-referential pattern as roster-gap. offense_label/defense_label are
    reconstructed from the stored cluster ids (only the combined system_label
    string is persisted) via the same OFFENSE_LABELS/DEFENSE_LABELS maps
    team_clustering.py used to assign them."""
    school_id = (
        await db.execute(select(User.school_id).where(User.id == current_user))
    ).scalar_one_or_none()
    if school_id is None:
        raise HTTPException(status_code=404, detail="No school associated with this user")

    stmt = (
        select(TeamSystemProfile)
        .where(TeamSystemProfile.school_id == school_id)
        .order_by(TeamSystemProfile.season.desc())
        .limit(1)
    )
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail=f"No team system profile found for school {school_id}")

    offense_label = OFFENSE_LABELS.get(row.offense_cluster_id) if row.offense_cluster_id is not None else None
    defense_label = (
        DEFENSE_LABELS.get(row.defense_cluster_id, DEFENSE_UNAVAILABLE_LABEL)
        if row.defense_cluster_id is not None
        else DEFENSE_UNAVAILABLE_LABEL
    )

    return TeamSystemProfileResponse(
        school_id=school_id,
        season=row.season,
        system_label=row.system_label,
        offense_label=offense_label,
        defense_label=defense_label,
    )


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
