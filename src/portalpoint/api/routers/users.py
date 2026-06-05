from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from portalpoint.api.deps import CurrentUser, DbSession
from portalpoint.api.schemas.fit_score import FitWeights
from portalpoint.api.schemas.user import (
    ImportanceWeights,
    ShortlistItem,
    ShortlistResponse,
    UserFilters,
    UserPreferences,
    UserPreferencesUpdate,
)
from portalpoint.db.models import School, UserPreference, UserShortlist

router = APIRouter(prefix="/api/users", tags=["users"])

_DEFAULTS = UserPreferences(
    importance_weights=ImportanceWeights(playing_time=7, nil=5, academics=5, location=5),
    filters=UserFilters(),
    fit_weights=FitWeights(),
)


def _prefs_to_schema(p: UserPreference) -> UserPreferences:
    return UserPreferences(
        importance_weights=ImportanceWeights(
            playing_time=p.importance_playing_time,
            nil=p.importance_nil,
            academics=p.importance_academics,
            location=p.importance_location,
        ),
        filters=UserFilters(**(p.filters or {})),
        fit_weights=FitWeights(
            gap=p.weight_gap,
            scheme=p.weight_scheme,
            opportunity=p.weight_opportunity,
            personal=p.weight_personal,
        ),
    )


def _check_auth(user_id: int, current_user: int) -> None:
    if user_id != current_user:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized")


@router.get("/{user_id}/preferences", response_model=UserPreferences)
async def get_preferences(user_id: int, current_user: CurrentUser, db: DbSession):
    _check_auth(user_id, current_user)
    prefs = (
        await db.execute(select(UserPreference).where(UserPreference.user_id == user_id))
    ).scalar_one_or_none()
    return _prefs_to_schema(prefs) if prefs else _DEFAULTS


@router.put("/{user_id}/preferences", response_model=UserPreferences)
async def update_preferences(
    user_id: int, body: UserPreferencesUpdate, current_user: CurrentUser, db: DbSession
):
    _check_auth(user_id, current_user)
    prefs = (
        await db.execute(select(UserPreference).where(UserPreference.user_id == user_id))
    ).scalar_one_or_none()

    if prefs is None:
        prefs = UserPreference(user_id=user_id)
        db.add(prefs)

    if body.importance_weights is not None:
        prefs.importance_playing_time = body.importance_weights.playing_time
        prefs.importance_nil         = body.importance_weights.nil
        prefs.importance_academics   = body.importance_weights.academics
        prefs.importance_location    = body.importance_weights.location
    if body.fit_weights is not None:
        prefs.weight_gap         = body.fit_weights.gap
        prefs.weight_scheme      = body.fit_weights.scheme
        prefs.weight_opportunity = body.fit_weights.opportunity
        prefs.weight_personal    = body.fit_weights.personal
    if body.filters is not None:
        prefs.filters = body.filters.model_dump(exclude_none=True)

    await db.commit()
    await db.refresh(prefs)
    return _prefs_to_schema(prefs)


@router.get("/{user_id}/shortlist", response_model=ShortlistResponse)
async def get_shortlist(user_id: int, current_user: CurrentUser, db: DbSession):
    _check_auth(user_id, current_user)
    rows = (
        await db.execute(
            select(UserShortlist, School.name.label("school_name"), School.conference)
            .join(School, School.id == UserShortlist.school_id)
            .where(UserShortlist.user_id == user_id)
            .order_by(UserShortlist.added_at.desc())
        )
    ).all()

    schools = [
        ShortlistItem(
            school_id=sl.school_id,
            school_name=school_name,
            conference=conference,
            overall_fit=sl.overall_fit,
            added_at=sl.added_at,
        )
        for sl, school_name, conference in rows
    ]
    return ShortlistResponse(user_id=user_id, schools=schools, total=len(schools))


@router.post("/{user_id}/shortlist/{school_id}", response_model=ShortlistItem, status_code=201)
async def add_to_shortlist(
    user_id: int, school_id: int, current_user: CurrentUser, db: DbSession
):
    _check_auth(user_id, current_user)

    school = (
        await db.execute(select(School).where(School.id == school_id))
    ).scalar_one_or_none()
    if school is None:
        raise HTTPException(status_code=404, detail=f"School {school_id} not found")

    entry = UserShortlist(user_id=user_id, school_id=school_id)
    db.add(entry)
    try:
        await db.commit()
        await db.refresh(entry)
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="School already on shortlist")

    return ShortlistItem(
        school_id=school_id,
        school_name=school.name,
        conference=school.conference,
        overall_fit=None,
        added_at=entry.added_at,
    )


@router.delete("/{user_id}/shortlist/{school_id}", status_code=204)
async def remove_from_shortlist(
    user_id: int, school_id: int, current_user: CurrentUser, db: DbSession
):
    _check_auth(user_id, current_user)
    result = await db.execute(
        delete(UserShortlist).where(
            UserShortlist.user_id == user_id,
            UserShortlist.school_id == school_id,
        )
    )
    await db.commit()
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="School not on shortlist")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
