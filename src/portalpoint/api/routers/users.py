from datetime import datetime, timezone

from fastapi import APIRouter, Response, status

from portalpoint.api.deps import CurrentUser
from portalpoint.api.schemas.fit_score import FitWeights
from portalpoint.api.schemas.user import (
    ImportanceWeights,
    ShortlistItem,
    ShortlistResponse,
    UserFilters,
    UserPreferences,
    UserPreferencesUpdate,
)

router = APIRouter(prefix="/api/users", tags=["users"])

_STUB_PREFS = UserPreferences(
    importance_weights=ImportanceWeights(playing_time=8, nil=7, academics=5, location=6),
    filters=UserFilters(regions=[], conferences=[]),
    fit_weights=FitWeights(gap=0.20, scheme=0.30, opportunity=0.25, personal=0.25),
)

_STUB_SHORTLIST = [
    ShortlistItem(school_id=301, school_name="Gonzaga",   conference="WCC",      overall_fit=92.0, added_at=datetime(2026, 5, 10, tzinfo=timezone.utc)),
    ShortlistItem(school_id=302, school_name="Duke",      conference="ACC",      overall_fit=87.5, added_at=datetime(2026, 5, 12, tzinfo=timezone.utc)),
    ShortlistItem(school_id=303, school_name="Villanova", conference="Big East", overall_fit=84.0, added_at=datetime(2026, 5, 14, tzinfo=timezone.utc)),
]


@router.get("/{user_id}/preferences", response_model=UserPreferences)
async def get_preferences(user_id: int, current_user: CurrentUser):
    # STUB — replace with DB lookup in Phase 2
    return _STUB_PREFS


@router.put("/{user_id}/preferences", response_model=UserPreferences)
async def update_preferences(user_id: int, body: UserPreferencesUpdate, current_user: CurrentUser):
    # STUB — merge incoming fields over defaults and return
    updated = _STUB_PREFS
    if body.importance_weights is not None:
        updated = updated.model_copy(update={"importance_weights": body.importance_weights})
    if body.filters is not None:
        updated = updated.model_copy(update={"filters": body.filters})
    if body.fit_weights is not None:
        updated = updated.model_copy(update={"fit_weights": body.fit_weights})
    return updated


@router.get("/{user_id}/shortlist", response_model=ShortlistResponse)
async def get_shortlist(user_id: int, current_user: CurrentUser):
    # STUB — replace with DB query in Phase 2
    return ShortlistResponse(user_id=user_id, schools=_STUB_SHORTLIST, total=len(_STUB_SHORTLIST))


@router.post("/{user_id}/shortlist/{school_id}", response_model=ShortlistItem, status_code=201)
async def add_to_shortlist(user_id: int, school_id: int, current_user: CurrentUser):
    # STUB — replace with DB insert + fit score lookup in Phase 2
    return ShortlistItem(
        school_id=school_id,
        school_name=f"School #{school_id}",
        conference="Unknown",
        overall_fit=None,
        added_at=datetime.now(timezone.utc),
    )


@router.delete("/{user_id}/shortlist/{school_id}", status_code=204)
async def remove_from_shortlist(user_id: int, school_id: int, current_user: CurrentUser):
    # STUB — replace with DB delete in Phase 2
    return Response(status_code=status.HTTP_204_NO_CONTENT)
