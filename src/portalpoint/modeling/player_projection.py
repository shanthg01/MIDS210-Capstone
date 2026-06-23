"""Player Projection — Phase 0 (neutral talent projection).

Pure fit/score/write functions for the Phase 0 stage of the player
projection plan (docs/models/player_projection_state_space_plan.md §15).
No state-space/Kalman machinery yet — that's Phase 1/2, gated on the
2020-2025 hoopR game-log backfill (only 2026 has game-level logs today).

Phase 0 design:
  1. Per-skill empirical-Bayes shrinkage of season-grain rate stats toward a
     position x season prior, weighted by a games-played/minutes-share
     sample-size proxy (§5/§7 of the plan doc).
  2. A regularized (Ridge) value-translation model trained against Hoop
     Explorer adjusted RAPM labels (off_adj_rapm/def_adj_rapm — see plan
     doc §5 for why those are the only real RAPM columns, not the
     `_prod`/`_pred`-split fields the doc originally assumed).
  3. The fitted value model is applied to every player with season stats,
     not just the Hoop-Explorer-matched subset — that's what makes this a
     projection rather than a label lookup.

foul_discipline is intentionally absent from the skill vector: no foul-rate
column exists at season grain (only hoopr_player_game_logs has it, and only
for 2026).

Position grouping uses Hoop Explorer's `pos_class`, not `players.position`.
Discovered while building this: every row in `players.position` is the
single literal value 'G' (13,303/13,303) — the column has zero variation
despite the model docstring claiming PG/SG/SF/PF/C. `hoop_explorer_player_stats.pos_class`
(WG/CG/WF/s-PG/C/S-PF/PF/C/PG) is real and should be the position-grouping
input everywhere in this module until `players.position` is actually fixed
upstream in the ingest pipeline.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge
from sqlalchemy import Engine

from portalpoint.modeling.db_writers import upsert_with_season_replace

MODEL_VERSION = "player-projection-shrinkage-v1"
EXPIRES_DAYS = 30
MIN_GAMES = 5
SHRINKAGE_K = 8.0  # "effective games" of prior strength blended in per skill
RIDGE_ALPHA = 5.0
CI_Z = 1.2816  # ~80% interval

POSITIONS = ["PG", "s-PG", "CG", "WG", "WF", "S-PF", "PF/C", "C"]  # hoop_explorer_player_stats.pos_class values

# Observable skill -> player_season_stats column.
SKILL_COLUMNS = {
    "shooting_3p": "fg3_pct",
    "shooting_2p_finishing": "rim_pct",
    "free_throw_touch": "ft_pct",
    "shot_creation_usage": "usage_rate",
    "passing_creation": "assist_rate",
    "turnover_avoidance": "tov_pct",  # raw column; higher tov_pct is worse, see INVERTED_SKILLS
    "offensive_rebounding": "off_reb_pct",
    "defensive_rebounding": "def_reb_pct",
    "steal_disruption": "steal_pct",
    "block_rim_protection": "block_pct",
}
INVERTED_SKILLS = {"turnover_avoidance"}
SKILLS = list(SKILL_COLUMNS)
VALUE_TARGETS = ("off_adj_rapm", "def_adj_rapm")

NEUTRAL_UPSERT_SQL = """
INSERT INTO player_projections
    (player_id, school_id, season, projection_mode,
     value_per_100, value_ci_lower, value_ci_upper,
     projected_minutes, projected_usage,
     projected_box_score, projected_rates, skill_states, skill_percentiles,
     uncertainty, explanation, model_version, computed_at, expires_at)
VALUES %s
ON CONFLICT (player_id, season, model_version) WHERE school_id IS NULL
DO UPDATE SET
    value_per_100     = EXCLUDED.value_per_100,
    value_ci_lower    = EXCLUDED.value_ci_lower,
    value_ci_upper    = EXCLUDED.value_ci_upper,
    skill_states      = EXCLUDED.skill_states,
    skill_percentiles = EXCLUDED.skill_percentiles,
    uncertainty       = EXCLUDED.uncertainty,
    explanation       = EXCLUDED.explanation,
    computed_at       = EXCLUDED.computed_at,
    expires_at        = EXCLUDED.expires_at
"""


def sample_weight(games_played: pd.Series, min_pct: pd.Series) -> pd.Series:
    """Sample-size proxy used to set shrinkage strength. games_played is the
    season game count; min_pct is barttorvik's 0-100 share of team minutes —
    together they approximate how much real signal a player's season holds."""
    return games_played.clip(lower=0) * (min_pct.fillna(0).clip(0, 100) / 100.0)


def _skill_prior(df: pd.DataFrame, col: str, season_col: str, position_col: str) -> pd.Series:
    """Position x season mean, falling back to season mean, then global mean,
    for skill/position/season combinations too sparse to have their own prior."""
    season_pos_mean = df.groupby([season_col, position_col])[col].transform("mean")
    season_mean = df.groupby(season_col)[col].transform("mean")
    global_mean = df[col].mean()
    return season_pos_mean.fillna(season_mean).fillna(global_mean)


def shrink_skills(
    df: pd.DataFrame,
    season_col: str = "season",
    position_col: str = "position",
    k: float = SHRINKAGE_K,
) -> pd.DataFrame:
    """Empirical-Bayes shrinkage of each raw skill rate toward its
    position x season prior, weighted by sample size. Adds skill_*,
    prior_*, and _weight columns; does not mutate the input frame."""
    out = df.copy()
    out["_weight"] = sample_weight(df["games_played"], df["min_pct"])
    for skill, col in SKILL_COLUMNS.items():
        prior = _skill_prior(df, col, season_col, position_col)
        raw = df[col].fillna(prior)
        shrunk = prior + (out["_weight"] / (out["_weight"] + k)) * (raw - prior)
        out[f"prior_{skill}"] = prior
        out[f"raw_{skill}"] = raw
        out[f"skill_{skill}"] = shrunk
    return out


def skill_percentiles(df: pd.DataFrame, season_col: str = "season") -> pd.DataFrame:
    """Within-season percentile rank (0-100) per shrunk skill. Percentile
    direction is flipped for turnover_avoidance so 100 always means "better"."""
    out = df.copy()
    for skill in SKILLS:
        pct = df.groupby(season_col)[f"skill_{skill}"].rank(pct=True) * 100
        if skill in INVERTED_SKILLS:
            pct = 100 - pct
        out[f"pctile_{skill}"] = pct.round(1)
    return out


def build_design_matrix(df: pd.DataFrame) -> pd.DataFrame:
    skill_cols = [f"skill_{s}" for s in SKILLS]
    pos_dummies = pd.get_dummies(df["position"], prefix="pos")
    X = pd.concat([df[skill_cols].fillna(0.0), pos_dummies], axis=1)
    return X.reindex(columns=skill_cols + [f"pos_{p}" for p in POSITIONS], fill_value=0.0)


def fit_value_model(df: pd.DataFrame, target: str, alpha: float = RIDGE_ALPHA) -> tuple[Ridge, float]:
    """Ridge-regress a Hoop Explorer adjusted-RAPM label on shrunk skill
    rates + position dummies, using only rows with a non-null label. Returns
    (fitted model, residual std) — residual std is the Phase 0 uncertainty
    proxy; real cross-validated intervals are Phase 1+ work."""
    train = df.dropna(subset=[target])
    if len(train) < 30:
        raise ValueError(f"Too few labeled rows ({len(train)}) to fit a value model for {target}")
    X = build_design_matrix(train)
    y = train[target].to_numpy()
    model = Ridge(alpha=alpha)
    model.fit(X, y)
    resid_std = float(np.std(y - model.predict(X)))
    return model, resid_std


def project_value(
    df: pd.DataFrame,
    off_model: Ridge,
    def_model: Ridge,
    off_resid_std: float,
    def_resid_std: float,
) -> pd.DataFrame:
    """Apply fitted off/def value models to every player with season stats —
    including players with no Hoop Explorer match, which is the point of
    fitting a regression instead of just copying the label through."""
    X = build_design_matrix(df)
    out = df.copy()
    out["off_value_per_100"] = off_model.predict(X)
    out["def_value_per_100"] = def_model.predict(X)
    out["value_per_100"] = out["off_value_per_100"] + out["def_value_per_100"]
    total_resid_std = float(np.sqrt(off_resid_std**2 + def_resid_std**2))
    out["value_ci_lower"] = out["value_per_100"] - CI_Z * total_resid_std
    out["value_ci_upper"] = out["value_per_100"] + CI_Z * total_resid_std
    out["_resid_std"] = total_resid_std
    return out


def build_neutral_records(df: pd.DataFrame, model_version: str = MODEL_VERSION) -> list[tuple]:
    """One row per player-season, projection_mode='neutral', school_id=None."""
    computed_at = datetime.now(timezone.utc)
    expires_at = computed_at + timedelta(days=EXPIRES_DAYS)
    records: list[tuple] = []
    for _, r in df.iterrows():
        skill_states = {s: round(float(r[f"skill_{s}"]), 4) for s in SKILLS}
        skill_pcts = {s: float(r[f"pctile_{s}"]) for s in SKILLS}
        explanation = {
            "prior_skill_estimate": {s: round(float(r[f"prior_{s}"]), 4) for s in SKILLS},
            "observed_performance_signal": {s: round(float(r[f"raw_{s}"]), 4) for s in SKILLS},
            "sample_size_weight": round(float(r["_weight"]), 2),
        }
        records.append((
            int(r["player_id"]), None, int(r["season"]), "neutral",
            round(float(r["value_per_100"]), 3),
            round(float(r["value_ci_lower"]), 3),
            round(float(r["value_ci_upper"]), 3),
            None, None,
            json.dumps({}), json.dumps({}),
            json.dumps(skill_states), json.dumps(skill_pcts),
            json.dumps({"residual_std": round(float(r["_resid_std"]), 3)}),
            json.dumps(explanation),
            model_version, computed_at, expires_at,
        ))
    return records


def upsert_neutral_projections(engine: Engine, records: list[tuple]) -> int:
    """No delete step — ON CONFLICT ... WHERE school_id IS NULL DO UPDATE
    handles reruns in place via the partial unique index, same as Gap
    Matching's no-delete upsert."""
    _, upserted = upsert_with_season_replace(engine, NEUTRAL_UPSERT_SQL, records, page_size=2000)
    return upserted
