"""Shared transfer_success_scores row -> PredictionResponse mapping.

Used by both predictions.py (single player x school lookup) and
comparison.py (N players x one program) — same real-row-or-stub split
fit_score_service.py uses for player_team_fit_scores.
"""
import random
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from portalpoint.api.schemas.prediction import PredictionResponse, SimilarTransfer, SuccessTier
from portalpoint.db.models import TransferSuccessScore
from portalpoint.modeling.transfer_success import SERVING_MODEL_VERSION

_STUB_MODEL_VERSION = "pred_v1.0-stub"


def stub_prediction(player_id: int, school_id: int) -> PredictionResponse:
    # Fallback only — player/school pair outside transfer_success_scores scope
    # (e.g. not an active portal candidate for a scored season).
    rng = random.Random(player_id * 1000 + school_id)
    probability = round(rng.uniform(0.35, 0.70), 3)
    if probability < 0.35:
        tier = SuccessTier.VERY_LOW
    elif probability < 0.50:
        tier = SuccessTier.LOW
    elif probability < 0.65:
        tier = SuccessTier.MODERATE
    elif probability < 0.80:
        tier = SuccessTier.HIGH
    else:
        tier = SuccessTier.VERY_HIGH
    return PredictionResponse(
        player_id=str(player_id),
        school_id=school_id,
        success_probability=probability,
        success_tier=tier,
        cell_n=None,
        shrinkage_w=None,
        explanation="No scored transfer-success row for this player/school pair yet.",
        similar_transfers=[],
        model_version=_STUB_MODEL_VERSION,
    )


def real_prediction(row: TransferSuccessScore) -> PredictionResponse:
    comps = row.similar_transfers or []
    return PredictionResponse(
        player_id=str(row.player_id),
        school_id=row.to_school_id,
        success_probability=row.success_probability,
        success_tier=SuccessTier(row.success_tier) if row.success_tier else SuccessTier.MODERATE,
        cell_n=row.cell_n,
        shrinkage_w=row.shrinkage_w,
        explanation=row.explanation or "",
        similar_transfers=[SimilarTransfer(**c) for c in comps],
        model_version=row.model_version,
    )


async def get_prediction(
    db: AsyncSession, player_id: int, school_id: int, season: int
) -> PredictionResponse:
    """Real DB row when available; stub when outside model scope."""
    result = await db.execute(
        select(TransferSuccessScore)
        .where(
            TransferSuccessScore.player_id == player_id,
            TransferSuccessScore.to_school_id == school_id,
            TransferSuccessScore.season == season,
            TransferSuccessScore.model_version == SERVING_MODEL_VERSION,
            TransferSuccessScore.expires_at > datetime.now(timezone.utc),
        )
        .order_by(TransferSuccessScore.computed_at.desc())
        .limit(1)
    )
    row = result.scalar_one_or_none()
    if row is not None:
        return real_prediction(row)
    return stub_prediction(player_id, school_id)
