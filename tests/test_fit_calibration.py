import pandas as pd
import pytest

from portalpoint.modeling.fit_calibration import (
    DEFAULT_FIT_WEIGHTS,
    calibrate_by_school,
    calibrate_series,
    canonical_overall,
    confidence_adjust,
    normalized_weights,
    percentile_to_score,
)
from scripts.run_fit_calibration import calibrate_frame


def test_percentile_scale_has_stable_interpretation() -> None:
    assert percentile_to_score(0.50) == pytest.approx(50.0)
    assert percentile_to_score(0.25) == pytest.approx(36.51, abs=0.02)
    assert percentile_to_score(0.75) == pytest.approx(63.49, abs=0.02)
    assert percentile_to_score(0.05) == pytest.approx(17.10, abs=0.02)
    assert percentile_to_score(0.95) == pytest.approx(82.90, abs=0.02)


def test_calibrate_series_preserves_order_and_ties() -> None:
    result = calibrate_series(pd.Series([90.0, 90.0, 70.0, 50.0]))
    assert result.iloc[0] == result.iloc[1]
    assert result.iloc[0] > result.iloc[2] > result.iloc[3]


def test_calibration_is_destination_school_relative() -> None:
    frame = pd.DataFrame(
        {
            "school_id": [1, 1, 1, 2, 2, 2],
            "scheme_fit": [70.0, 80.0, 90.0, 10.0, 20.0, 30.0],
        }
    )
    result = calibrate_by_school(frame, components=("scheme_fit",))
    assert result.loc[0, "calibrated_scheme_fit"] == pytest.approx(
        result.loc[3, "calibrated_scheme_fit"]
    )
    assert result.loc[2, "calibrated_scheme_fit"] == pytest.approx(
        result.loc[5, "calibrated_scheme_fit"]
    )


def test_low_confidence_shrinks_to_neutral() -> None:
    score = pd.Series([80.0, 80.0, 20.0])
    confidence = pd.Series([1.0, 0.0, 0.5])
    result = confidence_adjust(score, confidence)
    assert list(result) == pytest.approx([80.0, 50.0, 35.0])


def test_canonical_overall_uses_program_fit() -> None:
    frame = pd.DataFrame(
        [{"scheme_fit": 80.0, "gap_match": 60.0, "role_fit": 40.0, "program_fit": 90.0}]
    )
    score = canonical_overall(frame)
    expected = sum(frame.iloc[0][key] * weight for key, weight in DEFAULT_FIT_WEIGHTS.items())
    assert score.iloc[0] == pytest.approx(expected)


def test_personalized_weights_are_normalized() -> None:
    result = normalized_weights(
        {"scheme_fit": 2.0, "gap_match": 2.0, "role_fit": 2.0, "program_fit": 2.0}
    )
    assert set(result.values()) == {0.25}


def test_backfill_frame_uses_raw_gap_and_neutral_program_fit() -> None:
    frame = pd.DataFrame(
        {
            "id": range(5),
            "school_id": [1] * 5,
            "scheme_fit": [60.0, 70.0, 80.0, 90.0, 95.0],
            # Stored value can be low because Gap v4 already confidence-shrank it.
            "gap_match": [15.0, 20.0, 30.0, 40.0, 50.0],
            "role_fit": [30.0, 40.0, 50.0, 60.0, 70.0],
            "program_fit": [50.0] * 5,  # descoped stub — constant for every row
            "breakdown": [
                {"gap": {"raw_gap_match": raw, "gap_reliability": 1.0}}
                for raw in [90.0, 80.0, 70.0, 60.0, 50.0]
            ],
            "scheme_stale": [False] * 5,
            "minutes_ci_lower": [10.0] * 5,
            "minutes_ci_upper": [20.0] * 5,
            "usage_role_confidence": [1.0] * 5,
            "role_quality_flags": [None] * 5,
        }
    )
    result = calibrate_frame(frame)

    # Raw Gap order is descending even though the stored reliability-adjusted
    # values were ascending.
    assert result.loc[0, "gap_match"] > result.loc[4, "gap_match"]
    # Program Fit is a constant stub — calibration collapses every row to 50,
    # and confidence_adjust keeps it at 50 regardless (confidence is always 0).
    assert result.loc[0, "program_fit"] == pytest.approx(50.0)
    assert result.loc[0, "program_confidence"] == 0.0
    assert result["overall_fit"].between(10.0, 90.0).all()
