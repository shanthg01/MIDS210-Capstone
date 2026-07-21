from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from portalpoint.api.services.fit_score_service import real_fit_score


def _fit_score_row(breakdown: dict) -> SimpleNamespace:
    return SimpleNamespace(
        player_id=1,
        school_id=10,
        season=2026,
        overall_fit=70.0,
        gap_match=65.0,
        scheme_fit=80.0,
        role_fit=50.0,
        program_fit=50.0,
        weight_gap=0.2,
        weight_scheme=0.3,
        weight_role=0.25,
        weight_program=0.25,
        breakdown=breakdown,
        computed_at=datetime.now(timezone.utc),
        model_version="test",
        is_portal_candidate=True,
    )


def test_real_fit_score_exposes_cosine_explanations() -> None:
    contribution = {"feature": "three_point_rate", "contribution": 80.0}
    gap_contribution = {
        "feature": "usage_rate",
        "contribution": 60.0,
        "calibrated_contribution": 30.0,
    }
    response = real_fit_score(
        _fit_score_row(
            {
                "scheme": {
                    "cosine_contributions": [contribution],
                    "cosine_score_adjustment": 0.0,
                },
                "gap": {
                    "raw_gap_match": 60.0,
                    "calibrated_gap_match": 37.5,
                    "gap_reliability": 0.5,
                    "cosine_contributions": [gap_contribution],
                    "raw_score_adjustment": 0.0,
                    "reliability_baseline_contribution": 7.5,
                    "calibrated_score_adjustment": 0.0,
                },
            }
        )
    )

    assert response.breakdown.scheme.cosine_contributions is not None
    assert response.breakdown.scheme.cosine_contributions[0].contribution == 80.0
    assert response.breakdown.gap.cosine_contributions is not None
    assert response.breakdown.gap.cosine_contributions[0].calibrated_contribution == 30.0
    assert response.breakdown.gap.reliability_baseline_contribution == 7.5


def test_real_fit_score_keeps_old_breakdowns_backward_compatible() -> None:
    response = real_fit_score(_fit_score_row({}))

    assert response.breakdown.scheme.cosine_contributions is None
    assert response.breakdown.gap.cosine_contributions is None
    assert response.breakdown.gap.raw_gap_match is None
