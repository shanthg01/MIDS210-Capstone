"""transfer_success_scores row -> PredictionResponse mapping."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from portalpoint.api.schemas.prediction import PredictionResponse, SimilarTransfer
from portalpoint.db.models import TransferSuccessScore
from portalpoint.modeling.transfer_success import MODEL_VERSION

_CURRENT_SEASON_FALLBACK = 2027


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _map_similar_transfer(raw: dict[str, Any]) -> SimilarTransfer | None:
    try:
        return SimilarTransfer(
            player_name=str(raw["player_name"]),
            season=int(raw["season"]),
            success_label=bool(raw["success_label"]),
            actual_value_per_100=float(raw["actual_value_per_100"]),
            projected_value_per_100=float(raw["projected_value_per_100"]),
            value_vs_projection=float(raw["value_vs_projection"]),
            minutes_drift=_optional_float(raw.get("minutes_drift")),
            usage_drift=_optional_float(raw.get("usage_drift")),
            post_minutes_per_game=_optional_float(raw.get("post_minutes_per_game")),
            projected_minutes=_optional_float(raw.get("projected_minutes")),
            post_usage_rate=_optional_float(raw.get("post_usage_rate")),
            projected_usage=_optional_float(raw.get("projected_usage")),
        )
    except (KeyError, TypeError, ValueError):
        return None


def map_similar_transfers(raw: list[Any] | None) -> list[SimilarTransfer]:
    if not raw:
        return []
    mapped: list[SimilarTransfer] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        comp = _map_similar_transfer(item)
        if comp is not None:
            mapped.append(comp)
    return mapped


def row_to_prediction(row: TransferSuccessScore) -> PredictionResponse:
    return PredictionResponse(
        player_id=str(row.player_id),
        school_id=int(row.to_school_id),
        season=int(row.season),
        success_probability=float(row.success_probability),
        success_tier=row.success_tier,
        explanation=row.explanation,
        similar_transfers=map_similar_transfers(row.similar_transfers),
        model_version=str(row.model_version),
    )


async def get_current_season(db: AsyncSession) -> int:
    """Most recent non-expired season in transfer_success_scores."""
    now = datetime.now(timezone.utc)
    result = await db.execute(
        select(func.max(TransferSuccessScore.season)).where(
            TransferSuccessScore.expires_at > now,
            TransferSuccessScore.model_version == MODEL_VERSION,
        )
    )
    season = result.scalar_one_or_none()
    return int(season) if season is not None else _CURRENT_SEASON_FALLBACK


async def get_prediction(
    db: AsyncSession,
    player_id: int,
    school_id: int,
    season: int,
) -> PredictionResponse | None:
    """Active transfer-success row for the player × destination school pair."""
    now = datetime.now(timezone.utc)
    result = await db.execute(
        select(TransferSuccessScore)
        .where(
            TransferSuccessScore.player_id == player_id,
            TransferSuccessScore.to_school_id == school_id,
            TransferSuccessScore.season == season,
            TransferSuccessScore.model_version == MODEL_VERSION,
            TransferSuccessScore.expires_at > now,
        )
        .order_by(TransferSuccessScore.computed_at.desc())
        .limit(1)
    )
    row = result.scalar_one_or_none()
    if row is None:
        return None
    return row_to_prediction(row)
