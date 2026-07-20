from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import delete, desc, select
from sqlalchemy.exc import IntegrityError

from portalpoint.api.deps import CurrentUser, DbSession
from portalpoint.api.schemas.fit_score import FitWeights
from portalpoint.api.schemas.preference_profile import (
    PreferenceProfile,
    PreferenceProfileCreate,
    PreferenceProfileListResponse,
)
from portalpoint.api.schemas.school import UpdateSchoolRequest, UpdateSchoolResponse
from portalpoint.api.schemas.user import (
    ImportanceWeights,
    ShortlistItem,
    ShortlistResponse,
    UserFilters,
    UserPreferences,
    UserPreferencesUpdate,
)
from portalpoint.db.models import (
    Player,
    PlayerTeamFitScore,
    School,
    User,
    UserPreference,
    UserPreferenceProfile,
    UserShortlist,
)

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


@router.put("/{user_id}/school", response_model=UpdateSchoolResponse)
async def update_school(
    user_id: int, body: UpdateSchoolRequest, current_user: CurrentUser, db: DbSession
):
    _check_auth(user_id, current_user)
    school = (
        await db.execute(select(School).where(School.id == body.school_id))
    ).scalar_one_or_none()
    if school is None:
        raise HTTPException(status_code=404, detail="School not found")

    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    user.school_id = body.school_id
    await db.commit()
    return UpdateSchoolResponse(school_id=body.school_id)


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
        prefs.importance_role_fit = body.importance_weights.role_fit
        prefs.importance_gap_match = body.importance_weights.gap_match
        prefs.importance_program_fit = body.importance_weights.program_fit
    if body.fit_weights is not None:
        prefs.weight_gap = body.fit_weights.gap
        prefs.weight_scheme = body.fit_weights.scheme
        prefs.weight_role = body.fit_weights.role_fit
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
            player_id=str(sl.player_id),
            player_name=player_name,
            position=position,
            overall_fit=sl.overall_fit,
            added_at=sl.added_at,
        )
        for sl, player_name, position in rows
    ]
    return ShortlistResponse(user_id=user_id, players=players, total=len(players))


@router.post("/{user_id}/shortlist/{player_id}", response_model=ShortlistItem, status_code=201)
async def add_to_shortlist(user_id: int, player_id: int, current_user: CurrentUser, db: DbSession):
    _check_auth(user_id, current_user)

    player = (await db.execute(select(Player).where(Player.id == player_id))).scalar_one_or_none()
    if player is None:
        raise HTTPException(status_code=404, detail=f"Player {player_id} not found")

    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one()
    overall_fit = None
    if user.school_id is not None:
        overall_fit = (
            await db.execute(
                select(PlayerTeamFitScore.overall_fit)
                .where(
                    PlayerTeamFitScore.player_id == player_id,
                    PlayerTeamFitScore.school_id == user.school_id,
                    PlayerTeamFitScore.calibration_version.is_not(None),
                )
                .order_by(desc(PlayerTeamFitScore.season))
                .limit(1)
            )
        ).scalar_one_or_none()

    entry = UserShortlist(user_id=user_id, player_id=player_id, overall_fit=overall_fit)
    db.add(entry)
    try:
        await db.commit()
        await db.refresh(entry)
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Player already on shortlist"
        )

    return ShortlistItem(
        player_id=str(player_id),
        player_name=player.full_name,
        position=player.position,
        overall_fit=entry.overall_fit,
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


# ── Saved weight profiles ───────────────────────────────────────────────────
# Additive on top of UserPreference (the single "active" row fit_scores.py
# reads) — named snapshots a user can switch between. Activating a profile
# copies its fields into that row; this table is never read by the fit-score
# computation path itself.


def _profile_to_schema(p: UserPreferenceProfile) -> PreferenceProfile:
    return PreferenceProfile(
        id=p.id,
        name=p.name,
        created_at=p.created_at,
        fit_weights=FitWeights(
            gap=p.weight_gap,
            scheme=p.weight_scheme,
            role_fit=p.weight_role,
            program_fit=p.weight_program,
        ),
        importance_weights=ImportanceWeights(
            scheme_fit=p.importance_scheme_fit,
            role_fit=p.importance_role_fit,
            gap_match=p.importance_gap_match,
            program_fit=p.importance_program_fit,
        ),
        filters=UserFilters(**(p.filters or {})),
    )


@router.get("/{user_id}/preference-profiles", response_model=PreferenceProfileListResponse)
async def list_preference_profiles(user_id: int, current_user: CurrentUser, db: DbSession):
    _check_auth(user_id, current_user)
    rows = (
        (
            await db.execute(
                select(UserPreferenceProfile)
                .where(UserPreferenceProfile.user_id == user_id)
                .order_by(UserPreferenceProfile.created_at)
            )
        )
        .scalars()
        .all()
    )
    return PreferenceProfileListResponse(profiles=[_profile_to_schema(p) for p in rows])


@router.post("/{user_id}/preference-profiles", response_model=PreferenceProfile, status_code=201)
async def create_preference_profile(
    user_id: int, body: PreferenceProfileCreate, current_user: CurrentUser, db: DbSession
):
    _check_auth(user_id, current_user)
    profile = UserPreferenceProfile(
        user_id=user_id,
        name=body.name,
        weight_gap=body.fit_weights.gap,
        weight_scheme=body.fit_weights.scheme,
        weight_role=body.fit_weights.role_fit,
        weight_program=body.fit_weights.program_fit,
        importance_scheme_fit=body.importance_weights.scheme_fit,
        importance_role_fit=body.importance_weights.role_fit,
        importance_gap_match=body.importance_weights.gap_match,
        importance_program_fit=body.importance_weights.program_fit,
        filters=body.filters.model_dump(exclude_none=True),
    )
    db.add(profile)
    try:
        await db.commit()
        await db.refresh(profile)
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail=f"Profile named {body.name!r} already exists")
    return _profile_to_schema(profile)


@router.delete("/{user_id}/preference-profiles/{profile_id}", status_code=204)
async def delete_preference_profile(
    user_id: int, profile_id: int, current_user: CurrentUser, db: DbSession
):
    _check_auth(user_id, current_user)
    result = await db.execute(
        delete(UserPreferenceProfile).where(
            UserPreferenceProfile.id == profile_id,
            UserPreferenceProfile.user_id == user_id,
        )
    )
    await db.commit()
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Profile not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{user_id}/preference-profiles/{profile_id}/activate", response_model=UserPreferences)
async def activate_preference_profile(
    user_id: int, profile_id: int, current_user: CurrentUser, db: DbSession
):
    _check_auth(user_id, current_user)
    profile = (
        await db.execute(
            select(UserPreferenceProfile).where(
                UserPreferenceProfile.id == profile_id,
                UserPreferenceProfile.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile not found")

    prefs = (
        await db.execute(select(UserPreference).where(UserPreference.user_id == user_id))
    ).scalar_one_or_none()
    if prefs is None:
        prefs = UserPreference(user_id=user_id)
        db.add(prefs)

    prefs.weight_gap = profile.weight_gap
    prefs.weight_scheme = profile.weight_scheme
    prefs.weight_role = profile.weight_role
    prefs.weight_program = profile.weight_program
    prefs.importance_scheme_fit = profile.importance_scheme_fit
    prefs.importance_role_fit = profile.importance_role_fit
    prefs.importance_gap_match = profile.importance_gap_match
    prefs.importance_program_fit = profile.importance_program_fit
    prefs.filters = profile.filters

    await db.commit()
    await db.refresh(prefs)
    return _prefs_to_schema(prefs)
