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


def test_real_fit_score_program_fit_defaults_to_row_value_when_ungraded() -> None:
    # No program_fit_user_input passed — shared row's raw program_fit (50.0
    # canonical placeholder) is what's shown, flagged as ungraded.
    response = real_fit_score(_fit_score_row({}))

    assert response.program_fit == 50.0
    assert response.data_quality_flags["missing_program_fit"] is True
    assert response.component_confidences.program_fit == 0.0
    # overall_fit (shared/canonical) is never touched by any user's grade.
    assert response.overall_fit == 62.0


def test_real_fit_score_program_fit_uses_user_grade_when_present() -> None:
    # Per-user design decision (2026-07-21): program_fit shown to this user is
    # their own qualitative grade, not the shared row's 50.0 placeholder —
    # but overall_fit (the shared, cross-user canonical score) stays untouched.
    response = real_fit_score(
        _fit_score_row({}),
        program_fit_user_input=88.0,
        program_fit_user_input_notes="Great locker-room fit",
    )

    assert response.program_fit == 88.0
    assert response.program_fit_user_input == 88.0
    assert response.program_fit_user_input_notes == "Great locker-room fit"
    assert response.data_quality_flags["missing_program_fit"] is False
    assert response.component_confidences.program_fit == 1.0
    assert response.overall_fit == 62.0  # shared row untouched
    # raw_components keeps the true raw DB value for diagnostics, not the
    # displayed/graded value.
    assert response.raw_components.program_fit == 50.0
    # personalized_fit should reflect the graded value, not the raw 50.0.
    assert response.personalized_fit is not None
    assert response.personalized_fit != response.overall_fit
