"""
scripts/run_recommendations.py

Non-interactive run of the 2-stage recommendation engine.

For each active user of a school:
  1. Reads their saved fit weights from user_preferences table (Option B).
  2. Assembles a Top-50 candidate pool via SQL using the rec-v1.1 fit weights.
  3. Runs Stage 2 (re-rank with user weights → Top-10) per user.
  4. Upserts results to the recommendations table.

Stage 1 as a Python step is not needed today — SQL handles filtering and ranking
with the same scheme/gap/role weights as DEFAULT_FIT_WEIGHTS.
It will be reintroduced when predictions + team_rating_projections tables are ready
and the rank formula becomes: adjusted_projection + team_rating_delta + overall_fit/100.

For exploration use notebooks/models/recommendation_engine.ipynb.

Usage:
  uv run python scripts/run_recommendations.py --school_id 301 --season 2025
  uv run python scripts/run_recommendations.py --school_id 301 --season 2025 --user_id 1001
  uv run python scripts/run_recommendations.py --school_id 301 --season 2025 --dry-run   # safe smoke test

Tables used today:
  users                   — school_id, is_active
  user_preferences        — weight_scheme, weight_gap, weight_role per user
  player_team_fit_scores  — scheme_fit, gap_match, role_fit
  players                 — player_name, position
  transfers               — availability_status

Tables stubbed (uncomment when ready):
  predictions             — player_projection, data_confidence  (Model 5)
  team_rating_projections — team_rating_delta                   (Model 6)
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
    refine_to_top_10,
    # future — uncomment when predictions + team_rating_projections tables ready:
    # generate_top_50_candidates,
)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

MODEL_VERSION = "rec-v1.1"
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

# ── SQL: Top-50 candidate pool ───────────────────────────────────────────────
# Availability filter and Top-50 ranking are handled here in SQL today, using
# the current DEFAULT_FIT_WEIGHTS so the pre-filter matches Stage 2 defaults.
# When Models 5–6 land, remove ORDER BY / LIMIT, uncomment the extra joins,
# and let generate_top_50_candidates() compute the rank formula instead.

_STAGE1_SCHEME_WEIGHT = DEFAULT_FIT_WEIGHTS["scheme_fit"]
_STAGE1_GAP_WEIGHT = DEFAULT_FIT_WEIGHTS["gap_match"]
_STAGE1_ROLE_WEIGHT = DEFAULT_FIT_WEIGHTS["role_fit"]

CANDIDATE_SQL = f"""
SELECT
    ptf.player_id,
    ptf.school_id,
    p.full_name     AS player_name,
    p.position,
    ptf.scheme_fit,
    ptf.gap_match,
    ptf.role_fit,
    (
        {_STAGE1_SCHEME_WEIGHT} * ptf.scheme_fit
        + {_STAGE1_GAP_WEIGHT} * ptf.gap_match
        + {_STAGE1_ROLE_WEIGHT} * ptf.role_fit
    ) AS rec_stage1_fit,
    ptf.overall_fit
    -- future — uncomment when Model 5 (predictions) is ready:
    -- , pr.predicted_per_change  AS player_projection
    -- , pr.confidence            AS data_confidence
    -- future — uncomment when Model 6 (team_rating_projections) is ready:
    -- , trp.delta_adj_em         AS team_rating_delta
FROM player_team_fit_scores ptf
JOIN players p
    ON p.id = ptf.player_id
-- future — uncomment when Model 5 ready:
-- LEFT JOIN predictions pr
--     ON pr.player_id = ptf.player_id AND pr.school_id = ptf.school_id
-- future — uncomment when Model 6 ready:
-- LEFT JOIN team_rating_projections trp
--     ON trp.player_id = ptf.player_id AND trp.school_id = ptf.school_id
WHERE ptf.school_id          = :school_id
  AND ptf.season             = :season
  AND ptf.is_portal_candidate = true
ORDER BY rec_stage1_fit DESC
LIMIT 50
"""

# ── SQL: role_fit freshness check ────────────────────────────────────────────
# role_fit sits at the 50.0 stub baseline until run_playing_time.py has synced
# real values for this season. That's a valid neutral fallback, not an error —
# but it silently makes rec-v1.1's role signal inert, so we warn instead of
# gating (unlike destination_projection.py, which hard-gates on this table).

ROLE_FIT_FRESHNESS_SQL = """
SELECT EXISTS(
    SELECT 1 FROM playing_time_projections WHERE season = :season LIMIT 1
) AS has_role_fit_data
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

    # ── Build shared Top-50 candidate pool for this school ───────────────────
    # Today: SQL handles availability filter + ORDER BY overall_fit LIMIT 50.
    # future — when Models 5–6 land, replace with generate_top_50_candidates():
    # pool = build_candidate_pool(engine, args.school_id, args.season)  # no LIMIT in SQL
    # top50 = generate_top_50_candidates(pool, weights=DEFAULT_FIT_WEIGHTS, filter_available=True)
    top50 = build_candidate_pool(engine, args.school_id, args.season)
    if top50.empty:
        log.warning("No candidates found — check that school_id=%d season=%d has fit scores",
                    args.school_id, args.season)
        return

    if not check_role_fit_freshness(engine, args.season):
        log.warning(
            "No playing_time_projections found for season=%d — role_fit will be "
            "the 50.0 stub for all candidates; rec-v1.1 role signal is inert this run.",
            args.season,
        )

    # ── Stage 2: per-user re-rank using saved preferences → Top-10 ──────────
    all_mean_scores: list[float] = []

    for _, user in users_df.iterrows():
        uid = int(user["user_id"])
        user_preferences = {
            "scheme_fit_weight": float(user["weight_scheme"]),
            "gap_match_weight":  float(user["weight_gap"]),
            "role_fit_weight":   float(user["weight_role"]),
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
                          "scheme_fit", "gap_match", "role_fit", "rec_stage1_fit",
                          "personalized_fit"]].to_string(index=False))
        else:
            n = upsert_recommendations(engine, top10, uid)
            log.info("user_id=%d → %d recommendations written (mean_fit=%.1f)", uid, n, mean_score)

    if args.dry_run:
        log.info("[DRY RUN] complete — no rows written to recommendations table.")
        return

    # ── MLflow tracking ──────────────────────────────────────────────────────
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
            "pool_size":        float(len(top50)),
            "mean_overall_fit": overall_mean,
        })
        run_id = run.info.run_id

    result = maybe_promote(
        client, "recommendation-engine", run_id, "",
        metric_name="mean_overall_fit",
        new_value=overall_mean,
        higher_is_better=True,
    )
    log.info("MLflow run %s — %s", run_id, result)


if __name__ == "__main__":
    main()
