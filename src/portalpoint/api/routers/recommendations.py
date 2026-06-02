from datetime import datetime, timezone

from fastapi import APIRouter, Query

from portalpoint.api.schemas.recommendation import FitComponents, RecommendationItem, RecommendationsResponse

router = APIRouter(prefix="/api/recommendations", tags=["recommendations"])

# (school_id, name, conference, overall, gap, scheme, opportunity, personal, reasoning)
_SCHOOL_STUBS = [
    (301, "Gonzaga",         "WCC",      92.0, 95.0, 90.0, 88.0, 67.0, "Strong scheme fit — 3PT-heavy offense matches your shooting profile exactly."),
    (302, "Duke",            "ACC",      87.5, 85.0, 92.0, 78.0, 72.0, "High scheme match — drive-and-kick system maximizes your off-ball movement."),
    (303, "Villanova",       "Big East", 84.0, 88.0, 81.0, 82.0, 70.0, "Two guards departing; clear path to 24+ minutes as a starter."),
    (304, "Xavier",          "Big East", 82.1, 91.0, 75.0, 80.0, 64.0, "Top gap match — they need a shooting guard and your archetype fits perfectly."),
    (305, "St. Mary's",      "WCC",      80.8, 82.0, 84.0, 74.0, 71.0, "Similar pace to current program — minimal statistical adjustment expected."),
    (306, "Davidson",        "A-10",     79.5, 78.0, 80.0, 76.0, 68.0, "Strong NIL market and academic programs align with your stated priorities."),
    (307, "Butler",          "Big East", 77.3, 75.0, 72.0, 79.0, 65.0, "Coaching system emphasizes ball movement matching your assist profile."),
    (308, "VCU",             "A-10",     76.0, 70.0, 78.0, 75.0, 62.0, "High-tempo offense suits your usage profile and shot creation volume."),
    (309, "Creighton",       "Big East", 74.9, 82.0, 70.0, 72.0, 60.0, "Familiar conference opponent; scheme transition risk is low."),
    (310, "Loyola Chicago",  "MVC",      73.2, 77.0, 68.0, 70.0, 71.0, "Strong academic offerings and regional fit match your preference weights."),
]


@router.get("", response_model=RecommendationsResponse)
async def get_recommendations(user_id: int = Query(...)):
    # STUB — replace with Model 7 (30% SVD collab filter + 30% content-based + 40% fit scores)
    items = [
        RecommendationItem(
            rank=i + 1,
            school_id=school_id,
            school_name=name,
            conference=conf,
            overall_fit=fit,
            components=FitComponents(
                gap_match=gap,
                scheme_fit=scheme,
                opportunity=opp,
                personal_fit=personal,
            ),
            reasoning=reason,
        )
        for i, (school_id, name, conf, fit, gap, scheme, opp, personal, reason) in enumerate(_SCHOOL_STUBS)
    ]
    return RecommendationsResponse(
        player_id=user_id,
        recommendations=items,
        total=len(items),
        generated_at=datetime.now(timezone.utc),
        model_version="rec_v1.0-stub",
    )
