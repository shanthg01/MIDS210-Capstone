"""
scripts/run_recommendations.py

Non-interactive run of the 2-stage recommendation engine.

For each active user of a school:
  1. Reads their saved fit weights from user_preferences table (Option B).
  2. Assembles a candidate pool via SQL (availability filter only, no ranking).
  3. Runs Stage 1 (vectorized rank score → Top-50) in Python, including the
     normalized Team Rating Projection signal (team_impact_fit).
  4. Runs Stage 2 (re-rank with user weights → Top-10) per user.
  5. Upserts results to the recommendations table.

Stage 1 moved from SQL to Python (rec-v1.2) now that team_rating_projections
is real — team_impact_fit's clip+rescale can't be expressed as a static SQL
weight constant the way scheme/gap/role could. See
docs/models/recommendation_engine_plan.md for the full design.

For exploration use notebooks/models/recommendation_engine.ipynb.

Usage:
  uv run python scripts/run_recommendations.py --school_id 301 --season 2027
  uv run python scripts/run_recommendations.py --school_id 301 --season 2027 --user_id 1001
  uv run python scripts/run_recommendations.py --school_id 301 --season 2027 --dry-run   # safe smoke test

Pass whatever MAX(player_team_fit_scores.season) currently resolves to — check
via fit_score_service.get_current_season() semantics, or just query the max.

Tables used today:
  users                   — school_id, is_active
  user_preferences        — weight_scheme, weight_gap, weight_role per user
  player_team_fit_scores  — scheme_fit, gap_match, role_fit
  team_rating_projections — delta_adj_em (same season as player_team_fit_scores.season)
  players                 — player_name, position
  transfers               — availability_status

Tables stubbed (uncomment when ready):
  predictions             — player_projection, data_confidence  (Model 5)

Program Fit (Model, Issue #20) is descoped from the active roadmap (2026-07-11)
and is not part of this engine's weight formula.
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta, timezone

import mlflow
import pandas as pd
from sqlalchemy import text

from portalpoint.modeling.db_writers import upsert_with_season_replace
from portalpoint.modeling.io import get_sync_engine
from portalpoint.modeling.mlflow_helpers import maybe_promote, setup_mlflow
from portalpoint.modeling.recommendations import (
    DEFAULT_FIT_WEIGHTS,
    TEAM_IMPACT_FIT_NEUTRAL,
    generate_top_50_candidates,
    refine_to_top_10,
    team_impact_fit,
)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

MODEL_VERSION = "rec-v1.2"
EXPIRES_DAYS = 7

# ── SQL: users + their saved weights ────────────────────────────────────────

USERS_SQL = """
SELECT
    u.id                                AS user_id,
    COALESCE(up.weight_scheme, 0.30)    AS weight_scheme,
    COALESCE(up.weight_gap,    0.35)    AS weight_gap,
    COALESCE(up.weight_role,   0.35)    AS weight_role
    -- future — uncomment when program fit is ready:
    -- , COALESCE(up.weight_program, 0.0) AS weight_program
FROM users u
LEFT JOIN user_preferences up ON up.user_id = u.id
WHERE u.school_id = :school_id
  AND u.is_active  = true
"""

# ── SQL: candidate pool ───────────────────────────────────────────────────────
# Availability filtering (is_portal_candidate) is handled here in SQL; ranking
# is Stage 1's job now (generate_top_50_candidates(), in Python — see module
# docstring).
#
# Season semantics (verified against the live DB 2026-07-11, corrects the
# original plan's assumption): player_team_fit_scores.season now carries real
# scheme_fit/gap_match/role_fit together at season=2027 (role_fit's
# sync_role_fit_scores() upserted directly into the same season Scheme
# Fit/Gap Matching already cover, it didn't create a separate "observed"
# season) — MAX(player_team_fit_scores.season) is 2027, not 2026. That's the
# same season team_rating_projections already uses. The join below matches
# season directly; a `ptf.season + 1` offset (destination_projection.py's
# convention, for a *different* pair of tables) returns zero rows here.

CANDIDATE_SQL = """
SELECT
    ptf.player_id,
    ptf.school_id,
    p.full_name     AS player_name,
    p.position,
    ptf.scheme_fit,
    ptf.gap_match,
    ptf.role_fit,
    ptf.overall_fit,
    trp.delta_adj_em
    -- future — uncomment when Model 5 (predictions) is ready:
    -- , pr.predicted_per_change  AS player_projection
    -- , pr.confidence            AS data_confidence
FROM player_team_fit_scores ptf
JOIN players p
    ON p.id = ptf.player_id
LEFT JOIN team_rating_projections trp
    ON trp.player_id  = ptf.player_id
   AND trp.school_id  = ptf.school_id
   AND trp.season     = ptf.season
   AND trp.expires_at > now()
-- future — uncomment when Model 5 ready:
-- LEFT JOIN predictions pr
--     ON pr.player_id = ptf.player_id AND pr.school_id = ptf.school_id
WHERE ptf.school_id          = :school_id
  AND ptf.season             = :season
  AND ptf.is_portal_candidate = true
"""

# ── SQL: role_fit freshness check ────────────────────────────────────────────
# role_fit sits at the 50.0 stub baseline until run_playing_time.py has synced
# real values for this season. That's a valid neutral fallback, not an error —
# but it silently makes the role signal inert, so we warn instead of
# gating (unlike destination_projection.py, which hard-gates on this table).

ROLE_FIT_FRESHNESS_SQL = """
SELECT EXISTS(
    SELECT 1 FROM playing_time_projections WHERE season = :season LIMIT 1
) AS has_role_fit_data
"""

# ── SQL: team_rating_projections freshness check ─────────────────────────────
# Same neutral-fallback reasoning as role_fit above: team_impact_fit() maps a
# missing delta_adj_em to TEAM_IMPACT_FIT_NEUTRAL (50.0), a valid degraded
# state, not an error — warn instead of hard-gating.

TEAM_RATING_FRESHNESS_SQL = """
SELECT EXISTS(
    SELECT 1 FROM team_rating_projections
    WHERE season = :season AND expires_at > now() LIMIT 1
) AS has_team_rating_data
"""

DELETE_SQL = "DELETE FROM recommendations WHERE user_id = %s"

INSERT_SQL = """
INSERT INTO recommendations
    (user_id, player_id, rank, overall_fit, reasoning,
     model_version, generated_at, expires_at)
VALUES %s
"""


def load_users(engine, school_id: int, user_id: int | None) -> pd.DataFrame:
    sql = USERS_SQL
    params: dict = {"school_id": school_id}
    if user_id is not None:
        sql += " AND u.id = :user_id"
        params["user_id"] = user_id
    with engine.connect() as conn:
        return pd.read_sql(text(sql), conn, params=params)


def build_candidate_pool(engine, school_id: int, season: int) -> pd.DataFrame:
    with engine.connect() as conn:
        df = pd.read_sql(
            text(CANDIDATE_SQL),
            conn,
            params={"school_id": school_id, "season": season},
        )
    log.info("Candidate pool: %d rows for school_id=%d season=%d", len(df), school_id, season)
    return df


def check_role_fit_freshness(engine, season: int) -> bool:
    with engine.connect() as conn:
        row = conn.execute(text(ROLE_FIT_FRESHNESS_SQL), {"season": season}).fetchone()
    return bool(row[0])


def check_team_rating_freshness(engine, season: int) -> bool:
    with engine.connect() as conn:
        row = conn.execute(text(TEAM_RATING_FRESHNESS_SQL), {"season": season}).fetchone()
    return bool(row[0])


def upsert_recommendations(engine, top10: pd.DataFrame, user_id: int) -> int:
    now = datetime.now(timezone.utc).isoformat()
    expires = (datetime.now(timezone.utc) + timedelta(days=EXPIRES_DAYS)).isoformat()
    records = [
        (
            user_id,
            int(row["player_id"]),
            int(row["final_rank"]),
            float(row["personalized_fit"]),  # [0,100] — matches RecommendationItem.overall_fit
            None,
            MODEL_VERSION,
            now,
            expires,
        )
        for _, row in top10.iterrows()
    ]
    upsert_with_season_replace(
        engine,
        INSERT_SQL,
        records,
        delete_sql=DELETE_SQL,
        delete_params=(user_id,),
    )
    return len(records)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run 2-stage recommendation engine")
    parser.add_argument("--school_id", type=int, required=True)
    parser.add_argument("--season",    type=int, required=True)
    parser.add_argument("--user_id",   type=int, default=None,
                        help="Run for a single user; omit to run for all active users of the school")
    parser.add_argument("--dry-run", action="store_true",
                        help="Read + score only — skip DB write. Safe to run against shared DB.")
    args = parser.parse_args()

    engine = get_sync_engine()

    # ── Load users and their saved weights ───────────────────────────────────
    users_df = load_users(engine, args.school_id, args.user_id)
    if users_df.empty:
        log.warning("No active users found for school_id=%d", args.school_id)
        return
    log.info("Found %d user(s) to process", len(users_df))

    # ── Build candidate pool for this school ─────────────────────────────────
    # SQL handles availability filter (is_portal_candidate) + the season-offset
    # team_rating_projections join only; ranking happens in Python below.
    pool = build_candidate_pool(engine, args.school_id, args.season)
    if pool.empty:
        log.warning("No candidates found — check that school_id=%d season=%d has fit scores",
                    args.school_id, args.season)
        return

    if not check_role_fit_freshness(engine, args.season):
        log.warning(
            "No playing_time_projections found for season=%d — role_fit will be "
            "the 50.0 stub for all candidates; role signal is inert this run.",
            args.season,
        )

    # team_rating_projections uses the same season as player_team_fit_scores
    # (both 2027 on the live DB, verified 2026-07-11) — no +1 offset here,
    # unlike destination_projection.py's dest_season = t.season + 1 (a
    # different pair of tables with a genuine observed-vs-target split).
    if not check_team_rating_freshness(engine, args.season):
        log.warning(
            "No team_rating_projections found for season=%d — "
            "team_impact_fit will be the %.1f neutral stub for all candidates; "
            "team-rating signal is inert this run.",
            args.season, TEAM_IMPACT_FIT_NEUTRAL,
        )

    # delta_adj_em is NaN wherever the LEFT JOIN in CANDIDATE_SQL found no
    # matching team_rating_projections row — must be filled to a raw AdjEM
    # delta of 0 (neutral) before normalizing, not left NaN, since a per-row
    # NaN would poison that row's weighted sum in calculate_overall_fit.
    pool["team_impact_fit"] = team_impact_fit(pool["delta_adj_em"].fillna(0.0))

    # ── Stage 1: vectorized rank score → Top-50 ──────────────────────────────
    top50 = generate_top_50_candidates(pool, weights=DEFAULT_FIT_WEIGHTS)

    # ── Stage 2: per-user re-rank using saved preferences → Top-10 ──────────
    all_mean_scores: list[float] = []

    for _, user in users_df.iterrows():
        uid = int(user["user_id"])
        user_preferences = {
            "scheme_fit_weight":      float(user["weight_scheme"]),
            "gap_match_weight":       float(user["weight_gap"]),
            "role_fit_weight":        float(user["weight_role"]),
            "team_impact_fit_weight": DEFAULT_FIT_WEIGHTS["team_impact_fit"],
            # team_impact_fit_weight is a fixed macro-signal weight, not a
            # per-user preference yet — user_preferences has no
            # weight_team_rating column (see recommendation_engine_plan.md §5).
            # future — uncomment when program fit is ready:
            # "program_fit_weight": float(user["weight_program"]),
        }

        try:
            top10 = refine_to_top_10(top50, user_preferences=user_preferences, risk_tolerance="medium")
        except ValueError as e:
            log.error("user_id=%d — skipping, invalid preference weights: %s", uid, e)
            continue

        mean_score = float(top10["personalized_fit"].mean())
        all_mean_scores.append(mean_score)

        if args.dry_run:
            log.info("[DRY RUN] user_id=%d — would write %d rows (mean_fit=%.1f)",
                     uid, len(top10), mean_score)
            print(top10[["final_rank", "player_id", "player_name", "position",
                          "scheme_fit", "gap_match", "role_fit", "team_impact_fit",
                          "stage1_rank_score", "personalized_fit"]].to_string(index=False))
        else:
            n = upsert_recommendations(engine, top10, uid)
            log.info("user_id=%d → %d recommendations written (mean_fit=%.1f)", uid, n, mean_score)

    if args.dry_run:
        log.info("[DRY RUN] complete — no rows written to recommendations table.")
        return

    # ── MLflow tracking ──────────────────────────────────────────────────────
    import mlflow.pyfunc

    class RecEnginePyfunc(mlflow.pyfunc.PythonModel):
        """Marker artifact — the recommendation engine is a weighted-sum
        formula over other models' outputs, not a serializable sklearn
        model; register_model needs *some* logged model artifact to attach
        a version to, same pattern as destination_projection.py's
        DestProjectionPyfunc."""
        def predict(self, context, model_input):
            return model_input

    client = setup_mlflow("recommendation-engine")
    overall_mean = sum(all_mean_scores) / len(all_mean_scores) if all_mean_scores else 0.0

    with mlflow.start_run(run_name=f"rec-school{args.school_id}-s{args.season}") as run:
        mlflow.log_params({
            "school_id":      args.school_id,
            "season":         args.season,
            "model_version":  MODEL_VERSION,
            "n_users":        len(users_df),
            "stage1_weights": str(DEFAULT_FIT_WEIGHTS),
        })
        mlflow.log_metrics({
            "pool_size":        float(len(pool)),
            "top50_size":       float(len(top50)),
            "mean_overall_fit": overall_mean,
        })
        mlflow.pyfunc.log_model(artifact_path="rec_engine_model", python_model=RecEnginePyfunc())
        run_id = run.info.run_id

    result = maybe_promote(
        client, "recommendation-engine", run_id, "rec_engine_model",
        metric_name="mean_overall_fit",
        new_value=overall_mean,
        higher_is_better=True,
    )
    log.info("MLflow run %s — %s", run_id, result)


if __name__ == "__main__":
    main()
