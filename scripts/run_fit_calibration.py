"""Calibrate and persist the canonical four-component fit score.

Run after Scheme Fit, Gap Matching, Playing Time, and Team Rating Projection.
The job uses portal candidates as each destination school's reference
population, converts all four raw signals to a comparable scale, shrinks low-
confidence signals toward 50, and writes the canonical Overall Fit.

This script intentionally does not run as part of migrations or application
startup. A shared/live backfill is a separately approved operational step.

Usage:
  uv run python scripts/run_fit_calibration.py --season 2027 --dry-run
  uv run python scripts/run_fit_calibration.py --season 2027
  uv run python scripts/run_fit_calibration.py --season 2027 --school-id 301
"""

from __future__ import annotations

import argparse
import json
import logging

import numpy as np
import pandas as pd
from psycopg2.extras import execute_values
from sqlalchemy import text

from portalpoint.modeling.fit_calibration import (
    DEFAULT_FIT_WEIGHTS,
    MODEL_VERSION,
    calibrate_by_school,
    calibrate_series,
    canonical_overall,
    confidence_adjust,
)
from portalpoint.modeling.io import get_sync_engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

LOAD_SQL = """
SELECT
    ptf.id,
    ptf.school_id,
    ptf.scheme_fit,
    ptf.gap_match,
    ptf.role_fit,
    ptf.program_fit,
    ptf.breakdown,
    COALESCE(tsp.stale_flag, false) AS scheme_stale,
    ptp.minutes_ci_lower,
    ptp.minutes_ci_upper,
    ptp.usage_role_confidence,
    ptp.data_quality_flags AS role_quality_flags
FROM player_team_fit_scores ptf
LEFT JOIN team_system_profiles tsp
  ON tsp.school_id = ptf.school_id AND tsp.season = ptf.season
LEFT JOIN playing_time_projections ptp
  ON ptp.player_id = ptf.player_id
 AND ptp.school_id = ptf.school_id
 AND ptp.season = ptf.season
 AND ptp.model_version = 'playing-time-rotation-v2'
WHERE ptf.season = :season
  AND ptf.is_portal_candidate = true
"""

UPDATE_SQL = """
UPDATE player_team_fit_scores AS ptf
SET calibrated_scheme_fit = data.scheme_fit,
    calibrated_gap_match = data.gap_match,
    calibrated_role_fit = data.role_fit,
    calibrated_program_fit = data.program_fit,
    overall_fit = data.overall_fit,
    overall_confidence = data.overall_confidence,
    component_confidences = data.component_confidences::jsonb,
    data_quality_flags = data.data_quality_flags::jsonb,
    calibration_version = data.calibration_version,
    weight_gap = 0.30,
    weight_scheme = 0.25,
    weight_role = 0.25,
    weight_program = 0.20
FROM (VALUES %s) AS data(
    id, scheme_fit, gap_match, role_fit, program_fit, overall_fit,
    overall_confidence, component_confidences, data_quality_flags, calibration_version
)
WHERE ptf.id = data.id
"""


def _gap_confidence(breakdown: object) -> float:
    if not isinstance(breakdown, dict):
        return 0.0
    return float(np.clip((breakdown.get("gap") or {}).get("gap_reliability", 0.0), 0.0, 1.0))


def _raw_gap_score(stored_score: float, breakdown: object) -> float:
    """Undo Gap v4's old shrink-to-15 so confidence is applied exactly once."""
    if not isinstance(breakdown, dict):
        return float(stored_score)
    raw = (breakdown.get("gap") or {}).get("raw_gap_match")
    return float(stored_score if raw is None else raw)


def _role_confidence(frame: pd.DataFrame) -> pd.Series:
    has_projection = frame["minutes_ci_lower"].notna() & frame["minutes_ci_upper"].notna()
    width = (frame["minutes_ci_upper"] - frame["minutes_ci_lower"]).clip(lower=0.0)
    interval_quality = (1.0 - width / 40.0).clip(0.25, 1.0)
    usage_quality = pd.to_numeric(frame["usage_role_confidence"], errors="coerce").fillna(0.5)
    return (0.6 * usage_quality + 0.4 * interval_quality).where(has_projection, 0.0).clip(0.0, 1.0)


def _program_confidence(frame: pd.DataFrame) -> pd.Series:
    """Program Fit is fully descoped — no real model backs it, so it never
    carries confidence. confidence_adjust() shrinks it to neutral 50 for every
    row regardless of what calibrate_series ranks it at."""
    return pd.Series(0.0, index=frame.index)


def calibrate_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Pure dataframe step, split out so the backfill can be tested safely."""
    scored = frame.copy()
    scored["gap_match"] = [
        _raw_gap_score(score, breakdown)
        for score, breakdown in zip(scored["gap_match"], scored["breakdown"])
    ]
    scored = calibrate_by_school(scored)

    scored["scheme_confidence"] = np.where(
        scored["scheme_stale"] | scored["scheme_fit"].le(0.0), 0.0, 1.0
    )
    scored["gap_confidence"] = scored["breakdown"].map(_gap_confidence)
    scored["role_confidence"] = _role_confidence(scored)
    scored["program_confidence"] = _program_confidence(scored)

    confidence_columns = {
        "scheme_fit": "scheme_confidence",
        "gap_match": "gap_confidence",
        "role_fit": "role_confidence",
        "program_fit": "program_confidence",
    }
    for component, confidence_col in confidence_columns.items():
        scored[component] = confidence_adjust(
            scored[f"calibrated_{component}"], scored[confidence_col]
        )

    scored["weighted_overall_fit"] = canonical_overall(scored)
    scored["overall_fit"] = scored.groupby("school_id", group_keys=False)[
        "weighted_overall_fit"
    ].transform(calibrate_series)
    scored["overall_confidence"] = sum(
        scored[confidence_columns[component]] * weight
        for component, weight in DEFAULT_FIT_WEIGHTS.items()
    )
    return scored


def _records(scored: pd.DataFrame) -> list[tuple]:
    records: list[tuple] = []
    for row in scored.to_dict("records"):
        confidences = {
            "scheme_fit": round(float(row["scheme_confidence"]), 4),
            "gap_match": round(float(row["gap_confidence"]), 4),
            "role_fit": round(float(row["role_confidence"]), 4),
            "program_fit": round(float(row["program_confidence"]), 4),
        }
        flags = {
            "stale_scheme_fit": bool(row["scheme_stale"]),
            "low_gap_confidence": confidences["gap_match"] < 0.5,
            "missing_role_projection": confidences["role_fit"] == 0.0,
            "missing_program_fit": True,  # Program Fit descoped — always a stub
        }
        records.append(
            (
                int(row["id"]),
                round(float(row["scheme_fit"]), 2),
                round(float(row["gap_match"]), 2),
                round(float(row["role_fit"]), 2),
                round(float(row["program_fit"]), 2),
                round(float(row["overall_fit"]), 2),
                round(float(row["overall_confidence"]), 4),
                json.dumps(confidences),
                json.dumps(flags),
                MODEL_VERSION,
            )
        )
    return records


def main() -> None:
    parser = argparse.ArgumentParser(description="Calibrate canonical fit scores")
    parser.add_argument("--season", type=int, required=True)
    parser.add_argument("--school-id", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    engine = get_sync_engine()
    sql = LOAD_SQL
    params: dict[str, int] = {"season": args.season}
    if args.school_id is not None:
        sql += " AND ptf.school_id = :school_id"
        params["school_id"] = args.school_id

    with engine.connect() as connection:
        frame = pd.read_sql(text(sql), connection, params=params)
    if frame.empty:
        log.warning("No portal-candidate fit rows found")
        return

    scored = calibrate_frame(frame)
    log.info(
        "Calibrated %d rows across %d schools; overall mean=%.2f std=%.2f "
        "p05=%.2f p50=%.2f p95=%.2f range=%.2f..%.2f",
        len(scored),
        scored["school_id"].nunique(),
        scored["overall_fit"].mean(),
        scored["overall_fit"].std(ddof=0),
        scored["overall_fit"].quantile(0.05),
        scored["overall_fit"].quantile(0.50),
        scored["overall_fit"].quantile(0.95),
        scored["overall_fit"].min(),
        scored["overall_fit"].max(),
    )
    if args.dry_run:
        return

    records = _records(scored)
    raw_connection = engine.raw_connection()
    try:
        with raw_connection.cursor() as cursor:
            execute_values(cursor, UPDATE_SQL, records, page_size=2000)
        raw_connection.commit()
    finally:
        raw_connection.close()
    log.info("Persisted %d %s rows", len(records), MODEL_VERSION)


if __name__ == "__main__":
    main()
