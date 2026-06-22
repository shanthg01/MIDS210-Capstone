import random

from fastapi import APIRouter, Query

from portalpoint.api.deps import CurrentUser
from portalpoint.api.schemas.prediction import (
    PredictedRole,
    PredictionResponse,
    SHAPExplanation,
    SimilarTransfer,
)

router = APIRouter(prefix="/api/predictions", tags=["predictions"])

_SIMILAR_TRANSFERS = [
    ("Jordan Hayes",  "2023-24", "UNC Greensboro", "Davidson", 14.2, 17.8,  3.6, 22.1, 26.4, 4.1),
    ("Chris Manning", "2022-23", "Fordham",         "VCU",      11.8, 15.1,  3.3, 18.5, 24.0, 3.8),
    ("DeShawn Cole",  "2023-24", "UMES",             "Gonzaga",  12.5, 14.9,  2.4, 20.0, 22.8, 3.2),
]

_SHAP_POOL = [
    ("usage_rate_differential",   +0.8, "Your usage rate exceeds destination team average — expect more offensive responsibility."),
    ("conference_strength_delta", -0.3, "Conference upgrade typically suppresses raw stats by ~0.3 PER; factored in projection."),
    ("three_point_rate_match",    +0.6, "Shot profile closely matches system; minimal adjustment period expected."),
    ("pace_differential",         +0.4, "Destination team runs faster pace, which historically boosts counting stats for your archetype."),
    ("depth_chart_opening",       +1.1, "Two guards departing — direct path to starting minutes drives positive PER projection."),
]


@router.get("", response_model=PredictionResponse)
async def get_prediction(
    current_user: CurrentUser,
    player_id: int = Query(...),
    school_id: int = Query(...),
):
    # STUB — replace with Model 5 (XGBoost + SHAP, temporal CV on 2020-2023 train) in Phase 2
    rng = random.Random(player_id * 1000 + school_id)

    per_change = round(rng.uniform(0.5, 5.5), 1)
    minutes = round(rng.uniform(18.0, 28.0), 1)
    role = rng.choices(
        [PredictedRole.STARTER, PredictedRole.ROTATION, PredictedRole.BENCH],
        weights=[0.45, 0.40, 0.15],
    )[0]

    similar = [
        SimilarTransfer(
            player_name=name,
            season=season,
            from_school=frm,
            to_school=to,
            per_before=pb,
            per_after=pa,
            per_change=pc,
            minutes_before=mb,
            minutes_after=ma,
            outcome_score=score,
        )
        for name, season, frm, to, pb, pa, pc, mb, ma, score in _SIMILAR_TRANSFERS
    ]

    shap = [
        SHAPExplanation(feature=feat, impact=impact, description=desc)
        for feat, impact, desc in rng.sample(_SHAP_POOL, k=3)
    ]

    return PredictionResponse(
        player_id=player_id,
        school_id=school_id,
        predicted_per_change=per_change,
        predicted_minutes=minutes,
        predicted_role=role,
        confidence=round(rng.uniform(0.55, 0.82), 2),
        similar_transfers=similar,
        shap_explanations=shap,
        model_version="pred_v1.0-stub",
    )
