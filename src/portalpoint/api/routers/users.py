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
from portalpoint.db.models import Player, UserPreference, UserShortlist

router = APIRouter(prefix="/api/users", tags=["users"])

_DEFAULTS = UserPreferences(
    importance_weights=ImportanceWeights(scheme_fit=7, role_fit=5, gap_match=5, program_fit=5),
    filters=UserFilters(),
    fit_weights=FitWeights(),
)


def _prefs_to_schema(p: UserPreference) -> UserPreferences:
    return UserPreferences(
        importance_weights=ImportanceWeights(
            scheme_fit=p.importance_scheme_fit,
            role_fit=p.importance_role_fit,
            gap_match=p.importance_gap_match,
            program_fit=p.importance_program_fit,
        ),
        filters=UserFilters(**(p.filters or {})),
        fit_weights=FitWeights(
            gap=p.weight_gap,
            scheme=p.weight_scheme,
            role_fit=p.weight_role,
            program_fit=p.weight_program,
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
        prefs.importance_scheme_fit = body.importance_weights.scheme_fit
        prefs.importance_role_fit   = body.importance_weights.role_fit
        prefs.importance_gap_match  = body.importance_weights.gap_match
        prefs.importance_program_fit = body.importance_weights.program_fit
    if body.fit_weights is not None:
        prefs.weight_gap     = body.fit_weights.gap
        prefs.weight_scheme  = body.fit_weights.scheme
        prefs.weight_role    = body.fit_weights.role_fit
        prefs.weight_program = body.fit_weights.program_fit
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
            select(UserShortlist, Player.full_name.label("player_name"), Player.position)
            .join(Player, Player.id == UserShortlist.player_id)
            .where(UserShortlist.user_id == user_id)
            .order_by(UserShortlist.added_at.desc())
        )
    ).all()

    players = [
        ShortlistItem(
            player_id=sl.player_id,
            player_name=player_name,
            position=position,
            overall_fit=sl.overall_fit,
            added_at=sl.added_at,
        )
        for sl, player_name, position in rows
    ]
    return ShortlistResponse(user_id=user_id, players=players, total=len(players))


@router.post("/{user_id}/shortlist/{player_id}", response_model=ShortlistItem, status_code=201)
async def add_to_shortlist(
    user_id: int, player_id: int, current_user: CurrentUser, db: DbSession
):
    _check_auth(user_id, current_user)

    player = (
        await db.execute(select(Player).where(Player.id == player_id))
    ).scalar_one_or_none()
    if player is None:
        raise HTTPException(status_code=404, detail=f"Player {player_id} not found")

    entry = UserShortlist(user_id=user_id, player_id=player_id)
    db.add(entry)
    try:
        await db.commit()
        await db.refresh(entry)
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Player already on shortlist")

    return ShortlistItem(
        player_id=player_id,
        player_name=player.full_name,
        position=player.position,
        overall_fit=None,
        added_at=entry.added_at,
    )


@router.delete("/{user_id}/shortlist/{player_id}", status_code=204)
async def remove_from_shortlist(
    user_id: int, player_id: int, current_user: CurrentUser, db: DbSession
):
    _check_auth(user_id, current_user)
    result = await db.execute(
        delete(UserShortlist).where(
            UserShortlist.user_id == user_id,
            UserShortlist.player_id == player_id,
        )
    )
    await db.commit()
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Player not on shortlist")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
