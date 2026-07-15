"""Player Projection (Model #8) — neutral talent projection.

One model, built in iterations rather than as separate models — see
docs/models/player_projection_state_space_plan.md for the full history.
Sections below, in build order:

  1. Shared constants & configuration — single canonical skill taxonomy,
     position list, value-target identity. Everything downstream reads
     from here so the skill lists can't silently drift out of sync across
     stages again (real bug, 2026-06-24: this file's own `SKILLS` and the
     Kalman layers' `SKILLS` disagreed by one skill — `foul_discipline` —
     for most of a session before being unified here).
  2. Shrinkage Baseline & Value Translation — per-skill empirical-Bayes
     shrinkage of season-grain rate stats toward a position x season prior,
     feeding a regularized (Ridge) value-translation model trained against
     Hoop Explorer adjusted RAPM labels. The simplest baseline: one
     season's box score in, one value estimate out. Also owns the shared
     DB-write layer (`player_projections` upsert) every later stage reuses.
  3. Intra-Season Kalman Smoothing — validates per-skill Kalman
     filtering/smoothing on individual game logs, one season at a time,
     before attempting the full cross-season model below. Single-season
     local-level state-space per skill; produces one end-of-season smoothed
     estimate per player per skill, in the same shape the baseline expects.
  4. Cross-Season State-Space Model — the target model. A second,
     season-grain Kalman layer on top of the intra-season smoothed
     estimates: cross-season persistence (`rho`), development-curve/
     transfer/level-change drift, block-correlated shared priors,
     attempt-rate decomposition, and one-step-ahead next-season
     forecasting. This is what actually ships projections forward in time
     rather than just summarizing a season that already happened.
  5. Evaluation & Calibration — rolling-origin cross-validation, held-out
     regression metrics, calibration, baseline comparisons, archetype-
     metadata joins. Used to validate both the baseline and the full model
     against real held-out seasons.

Position grouping uses Hoop Explorer's `pos_class`, not `players.position`.
Discovered while building this: every row in `players.position` was the
single literal value 'G' (13,303/13,303) — the column had zero variation
despite the model docstring claiming PG/SG/SF/PF/C (since fixed upstream,
but `hoop_explorer_player_stats.pos_class` remains the position-grouping
input used everywhere in this module).
"""
from __future__ import annotations

import concurrent.futures
import json
import logging
import pickle
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy.optimize import minimize, minimize_scalar
from scipy.stats import spearmanr
from sklearn.linear_model import LinearRegression, Ridge
from sklearn.metrics import r2_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sqlalchemy import Engine, text

from portalpoint.modeling.db_writers import upsert_with_season_replace
from portalpoint.modeling.io import find_repo_root

log = logging.getLogger(__name__)

# =============================================================================
# Shared constants & configuration
# =============================================================================
# Single canonical skill taxonomy for the whole model. `SKILLS` (11) is the
# master list — every skill the Kalman layers can track from game logs,
# including `foul_discipline`. `RAW_RATE_SKILLS` (10, no `foul_discipline`)
# is the strict subset the Shrinkage Baseline can compute directly from
# `player_season_stats` — there is no season-grain foul-rate column there,
# only the game-grain `hoopr_player_game_logs.fouls` the Kalman layers use.
# These were two independently-hardcoded `SKILLS` constants (10 vs 11) until
# this merge — that mismatch caused at least 3 real bugs in one session.

POSITIONS = ["PG", "s-PG", "CG", "WG", "WF", "S-PF", "PF/C", "C"]  # hoop_explorer_player_stats.pos_class values

# Observable skill -> player_season_stats column (Shrinkage Baseline only).
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
INVERTED_SKILLS = {"turnover_avoidance", "foul_discipline"}
RAW_RATE_SKILLS = list(SKILL_COLUMNS)  # the Shrinkage Baseline's 10 box-score-computable skills

# Game-level skill definitions (Intra-Season Kalman Smoothing): (numerator
# expr, denominator/weight expr) for rate skills, (made_col, attempted_col)
# for shooting skills. Together these define the master 11-skill list.
RATE_PER_40_SKILLS = {
    "shot_creation_usage": lambda d: d["field_goals_attempted"] + 0.44 * d["free_throws_attempted"],
    "passing_creation": lambda d: d["assists"],
    "turnover_avoidance": lambda d: d["turnovers"],  # inverted at use time, see INVERTED_SKILLS
    "offensive_rebounding": lambda d: d["offensive_rebounds"],
    "defensive_rebounding": lambda d: d["defensive_rebounds"],
    "steal_disruption": lambda d: d["steals"],
    "block_rim_protection": lambda d: d["blocks"],
    # foul_discipline (2026-06-24): hoopr_player_game_logs.fouls exists at
    # game grain -- inverted at use time, same as turnover_avoidance (fewer
    # fouls is better). The Shrinkage Baseline (season-grain) has no
    # equivalent column in player_season_stats, so this skill only exists
    # from the Kalman layers onward -- see INVERTED_SKILLS/RAW_RATE_SKILLS.
    "foul_discipline": lambda d: d["fouls"],
}
SHOOTING_SKILLS = {
    "shooting_3p": ("three_point_field_goals_made", "three_point_field_goals_attempted"),
    "shooting_2p_finishing": ("_fg2_made", "_fg2_attempted"),
    "free_throw_touch": ("free_throws_made", "free_throws_attempted"),
}
SKILLS = list(SHOOTING_SKILLS) + list(RATE_PER_40_SKILLS)  # master 11-skill list (includes foul_discipline)

# Offense/defense feature-set split for the value-translation model
# (2026-06-24, user-initiated). off_adj_rapm is regressed on OFFENSE_SKILLS
# only, def_adj_rapm on DEFENSE_SKILLS only -- position dummies are the only
# "shared" feature (informs both offensive and defensive role expectations),
# already handled separately from skill_cols in build_design_matrix.
# turnover_avoidance is classified Offense (a turnover is an offensive-
# possession event by definition, not a defensive one). foul_discipline is
# Kalman-layer-only (see INVERTED_SKILLS above) -- not present in the
# Shrinkage Baseline's RAW_RATE_SKILLS at all, so it can never appear in a
# Shrinkage Baseline design matrix regardless of this classification; the
# classification only matters once a Cross-Season state frame (which does
# have skill_foul_discipline) is fit/projected.
OFFENSE_SKILLS = [
    "shooting_3p", "shooting_2p_finishing", "free_throw_touch", "shot_creation_usage",
    "passing_creation", "turnover_avoidance", "offensive_rebounding",
]
DEFENSE_SKILLS = ["defensive_rebounding", "steal_disruption", "block_rim_protection", "foul_discipline"]
VALUE_TARGETS = ("off_adj_rapm", "def_adj_rapm")
DEF_VALUE_TARGET_DIRECTION = "raw_hoop_explorer_lower_is_better"
TOTAL_VALUE_FORMULA = "off_value_per_100 - def_value_per_100"


# =============================================================================
# Shrinkage Baseline & Value Translation
# =============================================================================
# Per-skill empirical-Bayes shrinkage of season-grain rate stats toward a
# position x season prior, weighted by a games-played/minutes-share
# sample-size proxy, feeding a regularized (Ridge) value-translation model
# trained against Hoop Explorer adjusted RAPM labels (off_adj_rapm/
# def_adj_rapm -- the only real RAPM columns, not the `_prod`/`_pred`-split
# fields an earlier version of the plan doc assumed). Hoop Explorer's
# defensive adjusted RAPM is lower-is-better; total value follows
# adj_rapm_margin = off_adj_rapm - def_adj_rapm.
#
# The fitted value model is applied to every player with season stats, not
# just the Hoop-Explorer-matched subset -- that's what makes this a
# projection rather than a label lookup. Also owns the shared
# `player_projections` DB-write layer every later section reuses.

MODEL_VERSION = "player-projection-shrinkage-v2"
EXPIRES_DAYS = 30
MIN_GAMES = 5
SHRINKAGE_K = 8.0  # "effective games" of prior strength blended in per skill
RIDGE_ALPHA = 5.0
CI_Z = 1.2816  # ~80% interval

PLAYER_SEASON_SQL = """
SELECT
    pss.player_id,
    pss.season,
    he.pos_class AS position,
    pss.games_played,
    pss.min_pct,
    pss.fg3_pct,
    pss.rim_pct,
    pss.ft_pct,
    pss.usage_rate,
    pss.assist_rate,
    pss.tov_pct,
    pss.off_reb_pct,
    pss.def_reb_pct,
    pss.steal_pct,
    pss.block_pct,
    he.off_adj_rapm,
    he.def_adj_rapm,
    he.off_adj_rapm_prod,
    he.adj_rapm_prod_margin
FROM player_season_stats pss
LEFT JOIN hoop_explorer_player_stats he
    ON he.player_id = pss.player_id AND he.season = pss.season
WHERE pss.games_played >= :min_games
"""


def load_player_season_frame(engine: Engine, min_games: int = MIN_GAMES) -> pd.DataFrame:
    """Loads the Shrinkage Baseline player-season frame (all seasons at
    once -- `shrink_skills` groups by season internally, so there's no need
    to query per-season). Shared by `run_player_projection.py`, the
    notebook, and the Cross-Season State-Space Model's prior-sourcing —
    single source of truth instead of multiple copies of the same SQL."""
    with engine.connect() as conn:
        df = pd.read_sql(text(PLAYER_SEASON_SQL), conn, params={"min_games": min_games})
    # he LEFT JOIN can in principle match more than one HE row per
    # (player_id, season) if a future data issue duplicates he_player_code
    # mappings — guard against silently duplicating pss rows.
    return df.drop_duplicates(subset=["player_id", "season"], keep="first").reset_index(drop=True)

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
    projected_box_score = EXCLUDED.projected_box_score,
    projected_rates   = EXCLUDED.projected_rates,
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


def skill_percentiles(df: pd.DataFrame, season_col: str = "season", skills: list[str] = RAW_RATE_SKILLS) -> pd.DataFrame:
    """Within-season percentile rank (0-100) per shrunk skill. Percentile
    direction is flipped for turnover_avoidance (and any other
    `INVERTED_SKILLS` member) so 100 always means "better".

    `skills` defaults to the Shrinkage Baseline's `RAW_RATE_SKILLS` (10) for
    backward compatibility, but the Cross-Season state frame has 11 (master
    `SKILLS` includes `foul_discipline`, which the Shrinkage Baseline
    structurally lacks) — callers building percentiles for a Cross-Season
    frame must pass `SKILLS` explicitly (real bug found 2026-06-24: this
    function silently used the hardcoded 10-skill module constant regardless
    of what the input frame actually had, so a Cross-Season frame's
    `skill_foul_discipline` column was silently never percentiled —
    `build_cross_season_records` then KeyError'd looking for
    `pctile_foul_discipline`)."""
    out = df.copy()
    for skill in skills:
        pct = df.groupby(season_col)[f"skill_{skill}"].rank(pct=True) * 100
        if skill in INVERTED_SKILLS:
            pct = 100 - pct
        out[f"pctile_{skill}"] = pct.round(1)
    return out


def build_design_matrix(
    df: pd.DataFrame,
    skills: list[str] = RAW_RATE_SKILLS,
    extra_features: list[str] | None = None,
) -> pd.DataFrame:
    """`skills` defaults to `RAW_RATE_SKILLS` (the Shrinkage Baseline's full
    list) for backward compatibility. `fit_value_model`/`project_value` pass
    `OFFENSE_SKILLS`/`DEFENSE_SKILLS` explicitly (2026-06-24 offense/defense
    split) — any other caller (e.g. the Cross-Season model's
    `_stage_2a_design_matrix`, which has its own independent feature set) is
    unaffected.

    A requested skill whose `skill_<s>` column doesn't exist in `df` (e.g.
    `foul_discipline` — in `DEFENSE_SKILLS`, but absent from every Shrinkage
    Baseline frame, which has no season-grain fouls column at all) is
    zero-padded via the `reindex` below rather than raising — the Shrinkage
    Baseline's def-model gets an always-0 `foul_discipline` feature (a real
    but harmless dead coefficient slot, no information, fits to ~0), the
    Cross-Season model's def-model gets the real column. Found as a real bug
    2026-06-24: the original version selected `df[skill_cols]` directly,
    which `KeyError`'d on every Shrinkage Baseline call to
    `fit_value_model("def_adj_rapm")` the moment `DEFENSE_SKILLS` gained
    `foul_discipline`."""
    skill_cols = [f"skill_{s}" for s in skills]
    available_cols = [c for c in skill_cols if c in df.columns]
    pos_dummies = pd.get_dummies(df["position"], prefix="pos")
    X = pd.concat([df[available_cols].fillna(0.0), pos_dummies], axis=1)
    columns = skill_cols + [f"pos_{p}" for p in POSITIONS]
    if extra_features:
        extras = df.reindex(columns=extra_features, fill_value=0.0).fillna(0.0)
        X = pd.concat([X, extras], axis=1)
        columns += extra_features
    return X.reindex(columns=columns, fill_value=0.0)


def _skills_for_target(target: str) -> list[str]:
    """off_adj_rapm -> OFFENSE_SKILLS, def_adj_rapm -> DEFENSE_SKILLS. The
    target name already disambiguates which feature set to use at every
    real call site (Shrinkage Baseline, Gap D refit, Gap G per-fold fit, the
    MLflow pyfunc wrapper all call fit_value_model/project_value per-target)
    -- no caller-visible signature change needed for this split."""
    return OFFENSE_SKILLS if target == "off_adj_rapm" else DEFENSE_SKILLS


def combine_total_value(off_value: Any, def_value_raw: Any) -> Any:
    """Combine offensive value with raw Hoop Explorer defensive adjusted RAPM.

    Hoop Explorer's `def_adj_rapm` is lower-is-better, and the source identity
    is `adj_rapm_margin = off_adj_rapm - def_adj_rapm`. Keep this tiny helper
    as the single arithmetic contract for in-process scoring and MLflow
    pyfunc wrappers.
    """
    return off_value - def_value_raw


def fit_value_model(
    df: pd.DataFrame,
    target: str,
    alpha: float = RIDGE_ALPHA,
    extra_features: list[str] | None = None,
) -> tuple[Pipeline, float]:
    """Ridge-regress a Hoop Explorer adjusted-RAPM label on shrunk skill
    rates + position dummies, using only rows with a non-null label. Returns
    (fitted model, residual std) — residual std is the baseline uncertainty
    proxy; real cross-validated intervals are Evaluation & Calibration work.

    Offense/defense split (2026-06-24): `target` picks `OFFENSE_SKILLS` or
    `DEFENSE_SKILLS` as the feature set (see `_skills_for_target`) -- the
    off and def models are no longer trained on the same feature matrix."""
    train = df.dropna(subset=[target])
    if len(train) < 30:
        raise ValueError(f"Too few labeled rows ({len(train)}) to fit a value model for {target}")
    X = build_design_matrix(train, skills=_skills_for_target(target), extra_features=extra_features)
    y = train[target].to_numpy()
    model = Pipeline([
        ("scale", StandardScaler()),
        ("ridge", Ridge(alpha=alpha)),
    ])
    fit_kwargs: dict[str, Any] = {}
    if "_weight" in train:
        sample_weights = train["_weight"].fillna(0).clip(lower=0).to_numpy(dtype=np.float64)
        if sample_weights.sum() > 0:
            fit_kwargs["ridge__sample_weight"] = sample_weights
    model.fit(X, y, **fit_kwargs)
    resid_std = float(np.std(y - model.predict(X)))
    return model, resid_std


def _feature_prediction_variance(
    df: pd.DataFrame,
    model: Pipeline,
    skills: list[str],
    extra_features: list[str] | None = None,
) -> np.ndarray:
    """Propagate independent skill-state variances through a fitted linear
    value model. Ridge is fit inside a StandardScaler pipeline, so convert
    coefficients back to raw-feature scale before applying variance math.

    Position dummies have no model-side uncertainty here; downstream Role
    Fit/destination adapters own contextual roster uncertainty. If the frame
    has no `skill_var_*` columns, this returns zeros and preserves the
    baseline's prior constant-width behavior.
    """
    if not len(df):
        return np.array([], dtype=np.float64)

    ridge = model.named_steps["ridge"]
    scaler = model.named_steps["scale"]
    coef = np.asarray(ridge.coef_, dtype=np.float64)
    scale = np.asarray(scaler.scale_, dtype=np.float64)
    raw_coef = np.divide(coef, scale, out=np.zeros_like(coef), where=scale != 0)

    skill_cols = [f"skill_{s}" for s in skills]
    feature_cols = skill_cols + [f"pos_{p}" for p in POSITIONS] + list(extra_features or [])
    coef_by_feature = dict(zip(feature_cols, raw_coef))

    pred_var = np.zeros(len(df), dtype=np.float64)
    for skill in skills:
        var_col = f"skill_var_{skill}"
        if var_col not in df.columns:
            continue
        weight = float(coef_by_feature.get(f"skill_{skill}", 0.0))
        skill_var = df[var_col].fillna(0.0).clip(lower=0.0).to_numpy(dtype=np.float64)
        pred_var += (weight * weight) * skill_var
    for feature in extra_features or []:
        var_col = f"{feature}_var"
        if var_col not in df.columns:
            continue
        weight = float(coef_by_feature.get(feature, 0.0))
        feature_var = df[var_col].fillna(0.0).clip(lower=0.0).to_numpy(dtype=np.float64)
        pred_var += (weight * weight) * feature_var
    return pred_var


def _raw_feature_coefficients(
    model: Pipeline,
    skills: list[str],
    extra_features: list[str] | None = None,
) -> dict[str, float]:
    """Return linear-model coefficients on the original, unstandardized
    feature scale for contribution/explanation math."""
    ridge = model.named_steps["ridge"]
    scaler = model.named_steps["scale"]
    coef = np.asarray(ridge.coef_, dtype=np.float64)
    scale = np.asarray(scaler.scale_, dtype=np.float64)
    raw_coef = np.divide(coef, scale, out=np.zeros_like(coef), where=scale != 0)
    feature_cols = [f"skill_{s}" for s in skills] + [f"pos_{p}" for p in POSITIONS] + list(extra_features or [])
    return dict(zip(feature_cols, raw_coef))


def attach_value_drivers(
    df: pd.DataFrame,
    off_model: Pipeline,
    def_model: Pipeline,
    off_extra_features: list[str] | None = None,
    def_extra_features: list[str] | None = None,
    top_n: int = 5,
) -> pd.DataFrame:
    """Attach compact per-row value-driver explanations.

    Total value is offense minus raw defensive RAPM, so defensive model
    feature contributions are subtracted when explaining total value.
    """
    out = df.copy()
    X_off = build_design_matrix(out, skills=OFFENSE_SKILLS, extra_features=off_extra_features)
    X_def = build_design_matrix(out, skills=DEFENSE_SKILLS, extra_features=def_extra_features)
    off_coef = _raw_feature_coefficients(off_model, OFFENSE_SKILLS, off_extra_features)
    def_coef = _raw_feature_coefficients(def_model, DEFENSE_SKILLS, def_extra_features)

    drivers: list[dict] = []
    for i in range(len(out)):
        parts: list[dict] = []
        for feature, value in X_off.iloc[i].items():
            contribution = float(value) * float(off_coef.get(feature, 0.0))
            if contribution:
                parts.append({"feature": feature, "component": "offense", "total_value_contribution": contribution})
        for feature, value in X_def.iloc[i].items():
            raw_def_contribution = float(value) * float(def_coef.get(feature, 0.0))
            contribution = -raw_def_contribution
            if contribution:
                parts.append({"feature": feature, "component": "defense", "total_value_contribution": contribution})
        positive = sorted((p for p in parts if p["total_value_contribution"] > 0), key=lambda p: p["total_value_contribution"], reverse=True)[:top_n]
        negative = sorted((p for p in parts if p["total_value_contribution"] < 0), key=lambda p: p["total_value_contribution"])[:top_n]
        drivers.append({
            "top_positive": [
                {**p, "total_value_contribution": round(float(p["total_value_contribution"]), 3)} for p in positive
            ],
            "top_negative": [
                {**p, "total_value_contribution": round(float(p["total_value_contribution"]), 3)} for p in negative
            ],
        })
    out["_value_drivers"] = drivers
    return out


def project_value(
    df: pd.DataFrame,
    off_model: Pipeline,
    def_model: Pipeline,
    off_resid_std: float,
    def_resid_std: float,
    off_extra_features: list[str] | None = None,
    def_extra_features: list[str] | None = None,
    ci_scale: float = 1.0,
) -> pd.DataFrame:
    """Apply fitted off/def value models to every player with season stats —
    including players with no Hoop Explorer match, which is the point of
    fitting a regression instead of just copying the label through.

    Offense/defense split (2026-06-24): `off_model`/`def_model` were each
    fit on a different feature set (see `fit_value_model`) -- builds two
    design matrices, not one shared `X`.

    Defensive sign convention (2026-06-25): `def_model` predicts raw Hoop
    Explorer `def_adj_rapm`, where lower/more negative is better. Hoop
    Explorer's total margin identity is `adj_rapm_margin = off_adj_rapm -
    def_adj_rapm`, so `value_per_100` subtracts the defensive prediction."""
    X_off = build_design_matrix(df, skills=OFFENSE_SKILLS, extra_features=off_extra_features)
    X_def = build_design_matrix(df, skills=DEFENSE_SKILLS, extra_features=def_extra_features)
    out = df.copy()
    out["off_value_per_100"] = off_model.predict(X_off)
    out["def_value_per_100"] = def_model.predict(X_def)
    out["value_per_100"] = combine_total_value(out["off_value_per_100"], out["def_value_per_100"])
    residual_var = float(off_resid_std**2 + def_resid_std**2)
    off_skill_var = _feature_prediction_variance(out, off_model, OFFENSE_SKILLS, off_extra_features)
    def_skill_var = _feature_prediction_variance(out, def_model, DEFENSE_SKILLS, def_extra_features)
    skill_value_var = off_skill_var + def_skill_var
    value_std = np.sqrt(residual_var + skill_value_var) * float(ci_scale)

    out["value_ci_lower"] = out["value_per_100"] - CI_Z * value_std
    out["value_ci_upper"] = out["value_per_100"] + CI_Z * value_std
    out["_residual_std"] = float(np.sqrt(residual_var))
    out["_skill_state_value_std"] = np.sqrt(skill_value_var)
    out["_ci_scale"] = float(ci_scale)
    out["_value_std"] = value_std
    out["_resid_std"] = value_std
    return out


def build_neutral_records(df: pd.DataFrame, model_version: str = MODEL_VERSION) -> list[tuple]:
    """One row per player-season, projection_mode='neutral', school_id=None."""
    computed_at = datetime.now(timezone.utc)
    expires_at = computed_at + timedelta(days=EXPIRES_DAYS)
    records: list[tuple] = []
    for _, r in df.iterrows():
        skill_states = {
            s: round(float(-r[f"skill_{s}"] if s in INVERTED_SKILLS else r[f"skill_{s}"]), 4)
            for s in RAW_RATE_SKILLS
        }
        skill_pcts = {s: float(r[f"pctile_{s}"]) for s in RAW_RATE_SKILLS}
        explanation = {
            "prior_skill_estimate": {s: round(float(r[f"prior_{s}"]), 4) for s in RAW_RATE_SKILLS},
            "observed_performance_signal": {s: round(float(r[f"raw_{s}"]), 4) for s in RAW_RATE_SKILLS},
            "sample_size_weight": round(float(r["_weight"]), 2),
            "skill_state_direction": {
                s: "higher_is_better" if s not in INVERTED_SKILLS else "stored_as_negative_rate_so_higher_is_better"
                for s in RAW_RATE_SKILLS
            },
        }
        residual_std = float(r.get("_residual_std", r.get("_resid_std", 0.0)))
        value_std = float(r.get("_value_std", r.get("_resid_std", residual_std)))
        skill_state_value_std = float(r.get("_skill_state_value_std", 0.0))
        uncertainty = {
            "residual_std": round(residual_std, 3),
            "value_std": round(value_std, 3),
            "skill_state_value_std": round(skill_state_value_std, 3),
            "ci_scale": round(float(r.get("_ci_scale", 1.0)), 3),
        }
        records.append((
            int(r["player_id"]), None, int(r["season"]), "neutral",
            round(float(r["value_per_100"]), 3),
            round(float(r["value_ci_lower"]), 3),
            round(float(r["value_ci_upper"]), 3),
            None, None,
            json.dumps({}), json.dumps({}),
            json.dumps(skill_states), json.dumps(skill_pcts),
            json.dumps(uncertainty),
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


def save_artifacts(
    models_dir: Path,
    off_model: Pipeline,
    def_model: Pipeline,
    off_resid_std: float | None = None,
    def_resid_std: float | None = None,
) -> dict[str, Path]:
    """Pickle fitted off/def pipelines plus a replayable metadata bundle."""
    models_dir.mkdir(parents=True, exist_ok=True)
    metadata = {
        "model_version": MODEL_VERSION,
        "min_games": MIN_GAMES,
        "shrinkage_k": SHRINKAGE_K,
        "ridge_alpha": RIDGE_ALPHA,
        # Offense/defense split (2026-06-24): off_model/def_model no longer
        # share one feature matrix -- two separate column lists.
        "off_feature_columns": build_design_matrix(pd.DataFrame({
            "position": POSITIONS,
            **{f"skill_{s}": [0.0] * len(POSITIONS) for s in OFFENSE_SKILLS},
        }), skills=OFFENSE_SKILLS).columns.tolist(),
        "def_feature_columns": build_design_matrix(pd.DataFrame({
            "position": POSITIONS,
            **{f"skill_{s}": [0.0] * len(POSITIONS) for s in DEFENSE_SKILLS},
        }), skills=DEFENSE_SKILLS).columns.tolist(),
        "skill_columns": SKILL_COLUMNS,
        "inverted_skills": sorted(INVERTED_SKILLS),
        "value_targets": VALUE_TARGETS,
        "def_value_target_direction": DEF_VALUE_TARGET_DIRECTION,
        "total_value_formula": TOTAL_VALUE_FORMULA,
        "ci_z": CI_Z,
        "off_resid_std": off_resid_std,
        "def_resid_std": def_resid_std,
    }
    bundle = {
        "off_model": off_model,
        "def_model": def_model,
        "metadata": metadata,
    }
    paths = {
        "off_model": models_dir / "player_projection_off_model.pkl",
        "def_model": models_dir / "player_projection_def_model.pkl",
        "bundle": models_dir / "player_projection_model_bundle.pkl",
    }
    with open(paths["off_model"], "wb") as f:
        pickle.dump(off_model, f)
    with open(paths["def_model"], "wb") as f:
        pickle.dump(def_model, f)
    with open(paths["bundle"], "wb") as f:
        pickle.dump(bundle, f)
    return paths


# =============================================================================
# Intra-Season Kalman Smoothing
# =============================================================================
# Validates per-skill Kalman filtering/smoothing on individual game logs,
# one season at a time, before attempting the full Cross-Season State-Space
# Model below. Only season 2026 had game-level data (`hoopr_player_game_logs`)
# when this was first built; the 2020-2025 backfill has since landed, but
# this section remains a single-season local-level model per skill, not a
# cross-season persistence model — `rho` and the development curve are the
# Cross-Season section's job.
#
# Model per (player, skill) — scalar local-level state-space:
#
#     alpha_t = alpha_(t-1) + w_t,      w_t ~ N(0, Q)
#     y_t     = alpha_t + v_t,          v_t ~ N(0, R_t)
#
# `R_t` is sample-size-weighted (`R_t = numerator / weight_t`, see
# `_r_numerator`): shooting skills weight by attempts that game (Bernoulli
# variance, numerator ~ p(1-p)), rate skills (assists, rebounds, etc.,
# expressed per-40-minutes) weight by minutes played that game (Poisson-
# consistent numerator ~ mean_rate * 40 — fixed 2026-06-23; a flat numerator
# of 1.0 had been underestimating count-rate noise by orders of magnitude).
# `Q` is fit once per skill via pooled MLE across every player's game
# sequence for that skill — fitting a separate Q per player would be
# unstable on panels this short (many players have under 15 games a season).
#
# This section deliberately does not re-derive the value-translation step —
# it produces `skill_*` columns in the same shape the Shrinkage Baseline
# expects, so `fit_value_model`/`build_design_matrix`/`project_value` above
# are reused as-is.


def build_game_observations(game_logs: pd.DataFrame) -> pd.DataFrame:
    """One row per (player_id, game) with y_<skill>/weight_<skill> columns,
    sorted by game_date within player. weight=0 marks a missing observation
    for that skill/game (e.g. zero attempts), not a true zero rate."""
    df = game_logs.sort_values(["player_id", "game_date"]).reset_index(drop=True)
    df["_fg2_made"] = df["field_goals_made"] - df["three_point_field_goals_made"]
    df["_fg2_attempted"] = df["field_goals_attempted"] - df["three_point_field_goals_attempted"]

    for skill, (made_col, att_col) in SHOOTING_SKILLS.items():
        weight = df[att_col].fillna(0).clip(lower=0)
        df[f"y_{skill}"] = np.where(weight > 0, df[made_col] / weight.replace(0, np.nan), np.nan)
        df[f"weight_{skill}"] = weight

    minutes = df["minutes"].fillna(0).clip(lower=0)
    for skill, numer_fn in RATE_PER_40_SKILLS.items():
        numer = numer_fn(df).fillna(0)
        df[f"y_{skill}"] = np.where(minutes > 0, numer / minutes.replace(0, np.nan) * 40.0, np.nan)
        df[f"weight_{skill}"] = minutes

    return df


def kalman_filter_series(
    y: np.ndarray, R: np.ndarray, Q: float, mask: np.ndarray, prior_mean: float, prior_var: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Scalar local-level Kalman filter over one player's game sequence.

    Missing observations (mask=False) propagate the state with no update —
    the natural way to handle a ragged panel without imputing a fake
    game-level rate.

    Returns (filtered_mean, filtered_var, pred_mean, pred_var) — pred_* are
    the one-step-ahead prediction moments, needed for the pooled MLE
    log-likelihood in fit_q_mle.
    """
    n = len(y)
    a = np.empty(n)
    P = np.empty(n)
    pred_mean = np.empty(n)
    pred_var = np.empty(n)
    a_prev, P_prev = prior_mean, prior_var
    for t in range(n):
        a_pred = a_prev
        P_pred = P_prev + Q
        pred_mean[t] = a_pred
        pred_var[t] = P_pred + R[t]
        if mask[t]:
            k_gain = P_pred / (P_pred + R[t])
            a_t = a_pred + k_gain * (y[t] - a_pred)
            P_t = (1.0 - k_gain) * P_pred
        else:
            a_t, P_t = a_pred, P_pred
        a[t], P[t] = a_t, P_t
        a_prev, P_prev = a_t, P_t
    return a, P, pred_mean, pred_var


def kalman_smoother_series(a: np.ndarray, P: np.ndarray, Q: float) -> tuple[np.ndarray, np.ndarray]:
    """Rauch-Tung-Striebel smoother given filtered means/variances."""
    n = len(a)
    a_s, P_s = a.copy(), P.copy()
    for t in range(n - 2, -1, -1):
        p_pred = P[t] + Q
        if p_pred <= 0:
            continue
        j_gain = P[t] / p_pred
        a_s[t] = a[t] + j_gain * (a_s[t + 1] - a[t])
        P_s[t] = P[t] + j_gain * j_gain * (P_s[t + 1] - p_pred)
    return a_s, P_s


Sequence = tuple[np.ndarray, np.ndarray, np.ndarray, float, float]  # y, R, mask, prior_mean, prior_var

# Q search bounds. Widened from the original (1e-6, 2.0) — that range was
# implicitly tuned against the old, badly-undersized R_t (see _r_numerator):
# now that count-rate skills' R_t is correctly scaled to their y-units (which
# can run into the tens, e.g. assists/turnovers per 40 minutes, vs. shooting
# skills' [0, 1] rate scale), their plausible Q range is larger too. One wide
# shared bound is simpler than a per-skill-type bound and costs nothing extra
# — minimize_scalar's bounded Brent search is cheap regardless of width.
Q_BOUNDS = (1e-6, 100.0)


def _pooled_neg_log_likelihood(q_value: float, sequences: list[Sequence]) -> float:
    if q_value <= 0:
        return np.inf
    total = 0.0
    for y, R, mask, prior_mean, prior_var in sequences:
        _, _, pred_mean, pred_var = kalman_filter_series(y, R, q_value, mask, prior_mean, prior_var)
        if not mask.any():
            continue
        var = pred_var[mask]
        err = y[mask] - pred_mean[mask]
        total += 0.5 * float(np.sum(np.log(2 * np.pi * var) + err**2 / var))
    return total


def pooled_neg_log_likelihood(q_value: float, sequences: list[Sequence]) -> float:
    """Public alias of _pooled_neg_log_likelihood — exposed for notebook
    diagnostics (e.g. plotting the likelihood curve around a fitted Q)."""
    return _pooled_neg_log_likelihood(q_value, sequences)


MAX_SEQUENCES_FOR_Q_SEARCH = 1000


def fit_q_mle(
    sequences: list[Sequence], bounds: tuple[float, float] = Q_BOUNDS,
    max_sequences_for_search: int | None = MAX_SEQUENCES_FOR_Q_SEARCH, random_state: int = 0,
) -> tuple[float, float]:
    """Pooled MLE fit of one global process variance Q for a skill, across
    every player's game sequence. Returns (Q, neg_log_likelihood_at_Q).

    Performance note (2026-06-24): `Q` is a single population-level nuisance
    parameter, not a per-player estimate — it doesn't need the full
    population to converge to essentially the same value, only enough
    players for the pooled likelihood to be stable. `minimize_scalar` calls
    the pooled likelihood ~20-30 times (Brent's method), and each call was
    looping over every one of ~5,000 players in pure Python — the dominant
    cost in the real ~2h+ run. When `len(sequences)` exceeds
    `max_sequences_for_search`, the *search* runs on a deterministic random
    subsample (fixed `random_state`, so reruns are reproducible); the
    returned `Q` is then used to filter/smooth the *full* population exactly
    as before (`smooth_skill` calls this once, then loops over all players
    with the fitted `Q` — that loop is unaffected by this change). The
    returned `neg_log_likelihood_at_Q` is computed on the search subsample,
    not the full population — not meaningful to compare across calls with
    different sample sizes, but no caller currently uses this value (only
    `Q` itself is consumed downstream).
    """
    search_sequences = sequences
    if max_sequences_for_search is not None and len(sequences) > max_sequences_for_search:
        rng = np.random.default_rng(random_state)
        idx = rng.choice(len(sequences), size=max_sequences_for_search, replace=False)
        search_sequences = [sequences[i] for i in idx]
    result = minimize_scalar(_pooled_neg_log_likelihood, bounds=bounds, args=(search_sequences,), method="bounded")
    return float(result.x), float(result.fun)


def _r_numerator(skill: str, prior_mean: float) -> float:
    """Per-skill observation-noise numerator for R_t = numerator / weight_t.

    Shooting skills: y is a make/attempt Bernoulli rate, weight is attempts.
    Var(y) = p(1-p)/attempts, so numerator = p(1-p) — roughly constant
    (~0.2-0.25) across realistic shooting percentages, which is why a flat
    numerator of 1.0 happened to work tolerably for these (just a constant
    rescaling Q absorbs).

    Rate-per-40 skills: y = K/minutes*40 where K ~ Poisson(lambda*minutes)
    is the raw game count and lambda is the true per-minute rate.
    Var(K) = lambda*minutes (Poisson variance = mean), so
    Var(y) = Var(K)*(40/minutes)**2 = lambda*1600/minutes.
    lambda = E[y]/40, so Var(y) = (E[y]/40)*1600/minutes = E[y]*40/minutes
    -> numerator = E[y]*40 (approximated here by prior_mean*40).

    This was the actual bug: a flat numerator of 1.0 underestimated
    count-rate observation noise by orders of magnitude (e.g.
    turnover_avoidance's true numerator is ~prior_mean*40, often 100-500+,
    not 1) — Q was saturating at its upper bound trying to explain that
    mismatch as real game-to-game state movement instead of noise.
    """
    if skill in SHOOTING_SKILLS:
        p = min(max(prior_mean, 0.01), 0.99)
        return p * (1.0 - p)
    return max(prior_mean, 1e-3) * 40.0


PRIOR_VAR_SHRINK_K = 8.0  # matches SHRINKAGE_K above — same shrinkage
# shape (weight/(weight+k)) applied to prior *variance* here instead of mean,
# since the mean itself now comes pre-shrunk from external_priors (Gap D).


def build_player_sequences(
    obs_df: pd.DataFrame, skill: str, external_priors: pd.DataFrame | None = None,
) -> dict[int, Sequence]:
    """One Sequence per player_id for the given skill. Observation noise
    R_t = numerator / weight_t — see _r_numerator for the Bernoulli vs.
    Poisson-rate derivation. This is still an approximation (population-mean
    numerator, not per-player), but fixes the order-of-magnitude scale error
    that previously pinned count-rate skills' Q at its upper bound.

    `external_priors` (Gap D, Issue #37 reconciliation, 2026-06-23): an
    optional frame with columns `player_id`, `skill_{skill}`, `_weight` —
    the Shrinkage Baseline's `shrink_skills()` output for the same season.
    When given, each player's `prior_mean` becomes their position x season
    shrinkage estimate instead of the flat population mean, and `prior_var`
    is shrunk toward more confidence in proportion to their sample weight
    (same `weight/(weight+k)` shape the Shrinkage Baseline itself uses for
    the mean, applied here to variance). Players absent from
    `external_priors` (e.g. below the games-played floor) fall back to the
    flat population prior — this was the only behavior before Gap D, so it
    remains a real fallback, not a placeholder.
    """
    y_col, w_col = f"y_{skill}", f"weight_{skill}"
    valid_mask_all = obs_df[w_col] > 0
    population_prior_mean = float(obs_df.loc[valid_mask_all, y_col].mean())
    population_prior_var = float(obs_df.loc[valid_mask_all, y_col].var(ddof=1)) * 4.0
    if not np.isfinite(population_prior_var) or population_prior_var <= 0:
        population_prior_var = 1.0
    r_numerator = _r_numerator(skill, population_prior_mean)

    external_lookup: dict[int, tuple[float, float]] = {}
    if external_priors is not None and f"skill_{skill}" in external_priors.columns:
        # NOT itertuples()/_asdict() — pandas can't give a namedtuple field
        # literally called "_weight" (leading underscore is reserved for
        # namedtuple internals), so it silently renames it and the lookup
        # would go missing for every row. Direct column access avoids that.
        #
        # Performance fix (2026-06-24): the original version re-looked-up
        # `external_priors[f"skill_{skill}"]` (a fresh column access) on
        # *every* loop iteration via `.iloc[i]`, instead of pulling each
        # column out once as a plain numpy array first. With ~5,000 players
        # per season x 70 (season, skill) calls, that repeated per-row
        # pandas column/Series overhead was a real, avoidable cost in the
        # first real-data run of this code path — pull everything to numpy
        # once, then build the dict via zip (no pandas indexing inside the
        # loop at all).
        pid_arr = external_priors["player_id"].to_numpy()
        skill_arr = external_priors[f"skill_{skill}"].to_numpy(dtype=np.float64)
        weight_arr = (
            external_priors["_weight"].to_numpy(dtype=np.float64)
            if "_weight" in external_priors.columns
            else np.zeros(len(external_priors), dtype=np.float64)
        )
        external_lookup = {
            int(pid): (float(sk), float(w)) for pid, sk, w in zip(pid_arr, skill_arr, weight_arr)
        }

    sequences: dict[int, Sequence] = {}
    for player_id, g in obs_df.groupby("player_id"):
        pid = int(player_id)
        if pid in external_lookup:
            prior_mean, weight = external_lookup[pid]
            prior_var = population_prior_var * (PRIOR_VAR_SHRINK_K / (PRIOR_VAR_SHRINK_K + max(weight, 0.0)))
        else:
            prior_mean, prior_var = population_prior_mean, population_prior_var

        mask = (g[w_col] > 0).to_numpy()
        y = g[y_col].fillna(prior_mean).to_numpy(dtype=np.float64)
        weight_arr = g[w_col].clip(lower=1e-6).to_numpy(dtype=np.float64)
        R = r_numerator / weight_arr
        sequences[pid] = (y, R, mask, prior_mean, prior_var)
    return sequences


def smooth_skill(
    obs_df: pd.DataFrame, skill: str, external_priors: pd.DataFrame | None = None,
) -> tuple[float, pd.DataFrame]:
    """Fit Q for one skill, then filter+smooth every player's sequence.
    Returns (fitted_Q, frame with one row per player: end-of-season smoothed
    mean/var = the last smoothed state, used as the intra-season skill
    estimate). `external_priors`: see build_player_sequences (Gap D)."""
    sequences = build_player_sequences(obs_df, skill, external_priors=external_priors)
    q_value, _ = fit_q_mle(list(sequences.values()))

    rows = []
    for player_id, (y, R, mask, prior_mean, prior_var) in sequences.items():
        a, P, _, _ = kalman_filter_series(y, R, q_value, mask, prior_mean, prior_var)
        a_s, P_s = kalman_smoother_series(a, P, q_value)
        rows.append({
            "player_id": player_id,
            f"skill_{skill}": float(a_s[-1]),
            f"skill_var_{skill}": float(P_s[-1]),
            "_n_games_observed": int(mask.sum()),
        })
    return q_value, pd.DataFrame(rows)


def smooth_all_skills(
    obs_df: pd.DataFrame, external_priors_df: pd.DataFrame | None = None,
) -> tuple[dict[str, float], pd.DataFrame]:
    """Runs smooth_skill for every skill in SKILLS, merges into one
    per-player frame. Returns (fitted_Q_per_skill, merged frame).
    `external_priors_df`: the Shrinkage Baseline's full shrink_skills()
    output for this season (all skill_<skill>/_weight columns at once) — see
    build_player_sequences for the Gap D reasoning. None preserves the
    original flat-population-prior behavior."""
    fitted_q: dict[str, float] = {}
    merged: pd.DataFrame | None = None
    for skill in SKILLS:
        q_value, skill_df = smooth_skill(obs_df, skill, external_priors=external_priors_df)
        fitted_q[skill] = q_value
        skill_df = skill_df.drop(columns=["_n_games_observed"])
        merged = skill_df if merged is None else merged.merge(skill_df, on="player_id", how="outer")
    return fitted_q, merged


# =============================================================================
# Cross-Season State-Space Model
# =============================================================================
# The target model. Builds on Intra-Season Kalman Smoothing above rather
# than replacing it: that section's scalar Kalman filter/smoother is reused
# as-is, once per season, to produce one end-of-season smoothed skill
# estimate per player per season per skill. This section adds a second,
# season-grain Kalman layer on top of those season-ending estimates — the
# actual cross-season persistence (`rho`) and development-curve/transfer/
# level-change drift from the plan doc's §7 state evolution equation:
#
#     alpha[p,t+1] = rho * alpha[p,t] + mu[p,t] + epsilon[p,t]
#     mu[p,t] = beta_0 + beta_1*x + beta_2*x^2 + beta_3*transfer_flag + beta_4*level_change
#
# Two deliberate deviations from the plan doc's literal text, both because
# the data doesn't actually support the literal version:
#
# 1. **`x` is `career_season_index` (1, 2, 3, ... — rank among a player's
#    own observed game-log seasons), not literal `class_year`.**
#    `players.class_year` is a single column updated on every barttorvik
#    ingest re-run — it holds only the player's *most recently ingested*
#    class year, not a per-season history. Re-deriving historical class
#    year from it would require assuming no redshirts/grad years, which is
#    exactly the population (transfers) this model cares about.
#    `career_season_index` is directly computable from the data we actually
#    have and is arguably a better-motivated development-curve input anyway
#    (exposure-based, not eligibility-based).
#
# 2. **Block covariance is an empirical post-hoc residual-correlation
#    estimate, not a joint multivariate Kalman update.** Fitting a single
#    state vector per block (correlated process noise *during* filtering) is
#    a much bigger numerical-stability lift than the per-skill univariate
#    fits below. This section fits each skill's season-grain model
#    independently, then estimates the within-block correlation of
#    standardized one-step-ahead residuals as a diagnostic and a cross-skill
#    prior-blending input — the "shared priors informed by correlated
#    skills" version from plan doc §6's table, not the full joint-covariance
#    version. Documented as a scope decision, not silently downgraded.
#
# The season-level "observation" fed into this layer is the intra-season
# smoothed end-of-season estimate, with its own smoothed variance used as
# that observation's noise `R` — a standard hierarchical-Kalman composition:
# each season's intra-season filter answers "what do this season's games
# tell us", and this layer answers "how does that estimate evolve season to
# season."

# The intra-season filtering pass (build_season_skill_states) takes ~2h on
# the full 2020-2026 dataset — downstream callers all need its output as
# direct input (not just the fitted-param summary), so cache it instead of
# re-deriving on every call. Gitignored — same convention as
# data/features/*.parquet.
DEFAULT_CACHE_DIR = find_repo_root() / "data" / "features" / "player_projection_cross_season"

# §6's recommended blocks. foul_discipline is absent from SKILL_BLOCKS
# entirely (no season- or game-grain foul-rate data — see RAW_RATE_SKILLS/
# RATE_PER_40_SKILLS above), so defensive_playmaking only has 2 of its
# originally-recommended 3 members.
SKILL_BLOCKS: dict[str, list[str]] = {
    "shooting_touch": ["shooting_3p", "shooting_2p_finishing", "free_throw_touch"],
    "creation": ["shot_creation_usage", "passing_creation", "turnover_avoidance"],
    "rebounding": ["offensive_rebounding", "defensive_rebounding"],
    "defensive_playmaking": ["steal_disruption", "block_rim_protection"],
}

LEVEL_TIERS = 4

GAME_LOG_SQL = """
SELECT
    player_id, game_date, minutes, school_id, opponent_school_id, home_away,
    field_goals_made, field_goals_attempted,
    three_point_field_goals_made, three_point_field_goals_attempted,
    free_throws_made, free_throws_attempted,
    offensive_rebounds, defensive_rebounds,
    assists, steals, blocks, turnovers, fouls
FROM hoopr_player_game_logs
WHERE season = :season AND player_id IS NOT NULL
"""

TEAM_CONTEXT_SQL = """
SELECT school_id, season, adj_d, adj_tempo
FROM team_season_stats
WHERE season = :season
"""


def load_game_logs(engine: Engine, season: int) -> pd.DataFrame:
    with engine.connect() as conn:
        return pd.read_sql(text(GAME_LOG_SQL), conn, params={"season": season})


def load_game_context(engine: Engine, season: int) -> pd.DataFrame:
    """Per-team-season context for Gap B's observation-layer adjustment
    (Issue #37 item 2): opponent defensive strength (`adj_d`), the player's
    own team pace (`adj_tempo`), and competition tier (`compute_level_tier`).
    Returned per `school_id` so the caller joins it onto game logs twice —
    once as the player's own school (for pace/tier), once as the opponent
    (for opponent_adj_d) — see `attach_game_context`."""
    with engine.connect() as conn:
        team_context = pd.read_sql(text(TEAM_CONTEXT_SQL), conn, params={"season": season})
    tier_df = compute_level_tier(engine, [season])
    return team_context.merge(tier_df, on=["school_id", "season"], how="left")


def attach_game_context(game_logs: pd.DataFrame, team_context: pd.DataFrame) -> pd.DataFrame:
    """Joins `load_game_context`'s per-team frame onto game_logs twice (own
    school for pace/tier, opponent school for opponent_adj_d), plus a numeric
    `home_flag` (1.0 home, 0.0 away/neutral). Pure function — no DB — so it's
    unit-testable with synthetic frames."""
    out = game_logs.copy()
    own = team_context.rename(columns={"school_id": "school_id", "adj_tempo": "team_pace", "tier": "tier"})
    opp = team_context.rename(columns={"school_id": "opponent_school_id", "adj_d": "opponent_adj_d"})
    out = out.merge(own[["school_id", "team_pace", "tier"]], on="school_id", how="left")
    out = out.merge(opp[["opponent_school_id", "opponent_adj_d"]], on="opponent_school_id", how="left")
    out["home_flag"] = (out["home_away"].astype(str).str.lower() == "home").astype(float)
    return out


CONTEXT_COLS = ["opponent_adj_d", "team_pace", "tier", "home_flag"]


def _context_design_matrix(df: pd.DataFrame) -> pd.DataFrame:
    """Builds the context design matrix, imputing missing context (failed
    team-join, etc.) with each column's own mean rather than dropping rows —
    every game-row must stay in the panel the Kalman filter sees, even if its
    context adjustment ends up being a no-op (imputed-to-mean contributes ~0
    net adjustment once centered in apply_context_adjustment)."""
    X = df[CONTEXT_COLS].astype(np.float64)
    for col in CONTEXT_COLS:
        X[col] = X[col].fillna(X[col].mean())
        if X[col].isna().all():
            X[col] = 0.0
    return X


def fit_context_adjustment(obs_df: pd.DataFrame, skill: str) -> LinearRegression | None:
    """Fits a single weighted linear regression of the raw observed skill
    rate on opponent strength / team pace / competition tier / home-away —
    the additive observation-layer context term from plan doc §7's
    `y = Z*alpha + level_intercept + opponent_adjustment + ... + noise` form,
    fit once (not per-game-by-game), then subtracted from the observation
    before it reaches the Kalman update (`apply_context_adjustment`).

    Returns None if there isn't enough weighted, context-complete data to
    fit at all (e.g. an empty or all-missing-context frame) — callers must
    treat that as "no adjustment available," not crash.
    """
    y_col, w_col = f"y_{skill}", f"weight_{skill}"
    valid = obs_df[obs_df[w_col] > 0].dropna(subset=[y_col])
    if len(valid) < 30:
        return None
    X = _context_design_matrix(valid)
    weights = valid[w_col].clip(lower=1e-6).to_numpy(dtype=np.float64)
    model = LinearRegression()
    model.fit(X, valid[y_col].to_numpy(dtype=np.float64), sample_weight=weights)
    return model


def apply_context_adjustment(obs_df: pd.DataFrame, skill: str, model: LinearRegression | None) -> pd.Series:
    """Returns a context-neutralized `y_{skill}` series: observed rate minus
    the fitted context effect, re-centered on the population-weighted mean
    predicted effect so the adjusted series stays on the same natural scale
    the rest of the pipeline (priors, R_t, etc.) expects — only the
    game-to-game variation *explained by context* is removed, not the
    skill's overall level. Returns the original column unchanged if `model`
    is None (no fit was possible)."""
    y_col, w_col = f"y_{skill}", f"weight_{skill}"
    if model is None:
        return obs_df[y_col]
    X = _context_design_matrix(obs_df)
    predicted = model.predict(X)
    weights = obs_df[w_col].clip(lower=1e-6).to_numpy(dtype=np.float64)
    population_mean_predicted = float(np.average(predicted, weights=weights))
    return obs_df[y_col] - (predicted - population_mean_predicted)


def compute_level_tier(engine: Engine, seasons: list[int]) -> pd.DataFrame:
    """level_TP (plan doc §5): 4 quantile buckets of team_season_stats.adj_em,
    recomputed independently per season — conference strength drifts year to
    year, so this is never cached across seasons. tier=4 is the strongest
    quartile that season, tier=1 the weakest."""
    sql = "SELECT school_id, season, adj_em FROM team_season_stats WHERE season = ANY(:seasons) AND adj_em IS NOT NULL"
    with engine.connect() as conn:
        df = pd.read_sql(text(sql), conn, params={"seasons": seasons})
    df["tier"] = (
        df.groupby("season")["adj_em"].transform(
            lambda x: pd.qcut(x, LEVEL_TIERS, labels=False, duplicates="drop")
        )
        + 1
    )
    return df[["school_id", "season", "tier"]]


def build_season_skill_states(
    engine: Engine, seasons: list[int], use_baseline_prior: bool = True, max_workers: int | None = None,
    player_id_subset: set[int] | None = None, use_context_adjustment: bool = False,
) -> tuple[dict[int, dict[str, float]], pd.DataFrame]:
    """Runs the intra-season filter+smoother once per (season, skill) pair.
    Returns (fitted_Q per season per skill, one merged frame with
    skill_<s>/skill_var_<s> per player per season — the season-grain layer's
    raw "observations").

    `use_baseline_prior` (Gap D, Issue #37 reconciliation, 2026-06-23):
    when True (default), loads the Shrinkage Baseline's `shrink_skills()`
    output once for all seasons and passes each season's slice to
    `smooth_skill`'s `external_priors` argument, so the intra-season filter
    starts from the Shrinkage Baseline's position x season shrinkage
    estimate instead of a flat population mean — see `build_player_sequences`'s
    docstring for the full reasoning. `shrink_skills` groups by season
    internally, so loading and shrinking once here (not per-season) is
    correct and avoids redundant work.

    Performance (2026-06-24): every (season, skill) pair is an independent
    fit — nothing about season 2023's `shooting_3p` fit depends on 2024's or
    on `passing_creation`'s. The original implementation called
    `smooth_all_skills` once per season, which looped over the 10 skills
    serially *inside* that call — meaning up to 70 independent fits (7
    seasons x 10 skills) ran one at a time. This flattens that into one flat
    list of up to 70 tasks submitted to a single `ProcessPoolExecutor`
    (`max_workers=None` uses all available cores), instead of nesting a
    skills-pool inside a seasons-loop — nested process pools are themselves a
    real footgun (multiprocessing-within-multiprocessing is fragile and often
    silently serializes or deadlocks), so this flat structure is deliberate,
    not an oversight. Results are collected into a plain dict first and then
    re-assembled in canonical `seasons`/`SKILLS` order — `as_completed()`'s
    order is non-deterministic across runs, and the original per-season
    merge order must stay reproducible regardless of which worker finishes
    first.

    `player_id_subset` (2026-06-24, proxy-run support): when given, restricts
    every season's game logs (and the prior lookup) to this player set before
    any fitting happens — not a sampling step inside the math itself, just a
    smaller population fed through the unchanged pipeline. For getting a
    fast read on model behavior (sign/magnitude of fitted rho/beta/Q per
    skill, sanity of smoothed trajectories) before committing to a full
    real-data run — not used in the production path.

    `use_context_adjustment` (Gap B, Issue #37 reconciliation, 2026-06-24):
    when True, attaches opponent/pace/tier/home-away context to each
    season's observations (`load_game_context`/`attach_game_context`) and,
    per skill, fits a context-adjustment regression and subtracts the
    explained context effect from that skill's `y_<skill>` column
    (`fit_context_adjustment`/`apply_context_adjustment`) *before* it reaches
    the Kalman filter — the additive `opponent_adjustment` term from the plan
    doc's §7 observation equation. Each skill needs its own context-adjusted
    copy of the season's observations (the fitted regression differs per
    skill), so this branch builds one `obs_df` per (season, skill) instead of
    sharing one per season across skills.
    """
    baseline_shrunk: pd.DataFrame | None = None
    if use_baseline_prior:
        baseline_df = load_player_season_frame(engine)
        if player_id_subset is not None:
            baseline_df = baseline_df[baseline_df["player_id"].isin(player_id_subset)]
        baseline_shrunk = shrink_skills(baseline_df)

    season_obs: dict[int, pd.DataFrame] = {}
    for season in seasons:
        game_logs = load_game_logs(engine, season)
        if player_id_subset is not None:
            game_logs = game_logs[game_logs["player_id"].isin(player_id_subset)]
        if game_logs.empty:
            log.warning("No game logs for season %d, skipping", season)
            continue
        obs_df = build_game_observations(game_logs)
        if use_context_adjustment:
            team_context = load_game_context(engine, season)
            obs_df = attach_game_context(obs_df, team_context)
        season_obs[season] = obs_df

    tasks: list[tuple[int, str, pd.DataFrame, pd.DataFrame | None]] = []
    for season, obs_df in season_obs.items():
        external_priors_df = None
        if baseline_shrunk is not None:
            season_priors = baseline_shrunk[baseline_shrunk["season"] == season]
            external_priors_df = season_priors if not season_priors.empty else None
        for skill in SKILLS:
            y_col, w_col = f"y_{skill}", f"weight_{skill}"
            # Slim to exactly what smooth_skill/build_player_sequences reads
            # (player_id + this skill's y_/weight_ only) -- a real memory bug
            # surfaced here on the first Gap B real run: building all 70
            # (season, skill) tasks eagerly, each a *full* obs_df.copy()
            # (every skill's columns + raw game-log columns + context columns,
            # ~67MB with context attached), put ~4.7GB of redundant data in
            # this list before any submission even started, and crashed the
            # ProcessPoolExecutor's workers (BrokenProcessPool, almost
            # certainly an OS-level OOM kill, not a Python exception). Each
            # task only ever needed 3 columns.
            if use_context_adjustment:
                context_model = fit_context_adjustment(obs_df, skill)
                adjusted_y = apply_context_adjustment(obs_df, skill, context_model)
                skill_obs_df = pd.DataFrame({"player_id": obs_df["player_id"], y_col: adjusted_y, w_col: obs_df[w_col]})
            else:
                skill_obs_df = obs_df[["player_id", y_col, w_col]]
            tasks.append((season, skill, skill_obs_df, external_priors_df))

    results: dict[tuple[int, str], tuple[float, pd.DataFrame]] = {}
    with concurrent.futures.ProcessPoolExecutor(max_workers=max_workers) as executor:
        future_to_key = {
            executor.submit(smooth_skill, obs_df, skill, ext): (season, skill)
            for season, skill, obs_df, ext in tasks
        }
        for future in concurrent.futures.as_completed(future_to_key):
            key = future_to_key[future]
            results[key] = future.result()

    fitted_q_by_season: dict[int, dict[str, float]] = {}
    frames: list[pd.DataFrame] = []
    for season in seasons:
        if season not in season_obs:
            continue
        fitted_q: dict[str, float] = {}
        merged: pd.DataFrame | None = None
        for skill in SKILLS:
            q_value, skill_df = results[(season, skill)]
            fitted_q[skill] = q_value
            skill_df = skill_df.drop(columns=["_n_games_observed"])
            merged = skill_df if merged is None else merged.merge(skill_df, on="player_id", how="outer")
        merged["season"] = season
        fitted_q_by_season[season] = fitted_q
        frames.append(merged)
        n_with_prior = (
            len(baseline_shrunk[baseline_shrunk["season"] == season]) if baseline_shrunk is not None else 0
        )
        log.info(
            "Season %d: %d players, intra-season filter complete (%d with the Shrinkage Baseline prior)",
            season, len(merged), n_with_prior,
        )
    merged_all = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    return fitted_q_by_season, merged_all


def load_or_build_season_skill_states(
    engine: Engine, seasons: list[int], cache_dir: Path | None = None, force_rebuild: bool = False,
    use_baseline_prior: bool = True, use_context_adjustment: bool = False,
    max_workers: int | None = None,
) -> tuple[dict[int, dict[str, float]], pd.DataFrame]:
    """Cached wrapper around build_season_skill_states. The ~2h intra-season
    filtering pass is identical every time it's run against the same seasons
    *and* the same prior-sourcing (deterministic given the same data) — there's
    no reason to pay that cost once per caller (every downstream consumer
    needs this exact output). `use_baseline_prior` is part of the cache
    filename (Gap D, Issue #37 reconciliation) specifically so a flat-prior
    cache and a baseline-prior cache can't collide — set force_rebuild=True
    after a real upstream data change (e.g. another game-log backfill), not
    needed just to flip this flag. `use_context_adjustment` (Gap B) is part
    of the cache filename for the same reason.

    `seasons` is part of the cache filename too (real bug, found 2026-06-25):
    the original version only varied by prior/context suffix, so calling
    this with a *different* `seasons` list (e.g. a quick 2-season check after
    a full 2020-2026 run) would silently load the wrong cached states —
    same data shape, wrong season coverage, no error."""
    cache_dir = cache_dir or DEFAULT_CACHE_DIR
    prior_suffix = "baselineprior" if use_baseline_prior else "flatprior"
    context_suffix = "ctxadj" if use_context_adjustment else "noctx"
    seasons_suffix = "-".join(str(s) for s in sorted(seasons))
    states_path = cache_dir / f"season_states_{prior_suffix}_{context_suffix}_{seasons_suffix}.parquet"
    q_path = cache_dir / f"fitted_q_by_season_{prior_suffix}_{context_suffix}_{seasons_suffix}.json"

    if not force_rebuild and states_path.exists() and q_path.exists():
        log.info("Loading cached season skill states from %s", states_path)
        season_states = pd.read_parquet(states_path)
        fitted_q_by_season = {int(k): v for k, v in json.loads(q_path.read_text()).items()}
        return fitted_q_by_season, season_states

    fitted_q_by_season, season_states = build_season_skill_states(
        engine, seasons, use_baseline_prior=use_baseline_prior, use_context_adjustment=use_context_adjustment,
        max_workers=max_workers,
    )
    cache_dir.mkdir(parents=True, exist_ok=True)
    season_states.to_parquet(states_path)
    q_path.write_text(json.dumps(fitted_q_by_season))
    log.info("Cached season skill states to %s", states_path)
    return fitted_q_by_season, season_states


def build_season_covariates(engine: Engine, season_states: pd.DataFrame) -> pd.DataFrame:
    """career_season_index (rank within each player's own observed seasons),
    transfer_flag, and level_change for every (player_id, season) row in
    season_states. See module docstring for why career_season_index replaces
    literal class_year."""
    df = season_states[["player_id", "season"]].drop_duplicates().sort_values(["player_id", "season"]).copy()
    df["career_season_index"] = df.groupby("player_id").cumcount() + 1

    with engine.connect() as conn:
        transfers = pd.read_sql(
            text("SELECT player_id, season, from_school_id, to_school_id FROM transfers"), conn,
        )
    transfers["transfer_flag"] = 1.0

    seasons = sorted(df["season"].unique().tolist())
    tier_df = compute_level_tier(engine, seasons + [min(seasons) - 1])

    to_tier = tier_df.rename(columns={"school_id": "to_school_id", "tier": "to_tier"})
    transfers = transfers.merge(to_tier, on=["to_school_id", "season"], how="left")

    transfers["prev_season"] = transfers["season"] - 1
    from_tier = tier_df.rename(columns={"school_id": "from_school_id", "season": "prev_season", "tier": "from_tier"})
    transfers = transfers.merge(from_tier, on=["from_school_id", "prev_season"], how="left")
    transfers["level_change"] = (transfers["to_tier"] - transfers["from_tier"]).fillna(0.0)

    df = df.merge(
        transfers[["player_id", "season", "transfer_flag", "level_change"]],
        on=["player_id", "season"], how="left",
    )
    df["transfer_flag"] = df["transfer_flag"].fillna(0.0)
    df["level_change"] = df["level_change"].fillna(0.0)
    return df


def load_or_build_season_covariates(
    engine: Engine, season_states: pd.DataFrame, cache_dir: Path | None = None, force_rebuild: bool = False,
) -> pd.DataFrame:
    """Cached wrapper around build_season_covariates — same rationale as
    load_or_build_season_skill_states, though this step is cheap on its own
    (a few DB queries, not 2h of filtering); cached mainly so a full
    load_or_build_season_skill_states + load_or_build_season_covariates pair
    is a single fast no-op call once both are warm.

    Cache filename includes the seasons actually present in `season_states`
    (same real-bug fix as `load_or_build_season_skill_states` — a constant
    `"covariates.parquet"` filename would silently return covariates built
    for a different season range)."""
    cache_dir = cache_dir or DEFAULT_CACHE_DIR
    seasons_suffix = "-".join(str(s) for s in sorted(season_states["season"].unique().tolist()))
    covariates_path = cache_dir / f"covariates_{seasons_suffix}.parquet"

    if not force_rebuild and covariates_path.exists():
        log.info("Loading cached season covariates from %s", covariates_path)
        return pd.read_parquet(covariates_path)

    covariates = build_season_covariates(engine, season_states)
    cache_dir.mkdir(parents=True, exist_ok=True)
    covariates.to_parquet(covariates_path)
    log.info("Cached season covariates to %s", covariates_path)
    return covariates


def kalman_filter_with_drift(
    y: np.ndarray, R: np.ndarray, rho: float, mu: np.ndarray, q_value: float,
    mask: np.ndarray, prior_mean: float, prior_var: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Season-grain filter: alpha_t = rho*alpha_(t-1) + mu_t + eps_t. mu_t is
    precomputed per timestep from that player-season's covariates and the
    candidate beta params — this function just runs the filter recursion
    given mu already evaluated, same interface shape as
    `kalman_filter_series` above but with an AR coefficient and a
    time-varying (not zero) drift term."""
    n = len(y)
    a = np.empty(n)
    p_var = np.empty(n)
    pred_mean = np.empty(n)
    pred_var = np.empty(n)
    a_prev, p_prev = prior_mean, prior_var
    for t in range(n):
        a_pred = rho * a_prev + mu[t]
        p_pred = rho * rho * p_prev + q_value
        pred_mean[t] = a_pred
        pred_var[t] = p_pred + R[t]
        if mask[t]:
            k_gain = p_pred / (p_pred + R[t])
            a_t = a_pred + k_gain * (y[t] - a_pred)
            p_t = (1.0 - k_gain) * p_pred
        else:
            a_t, p_t = a_pred, p_pred
        a[t], p_var[t] = a_t, p_t
        a_prev, p_prev = a_t, p_t
    return a, p_var, pred_mean, pred_var


SeasonSequence = tuple[
    np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, float, float, np.ndarray,
]
# y, R, mask, career_season_index, transfer_flag, level_change, prior_mean, prior_var, seasons
#
# `seasons` (2026-06-25): the real season year per row, same order as `y` --
# added because `smooth_season_skill`'s output previously carried only a
# synthetic positional `season_rank` (np.arange(1, len(y)+1)), independently
# re-derived from this same sequence rather than reading the real value that
# was already in scope here. That's a real risk, not just style: if any
# skill's `dropna(subset=[y_col, var_col])` above drops a *different* season
# for a player than another skill does (plausible — different skills can
# have different per-season data availability), their `season_rank`
# sequences silently desync relative to each other, and `fit_all_skills`'
# merge (previously keyed on `season_rank`) would merge mismatched seasons
# across skills with no error. Carrying the real season through removes that
# whole class of risk — merges can key on `season` directly.

PARAM_NAMES = ["rho", "beta_0", "beta_1", "beta_2", "beta_3", "beta_4", "log_Q"]


def build_season_sequences(
    skill_df: pd.DataFrame, covariates: pd.DataFrame, skill: str,
) -> dict[int, SeasonSequence]:
    """One SeasonSequence per player_id, ordered by season, for one skill."""
    y_col, var_col = f"skill_{skill}", f"skill_var_{skill}"
    merged = skill_df[["player_id", "season", y_col, var_col]].merge(
        covariates, on=["player_id", "season"], how="inner",
    ).sort_values(["player_id", "season"])
    merged = merged.dropna(subset=[y_col, var_col])

    valid_mask_all = merged[y_col].notna()
    prior_mean = float(merged.loc[valid_mask_all, y_col].mean())
    prior_var = float(merged.loc[valid_mask_all, y_col].var(ddof=1)) * 4.0
    if not np.isfinite(prior_var) or prior_var <= 0:
        prior_var = 1.0

    sequences: dict[int, SeasonSequence] = {}
    for player_id, g in merged.groupby("player_id"):
        n = len(g)
        mask = np.ones(n, dtype=bool)
        y = g[y_col].to_numpy(dtype=np.float64)
        r_arr = g[var_col].clip(lower=1e-6).to_numpy(dtype=np.float64)
        csi = g["career_season_index"].to_numpy(dtype=np.float64)
        transfer_flag = g["transfer_flag"].to_numpy(dtype=np.float64)
        level_change = g["level_change"].to_numpy(dtype=np.float64)
        seasons_arr = g["season"].to_numpy(dtype=np.int64)
        sequences[int(player_id)] = (
            y, r_arr, mask, csi, transfer_flag, level_change, prior_mean, prior_var, seasons_arr,
        )
    return sequences


def _season_pooled_neg_log_likelihood(params: np.ndarray, sequences: list[SeasonSequence]) -> float:
    rho, beta_0, beta_1, beta_2, beta_3, beta_4, log_q = params
    q_value = float(np.exp(log_q))
    if not (0.0 <= rho <= 1.2) or q_value <= 0:
        return np.inf
    total = 0.0
    for y, r_arr, mask, csi, transfer_flag, level_change, prior_mean, prior_var, _ in sequences:
        mu = beta_0 + beta_1 * csi + beta_2 * csi**2 + beta_3 * transfer_flag + beta_4 * level_change
        _, _, pred_mean, pred_var = kalman_filter_with_drift(
            y, r_arr, rho, mu, q_value, mask, prior_mean, prior_var,
        )
        if not mask.any():
            continue
        var = pred_var[mask]
        err = y[mask] - pred_mean[mask]
        total += 0.5 * float(np.sum(np.log(2 * np.pi * var) + err**2 / var))
    return total


def _q_init_from_diffs(sequences: list[SeasonSequence]) -> float:
    y_diffs = []
    for y, _, mask, _, _, _, _, _, _ in sequences:
        obs = y[mask]
        if len(obs) > 1:
            y_diffs.extend(np.diff(obs).tolist())
    q_init = float(np.var(y_diffs)) if len(y_diffs) > 5 else 0.1
    return max(q_init, 1e-4)


def estimate_rho_autocorrelation(
    skill_df: pd.DataFrame, covariates: pd.DataFrame, skill: str,
    clip: tuple[float, float] = (0.2, 0.95),
) -> float | None:
    """Estimates a population-level `rho` as the pooled lag-1 Pearson
    correlation of consecutive-season smoothed skill estimates — deliberately
    *not* a state-space MLE estimate.

    Real finding (2026-06-23): jointly fitting `rho` and the drift terms via
    pooled MLE is not just unreliable on short sequences (the original
    finding — see git history / plan doc — multi-start optimization landing
    on a wrong-but-genuinely-better-likelihood optimum), it stayed broken even
    after trying the natural fixes: pooling `rho` from only the long-career
    subset (still landed near 0 — even our longest real sequences, capped at
    7 seasons by the game-log backfill, aren't long enough to identify `rho`
    independently of the trend terms), and adding a Gaussian MAP-style prior
    penalty on `rho` (the likelihood's pull toward the degenerate rho->0
    solution was large enough — hundreds of NLL units in testing — to swamp
    even a fairly tight prior). All three attempts are preserved in
    tests/test_player_projection.py as documented failure modes, not
    deleted, since the next person touching this should not have to
    rediscover why a "smarter prior" or "longer subset" won't fix it either.

    Simple lag-1 autocorrelation sidesteps the problem because it isn't
    jointly estimated with anything — it's a single, well-identified
    statistic computed directly from the observed (smoothed) skill series,
    with no competing parameter for the optimizer to trade it against. It
    will run somewhat high when a real trend is present (trending series have
    inflated lag-1 correlation independent of true persistence), which is why
    `clip` exists — bounds it away from both false near-1.0 (would make the
    season-level filter barely move) and the MLE's degenerate near-0 region.
    """
    merged = skill_df[["player_id", "season", f"skill_{skill}"]].merge(
        covariates[["player_id", "season"]], on=["player_id", "season"], how="inner",
    ).sort_values(["player_id", "season"])
    merged["next_value"] = merged.groupby("player_id")[f"skill_{skill}"].shift(-1)
    consecutive = merged.dropna(subset=[f"skill_{skill}", "next_value"])
    if len(consecutive) < 50:
        return None
    raw_rho = float(np.corrcoef(consecutive[f"skill_{skill}"], consecutive["next_value"])[0, 1])
    if not np.isfinite(raw_rho):
        return None
    clipped_rho = float(np.clip(raw_rho, clip[0], clip[1]))
    # Log the raw value even when clipping doesn't change it — a value that
    # lands exactly at one of the bounds (like shooting_3p's 0.200 in the
    # first real run) is otherwise indistinguishable from "the raw value was
    # 0.19" vs. "the raw value was -0.4," which mean very different things.
    if clipped_rho != raw_rho:
        log.warning(
            "Skill %-24s rho clipped: raw=%.4f -> clipped=%.4f (n=%d pairs)",
            skill, raw_rho, clipped_rho, len(consecutive),
        )
    else:
        log.info("Skill %-24s rho (unclipped): %.4f (n=%d pairs)", skill, raw_rho, len(consecutive))
    return clipped_rho


MAX_SEQUENCES_FOR_SEASON_SEARCH = 1000


def fit_season_model(
    sequences: list[SeasonSequence], fixed_rho: float | None = None,
    max_sequences_for_search: int | None = MAX_SEQUENCES_FOR_SEASON_SEARCH, random_state: int = 0,
) -> dict[str, float]:
    """Pooled MLE for one skill's season-grain sequences.

    If `fixed_rho` is given, optimizes only (beta_0..4, Q) with `rho` held
    fixed — this is the recommended path (see `estimate_rho_autocorrelation`'s
    docstring for why joint estimation isn't reliable on this data's mostly
    2-4-season sequences). If `fixed_rho` is None, optimizes the full
    (rho, beta_0..4, Q) — kept available for the long-sequence subset
    `estimate_rho_autocorrelation` itself uses, and for diagnostics.

    Initial guess: rho=0.8 (mean-reverting prior, not a pure random walk) when
    rho is free; all betas=0; Q from the empirical season-to-season variance —
    a flat/no-drift start the optimizer should move away from if the data
    supports real persistence/drift effects.

    Performance note (2026-06-24): this is a pooled population-level fit
    (beta_0..4, Q), the same shape of cost/identifiability tradeoff as
    `fit_q_mle`'s Q-search above — found, via a live `py-spy dump` of an
    actual stuck run, to be the *real* dominant cost in the full pipeline
    (Nelder-Mead on 6 free dimensions needs far more function evaluations
    than the 1-D Brent search that motivated the original Q-search
    subsampling fix, and each evaluation loops over every player's full
    sequence). Same fix, same justification: search on a deterministic
    random subsample, since `beta_0..4`/`Q` are pooled estimates that don't
    need the full population to converge to essentially the same value.
    """
    search_sequences = sequences
    if max_sequences_for_search is not None and len(sequences) > max_sequences_for_search:
        rng = np.random.default_rng(random_state)
        idx = rng.choice(len(sequences), size=max_sequences_for_search, replace=False)
        search_sequences = [sequences[i] for i in idx]

    q_init = _q_init_from_diffs(search_sequences)

    if fixed_rho is not None:
        def _nll_fixed(params: np.ndarray) -> float:
            beta_0, beta_1, beta_2, beta_3, beta_4, log_q = params
            full = np.array([fixed_rho, beta_0, beta_1, beta_2, beta_3, beta_4, log_q])
            return _season_pooled_neg_log_likelihood(full, search_sequences)

        x0 = np.array([0.0, 0.0, 0.0, 0.0, 0.0, np.log(q_init)])
        result = minimize(
            _nll_fixed, x0, method="Nelder-Mead",
            options={"maxiter": 2000, "xatol": 1e-5, "fatol": 1e-5},
        )
        fitted = dict(zip(PARAM_NAMES[1:], result.x))  # skip "rho"
        fitted["rho"] = float(fixed_rho)
    else:
        x0 = np.array([0.8, 0.0, 0.0, 0.0, 0.0, 0.0, np.log(q_init)])
        result = minimize(
            _season_pooled_neg_log_likelihood, x0, args=(search_sequences,),
            method="Nelder-Mead",
            options={"maxiter": 2000, "xatol": 1e-5, "fatol": 1e-5},
        )
        fitted = dict(zip(PARAM_NAMES, result.x))

    fitted["Q"] = float(np.exp(fitted.pop("log_Q")))
    fitted["neg_log_likelihood"] = float(result.fun)
    fitted["converged"] = bool(result.success)
    fitted["rho_fixed"] = fixed_rho is not None
    return fitted


def smooth_season_skill(
    skill_df: pd.DataFrame, covariates: pd.DataFrame, skill: str,
) -> tuple[dict[str, float], pd.DataFrame]:
    """Fits the season-grain model for one skill: `rho` from pooled lag-1
    autocorrelation (see `estimate_rho_autocorrelation` for why this, not
    MLE), fixed, then drift terms (beta_0..4) fit by MLE on the full
    population. Returns (fitted params dict, frame with one row per
    (player_id, season): filtered mean/var plus the standardized one-step-
    ahead residual, used for block-correlation diagnostics in
    compute_block_correlations)."""
    rho = estimate_rho_autocorrelation(skill_df, covariates, skill)
    sequences = build_season_sequences(skill_df, covariates, skill)
    seq_list = list(sequences.values())
    fitted = fit_season_model(seq_list, fixed_rho=rho)
    rho, beta_0, beta_1, beta_2, beta_3, beta_4 = (
        fitted["rho"], fitted["beta_0"], fitted["beta_1"], fitted["beta_2"], fitted["beta_3"], fitted["beta_4"],
    )
    q_value = fitted["Q"]

    rows = []
    for player_id, (y, r_arr, mask, csi, transfer_flag, level_change, prior_mean, prior_var, seasons_arr) in sequences.items():
        mu = beta_0 + beta_1 * csi + beta_2 * csi**2 + beta_3 * transfer_flag + beta_4 * level_change
        a, p_var, pred_mean, pred_var = kalman_filter_with_drift(
            y, r_arr, rho, mu, q_value, mask, prior_mean, prior_var,
        )
        std_resid = (y - pred_mean) / np.sqrt(pred_var)
        seasons_for_player = np.arange(1, len(y) + 1)  # positional ordering only -- see "season" below for the real key
        for i in range(len(y)):
            rows.append({
                "player_id": player_id,
                "season": int(seasons_arr[i]),
                "season_rank": int(seasons_for_player[i]),
                f"phase2_skill_{skill}": float(a[i]),
                f"phase2_skill_var_{skill}": float(p_var[i]),
                f"std_resid_{skill}": float(std_resid[i]),
            })
    return fitted, pd.DataFrame(rows)


def fit_all_skills(
    skill_df: pd.DataFrame, covariates: pd.DataFrame, max_workers: int | None = None,
) -> tuple[dict[str, dict], pd.DataFrame]:
    """Runs smooth_season_skill for every skill, merges into one per
    (player_id, season) frame (season, not season_rank — see SeasonSequence's
    docstring for why season is the more reliable merge key).

    Performance (2026-06-24): each skill's `smooth_season_skill` fit is fully
    independent (different column slice of `skill_df`, no shared state) —
    confirmed via `py-spy dump` to be the actual dominant cost in the full
    pipeline (see `fit_season_model`'s docstring). Runs all `len(SKILLS)`
    fits through one `ProcessPoolExecutor` (`max_workers=None` uses every
    available core) instead of the original serial loop. Results are
    collected into a dict first and reassembled in canonical `SKILLS` order
    before merging — `as_completed()`'s order is non-deterministic across
    runs, and the merge order/output must stay reproducible regardless of
    which worker finishes first.
    """
    results: dict[str, tuple[dict, pd.DataFrame]] = {}
    with concurrent.futures.ProcessPoolExecutor(max_workers=max_workers) as executor:
        future_to_skill = {
            executor.submit(smooth_season_skill, skill_df, covariates, skill): skill for skill in SKILLS
        }
        for future in concurrent.futures.as_completed(future_to_skill):
            skill = future_to_skill[future]
            results[skill] = future.result()

    fitted_params: dict[str, dict] = {}
    merged: pd.DataFrame | None = None
    for skill in SKILLS:
        fitted, skill_df_result = results[skill]
        fitted_params[skill] = fitted
        # Merge key is the real "season" (2026-06-25), not "season_rank" --
        # season_rank is computed independently inside each skill's own
        # build_season_sequences call, and two skills can drop different
        # seasons for the same player (different per-skill data
        # availability), desyncing their season_rank sequences relative to
        # each other. "season" doesn't have that risk. Each skill's own
        # season_rank column is dropped before merging (not a reliable
        # cross-skill value once it's no longer the join key) -- callers
        # needing season_rank should recompute it from `covariates`'
        # `career_season_index` after joining on (player_id, season).
        skill_df_result = skill_df_result.drop(columns=["season_rank"])
        merged = (
            skill_df_result if merged is None
            else merged.merge(skill_df_result, on=["player_id", "season"], how="outer")
        )
        log.info(
            "Skill %-24s rho=%.3f (fixed=%s) beta=[%.3f %.3f %.3f %.3f %.3f] Q=%.4f converged=%s",
            skill, fitted["rho"], fitted["rho_fixed"], fitted["beta_0"], fitted["beta_1"], fitted["beta_2"],
            fitted["beta_3"], fitted["beta_4"], fitted["Q"], fitted["converged"],
        )
    return fitted_params, merged


def compute_block_correlations(residual_df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Empirical correlation of standardized one-step-ahead residuals within
    each SKILL_BLOCKS group — the "block covariance" deliverable, per the
    module docstring's scope decision (diagnostic + shared-prior input, not
    a joint multivariate Kalman update)."""
    correlations: dict[str, pd.DataFrame] = {}
    for block_name, skills_in_block in SKILL_BLOCKS.items():
        cols = [f"std_resid_{s}" for s in skills_in_block if f"std_resid_{s}" in residual_df.columns]
        if len(cols) < 2:
            continue
        correlations[block_name] = residual_df[cols].corr()
    return correlations


# Gap A (Issue #37 reconciliation, 2026-06-23): only the 2 blocks that
# actually validated against §6's hypotheses (plan doc §22) — creation
# (passing_creation vs turnover_avoidance resid corr 0.35) and rebounding
# (offensive vs defensive rebounding resid corr 0.41). shooting_touch (weak/
# mixed) and defensive_playmaking (near-zero) are deliberately excluded —
# blending priors within an unvalidated block would manufacture a false
# cross-skill signal, not capture a real one.
VALIDATED_BLOCKS = ("creation", "rebounding")


def blend_block_priors(
    residual_df: pd.DataFrame,
    block_correlations: dict[str, pd.DataFrame],
    validated_blocks: tuple[str, ...] = VALIDATED_BLOCKS,
) -> pd.DataFrame:
    """Upgrades the block-correlation diagnostic into an actual shared-prior
    mechanism, for validated blocks only (Gap A).

    For each skill in a validated block, finds its most-correlated block-mate
    and applies a simple linear (MMSE-style) adjustment:

        adjusted_skill = cross_season_skill
                          + correlation * std_resid[block-mate]
                            * sqrt(cross_season_skill_var)

    This nudges a skill's season-grain estimate using the correlated
    block-mate's standardized one-step-ahead residual (how much that skill
    over/under-performed its own model's expectation that season), scaled by
    the empirical correlation and converted back into the skill's natural
    units via its own forecast variance. This is the documented "shared
    priors informed by correlated skills" version from plan doc §6's table —
    not a joint multivariate Kalman update (full covariance filtering stays
    deferred, see the implementation-time plan's "Explicitly Deferred"
    section for the concrete triggers to revisit that).

    Adds `phase2_skill_{skill}_blended` columns for skills in
    `validated_blocks`; skills outside those blocks (or blocks missing from
    `block_correlations`) are left unchanged — no `_blended` column is added
    for them, so callers can tell which skills actually got this treatment.
    """
    out = residual_df.copy()
    for block_name in validated_blocks:
        skills_in_block = SKILL_BLOCKS.get(block_name, [])
        corr = block_correlations.get(block_name)
        if corr is None or len(skills_in_block) < 2:
            continue
        for skill in skills_in_block:
            other_skills = [s for s in skills_in_block if s != skill]
            available = [s for s in other_skills if f"std_resid_{s}" in out.columns]
            if not available:
                continue
            best_mate, best_corr = max(
                ((s, corr.loc[f"std_resid_{skill}", f"std_resid_{s}"]) for s in available),
                key=lambda pair: abs(pair[1]),
            )
            base_col, var_col = f"phase2_skill_{skill}", f"phase2_skill_var_{skill}"
            if base_col not in out.columns or var_col not in out.columns:
                continue
            adjustment = best_corr * out[f"std_resid_{best_mate}"] * np.sqrt(out[var_col].clip(lower=0.0))
            out[f"{base_col}_blended"] = out[base_col] + adjustment
    return out


# Gap C (Issue #37 reconciliation, 2026-06-24): real per-40/per-100 rate
# projections for `projected_rates`/`projected_box_score` (item 3), feeding
# Gap F's write path. Two stages, per the plan:
#
# Stage 2B (conditional rates) turns out to need no new regression at all:
# `passing_creation`/`offensive_rebounding`/`defensive_rebounding`/
# `steal_disruption`/`block_rim_protection`/`turnover_avoidance` are *already*
# per-40 rates by construction (`RATE_PER_40_SKILLS` above literally defines
# them that way) — their season-grain smoothed state already *is* the
# projected per-40 rate. Stage 2B is `STAGE_2B_RATE_SKILLS` below: a direct
# relabeling, not a model.
#
# Stage 2A (possession outcome) is the real new work: `shot_creation_usage`
# is a single total-volume number (FGA + 0.44*FTA per 40); none of the
# existing skills capture how that volume splits across 2PA/3PA/FT-trip
# attempts. Documented adaptation (same honesty standard as the rest of this
# module): we only have aggregated box-score counts, not true per-possession
# PBP, so "possession outcome" here means season-aggregated per-40 attempt
# *rates* for {2PA, 3PA, FT-trip}, regressed on the shooting-percentage
# skills + total volume + position — not a literal multinomial over
# possession-level events, and not constrained to sum to `shot_creation_usage`
# exactly (a real simplification, stated rather than hidden). The plan doc's
# "other" category (possessions a player didn't personally end) is dropped
# entirely — nothing in the available box-score data identifies it, and
# inventing a residual category would manufacture a number with no real
# basis. Make/miss splits for 2PA/3PA are then derived by multiplying the
# fitted attempt rate by the corresponding shooting-percentage skill
# (already a probability in [0, 1]) — `FT trip` has no make/miss split in
# the plan's own category list (the make/miss is `free_throw_touch`'s job).

STAGE_2A_FEATURE_SKILLS = ["shooting_3p", "shooting_2p_finishing", "free_throw_touch", "shot_creation_usage"]
STAGE_2A_TARGETS = ("rate_2pa_attempted", "rate_3pa_attempted", "rate_ft_trip")
STAGE_2B_RATE_SKILLS = {
    "rate_assist": "passing_creation",
    "rate_oreb": "offensive_rebounding",
    "rate_dreb": "defensive_rebounding",
    "rate_stl": "steal_disruption",
    "rate_blk": "block_rim_protection",
    "rate_tov": "turnover_avoidance",
}

ATTEMPT_RATE_SQL = """
SELECT
    player_id, season,
    SUM(minutes) AS total_minutes,
    SUM(field_goals_attempted - three_point_field_goals_attempted) AS fg2a,
    SUM(three_point_field_goals_attempted) AS fg3a,
    SUM(free_throws_attempted) AS fta
FROM hoopr_player_game_logs
WHERE season = ANY(:seasons) AND player_id IS NOT NULL
GROUP BY player_id, season
"""


MIN_MINUTES_FOR_RATE_TARGET = 40.0  # roughly one game's worth -- see docstring


def build_attempt_rate_targets(engine: Engine, seasons: list[int]) -> pd.DataFrame:
    """Season-aggregated per-40 attempt-rate regression *targets* for Stage
    2A — real box-score totals, not the smoothed skill states (those are the
    *features*, built separately by `build_season_skill_states`).

    Real bug, found on the first real-data run (2026-06-24): the original
    version only `clip(lower=1e-6)`'d `total_minutes` before dividing —
    correct for avoiding a literal division by zero, but a player with a few
    seconds of garbage-time minutes and even one attempt produces an
    astronomical per-40 rate (1 attempt / 1e-6 minutes * 40 ≈ 40 million),
    which dominates the Ridge fit's residual variance entirely (observed
    `resid_std` ≈ 227,000 on real data — nonsensical). Fixed by dropping rows
    below `MIN_MINUTES_FOR_RATE_TARGET` outright, matching the Shrinkage
    Baseline's own `MIN_GAMES`-floor convention (drop low-sample rows rather
    than compute a garbage rate for them), instead of letting a near-zero
    denominator through.
    """
    with engine.connect() as conn:
        totals = pd.read_sql(text(ATTEMPT_RATE_SQL), conn, params={"seasons": seasons})
    return _compute_attempt_rates(totals)


def _compute_attempt_rates(totals: pd.DataFrame) -> pd.DataFrame:
    """Pure rate-computation step factored out of `build_attempt_rate_targets`
    so the minutes-floor fix is unit-testable without a DB connection."""
    totals = totals[totals["total_minutes"] >= MIN_MINUTES_FOR_RATE_TARGET].copy()
    minutes = totals["total_minutes"]
    totals["rate_2pa_attempted"] = totals["fg2a"] / minutes * 40.0
    totals["rate_3pa_attempted"] = totals["fg3a"] / minutes * 40.0
    totals["rate_ft_trip"] = totals["fta"] / minutes * 40.0
    return totals[["player_id", "season", "total_minutes", *STAGE_2A_TARGETS]]


def _stage_2a_design_matrix(df: pd.DataFrame) -> pd.DataFrame:
    """[skill_<feature>] + position dummies — same shape/convention as
    `build_design_matrix` above, but Stage 2A's smaller feature set (shooting
    + volume skills only, not the full SKILLS list)."""
    skill_cols = [f"skill_{s}" for s in STAGE_2A_FEATURE_SKILLS]
    pos_dummies = pd.get_dummies(df["position"], prefix="pos") if "position" in df.columns else pd.DataFrame(index=df.index)
    X = pd.concat([df[skill_cols].fillna(0.0), pos_dummies], axis=1)
    return X.reindex(columns=skill_cols + list(pos_dummies.columns), fill_value=0.0)


def fit_attempt_rate_models(
    states_df: pd.DataFrame, alpha: float = 5.0,
) -> dict[str, tuple[Pipeline, float]]:
    """Fits one weighted Ridge model per Stage 2A target. `states_df` must
    have `skill_<feature>` columns (Stage 2A's feature set), `position`, the
    3 target columns from `build_attempt_rate_targets`, and `total_minutes`
    (used as the sample weight — more minutes, more reliable a season's
    attempt-rate target is). Returns {target: (fitted_pipeline,
    residual_std)} — residual_std mirrors `fit_value_model`'s uncertainty
    convention."""
    models: dict[str, tuple[Pipeline, float]] = {}
    for target in STAGE_2A_TARGETS:
        train = states_df.dropna(subset=[target, *[f"skill_{s}" for s in STAGE_2A_FEATURE_SKILLS]])
        if len(train) < 30:
            continue
        X = _stage_2a_design_matrix(train)
        y = train[target].to_numpy(dtype=np.float64)
        weights = train["total_minutes"].clip(lower=1e-6).to_numpy(dtype=np.float64) if "total_minutes" in train else None
        model = Pipeline([("scale", StandardScaler()), ("ridge", Ridge(alpha=alpha))])
        fit_kwargs = {"ridge__sample_weight": weights} if weights is not None else {}
        model.fit(X, y, **fit_kwargs)
        resid_std = float(np.std(y - model.predict(X)))
        models[target] = (model, resid_std)
    return models


def project_rates(
    states_df: pd.DataFrame, attempt_models: dict[str, tuple[Pipeline, float]],
    pace: pd.Series | None = None,
) -> pd.DataFrame:
    """Combines Stage 2A's fitted attempt-rate models with the shooting-
    percentage skills (make/miss split) and Stage 2B's direct skill read-off
    into one per-40 + per-100 rate frame per (player_id, season).

    `pace` (optional): per-row team `adj_tempo` (possessions/40min) for a
    per-100-possession conversion (`per_100 = per_40 / (pace / 100)`).
    Without it, falls back to a fixed NCAA Division I average pace
    (~68 possessions/40min) — an approximation stated here, not hidden.
    """
    out = states_df[["player_id", "season"]].copy()
    X = _stage_2a_design_matrix(states_df)

    for target, (model, _resid_std) in attempt_models.items():
        out[target] = model.predict(X).clip(min=0.0)

    if "rate_2pa_attempted" in out.columns and "skill_shooting_2p_finishing" in states_df.columns:
        pct = states_df["skill_shooting_2p_finishing"].clip(0.0, 1.0)
        out["rate_2pa_make"] = out["rate_2pa_attempted"] * pct
        out["rate_2pa_miss"] = out["rate_2pa_attempted"] * (1.0 - pct)
    if "rate_3pa_attempted" in out.columns and "skill_shooting_3p" in states_df.columns:
        pct = states_df["skill_shooting_3p"].clip(0.0, 1.0)
        out["rate_3pa_make"] = out["rate_3pa_attempted"] * pct
        out["rate_3pa_miss"] = out["rate_3pa_attempted"] * (1.0 - pct)

    for rate_col, skill in STAGE_2B_RATE_SKILLS.items():
        skill_col = f"skill_{skill}"
        if skill_col in states_df.columns:
            out[rate_col] = states_df[skill_col].clip(lower=0.0)

    DEFAULT_PACE = 68.0
    pace_arr = pace.to_numpy(dtype=np.float64) if pace is not None else np.full(len(out), DEFAULT_PACE)
    pace_arr = np.where(np.isfinite(pace_arr) & (pace_arr > 0), pace_arr, DEFAULT_PACE)
    per40_cols = [c for c in out.columns if c not in ("player_id", "season")]
    for col in per40_cols:
        out[f"{col}_per100"] = out[col] / (pace_arr / 100.0)

    return out


# Gap F (Issue #37 reconciliation, 2026-06-24): writes real skill_states/
# uncertainty/projected_rates/projected_box_score for the Cross-Season model,
# instead of the Shrinkage Baseline's empty `{}` placeholders for those same
# fields. Writes to the same `player_projections` table under a *different*
# `model_version` — the table's partial unique index is on (player_id,
# season, model_version) WHERE school_id IS NULL, so these rows never
# collide with or overwrite the Shrinkage Baseline's. `upsert_neutral_
# projections` is fully generic (keyed off model_version, not the row
# content), so it's reused as-is here, not duplicated.
MODEL_VERSION_CROSS_SEASON = "player-projection-phase2a-v2"
MODEL_VERSION_CROSS_SEASON_FORECAST = "player-proj-phase2a-fcast-v1"
FORECAST_OFF_EXTRA_FEATURES = ["source_off_value_per_100", "source_value_per_100"]
FORECAST_DEF_EXTRA_FEATURES = ["source_def_value_per_100", "source_value_per_100"]


def forecast_next_season_states(
    cross_season_states: pd.DataFrame,
    covariates: pd.DataFrame,
    fitted_params: dict[str, dict],
) -> pd.DataFrame:
    """One-step-ahead neutral forecasts from observed season states.

    The same-season state rows answer "what did this player's observed
    season imply about his skill state?" Production player projection needs
    the next question: "given that observed state, what is the best
    estimate for the target season?" This applies the fitted season-grain
    transition equation once:

        alpha[t+1|t] = rho * alpha[t] + mu[t+1]

    `season` in the returned frame is the target/projected season, while
    `source_observed_season` records the season used to forecast it. Historical
    target seasons use known target-season transfer/level-change covariates
    when available; future rows without a known destination default to neutral
    no-transfer/no-level-change covariates.
    """
    source = cross_season_states.copy()
    source["source_observed_season"] = source["season"].astype(int)
    source["season"] = source["source_observed_season"] + 1

    observed_cov = covariates.rename(columns={
        "season": "source_observed_season",
        "career_season_index": "source_career_season_index",
    })
    target_cov = covariates.rename(columns={
        "career_season_index": "target_career_season_index",
        "transfer_flag": "target_transfer_flag",
        "level_change": "target_level_change",
    })
    out = (
        source
        .merge(
            observed_cov[["player_id", "source_observed_season", "source_career_season_index"]],
            on=["player_id", "source_observed_season"], how="left",
        )
        .merge(
            target_cov[["player_id", "season", "target_career_season_index", "target_transfer_flag", "target_level_change"]],
            on=["player_id", "season"], how="left",
        )
    )
    out["target_career_season_index"] = out["target_career_season_index"].fillna(out["source_career_season_index"] + 1)
    out["target_transfer_flag"] = out["target_transfer_flag"].fillna(0.0)
    out["target_level_change"] = out["target_level_change"].fillna(0.0)

    for skill in SKILLS:
        skill_col = f"skill_{skill}"
        var_col = f"skill_var_{skill}"
        if skill_col not in out.columns or var_col not in out.columns:
            continue
        params = fitted_params[skill]
        csi = out["target_career_season_index"].to_numpy(dtype=np.float64)
        transfer = out["target_transfer_flag"].to_numpy(dtype=np.float64)
        level = out["target_level_change"].to_numpy(dtype=np.float64)
        mu = (
            params["beta_0"]
            + params["beta_1"] * csi
            + params["beta_2"] * csi**2
            + params["beta_3"] * transfer
            + params["beta_4"] * level
        )
        rho = float(params["rho"])
        out[skill_col] = rho * out[skill_col].to_numpy(dtype=np.float64) + mu
        out[var_col] = rho * rho * out[var_col].clip(lower=0.0).to_numpy(dtype=np.float64) + float(params["Q"])

    drop_cols = [
        "source_career_season_index", "target_career_season_index",
        "target_transfer_flag", "target_level_change",
    ]
    return out.drop(columns=[c for c in drop_cols if c in out.columns])


def build_cross_season_records(
    projected_df: pd.DataFrame, projected_rates_df: pd.DataFrame | None = None,
    archetypes_df: pd.DataFrame | None = None, model_version: str = MODEL_VERSION_CROSS_SEASON,
) -> list[tuple]:
    """The Cross-Season model's analog of `build_neutral_records`.
    `projected_df` must be `project_value`'s output (value_per_100/CI/
    `_resid_std`) applied to a `skill_percentiles`'d Cross-Season state frame
    (`skill_<s>`/`skill_var_<s>`/`pctile_<s>` columns) — same shape the
    Shrinkage Baseline uses, just sourced from the season-grain states
    instead of single-season shrinkage. `projected_rates_df` is Gap C's
    `project_rates` output (optional — left-joined by (player_id, season));
    players below Gap C's `MIN_MINUTES_FOR_RATE_TARGET` floor get `{}` for
    `projected_box_score`/`projected_rates` specifically (same "real but
    incomplete, not a crash" convention the Shrinkage Baseline itself uses
    for fields it hasn't built yet), not dropped from the table.

    `projected_box_score` is a real per-40 derived summary (points, rebounds,
    assists, steals, blocks, turnovers) computed from Gap C's category rates
    — `pts_per_40` needs free-throw *makes*, not just the trip rate Gap C
    fits directly, so it's derived here as `rate_ft_trip * skill_free_throw_touch`.

    `archetypes_df` (Gap E, Issue #37 reconciliation): optional frame with
    `player_id`/`season`/`archetype_label`/`confidence` columns (same shape
    `join_archetype_metadata` below expects/produces) — when given, adds
    those two fields into `explanation` for players with a matched archetype
    row. Evaluation/explanation metadata *only*, per Issue #37's explicit
    constraint — never touches `skill_states`, the design matrix, or either
    value model. Missing for a player-season simply means no archetype keys
    are added, not a crash or a dropped row.
    """
    computed_at = datetime.now(timezone.utc)
    expires_at = computed_at + timedelta(days=EXPIRES_DAYS)

    # player_id is a 63-bit BigInteger (hash(barttorvik_id)) -- DataFrame.iterrows()
    # upcasts a whole row to a single dtype, and a *purely numeric* frame (player_id/
    # season plus only float columns, no string/object column to force `object` dtype)
    # upcasts to float64, silently losing precision on ids >= 2^53 (~9e15) -- ~99.9%
    # of real ids, since they're ~uniform over 63 bits. Confirmed real (2026-07-15):
    # this was the root cause of near-empty projected_box_score for every
    # player-proj-phase2a-fcast-v1 row in every season, including live production --
    # projected_rates_df (player_id/season + only float rate_ columns) hit this on
    # every row; projected_df/archetypes_df happened to survive by accident (each
    # carries a string column -- position, archetype_label -- that forces the row to
    # `object` dtype instead, which preserves the original Python int exactly). Fixed
    # everywhere here by reading player_id/season from the typed columns directly
    # instead of trusting whatever dtype iterrows() lands the row on.
    rates_lookup: dict[tuple[int, int], dict] = {}
    if projected_rates_df is not None:
        rate_cols = [c for c in projected_rates_df.columns if c not in ("player_id", "season")]
        rp_ids = projected_rates_df["player_id"].to_numpy(dtype=np.int64)
        rp_seasons = projected_rates_df["season"].to_numpy(dtype=np.int64)
        for i, (_, rr) in enumerate(projected_rates_df.iterrows()):
            rates_lookup[(int(rp_ids[i]), int(rp_seasons[i]))] = {
                c: round(float(rr[c]), 3) for c in rate_cols
            }

    archetype_lookup: dict[tuple[int, int], dict] = {}
    if archetypes_df is not None:
        ar_ids = archetypes_df["player_id"].to_numpy(dtype=np.int64)
        ar_seasons = archetypes_df["season"].to_numpy(dtype=np.int64)
        for i, (_, ar) in enumerate(archetypes_df.iterrows()):
            if pd.isna(ar.get("archetype_label")):
                continue
            archetype_lookup[(int(ar_ids[i]), int(ar_seasons[i]))] = {
                "archetype_label": ar["archetype_label"],
                "archetype_confidence": float(ar["confidence"]) if pd.notna(ar.get("confidence")) else None,
            }

    records: list[tuple] = []
    pd_ids = projected_df["player_id"].to_numpy(dtype=np.int64)
    pd_seasons = projected_df["season"].to_numpy(dtype=np.int64)
    for i, (_, r) in enumerate(projected_df.iterrows()):
        pid = int(pd_ids[i])
        season = int(pd_seasons[i])
        skill_states = {
            s: round(float(-r[f"skill_{s}"] if s in INVERTED_SKILLS else r[f"skill_{s}"]), 4)
            for s in SKILLS
        }
        skill_pcts = {s: float(r[f"pctile_{s}"]) for s in SKILLS}
        residual_std = float(r.get("_residual_std", r.get("_resid_std", 0.0)))
        value_std = float(r.get("_value_std", r.get("_resid_std", residual_std)))
        skill_state_value_std = float(r.get("_skill_state_value_std", 0.0))
        uncertainty = {
            "residual_std": round(residual_std, 3),
            "value_std": round(value_std, 3),
            "skill_state_value_std": round(skill_state_value_std, 3),
            "ci_scale": round(float(r.get("_ci_scale", 1.0)), 3),
            "skill_state_var": {
                s: round(float(r[f"skill_var_{s}"]), 4) for s in SKILLS if f"skill_var_{s}" in r.index
            },
        }
        explanation = {
            "source": "phase2a_next_season_forecast" if "source_observed_season" in r.index else "phase2a_season_grain_state_space",
            "skill_state_direction": {
                s: "higher_is_better" if s not in INVERTED_SKILLS else "stored_as_negative_rate_so_higher_is_better"
                for s in SKILLS
            },
            **archetype_lookup.get((pid, season), {}),
        }
        if "source_observed_season" in r.index and pd.notna(r["source_observed_season"]):
            explanation["source_observed_season"] = int(r["source_observed_season"])
            explanation["target_projected_season"] = season
            explanation["forecast_horizon_seasons"] = season - int(r["source_observed_season"])
        source_value_fields = [
            "source_value_per_100", "source_off_value_per_100", "source_def_value_per_100",
        ]
        source_values = {
            field: round(float(r[field]), 3)
            for field in source_value_fields
            if field in r.index and pd.notna(r[field])
        }
        if source_values:
            explanation["source_internal_value_prior"] = source_values
        if "_value_drivers" in r.index and isinstance(r["_value_drivers"], dict):
            explanation["value_components"] = {
                "off_value_per_100": round(float(r["off_value_per_100"]), 3),
                "raw_def_value_per_100": round(float(r["def_value_per_100"]), 3),
                "total_value_formula": TOTAL_VALUE_FORMULA,
            }
            explanation["value_drivers"] = r["_value_drivers"]

        rates = rates_lookup.get((pid, season), {})
        box_score: dict = {}
        if rates:
            ft_makes_per_40 = rates.get("rate_ft_trip", 0.0) * float(r.get("skill_free_throw_touch", 0.0))
            box_score = {
                "pts_per_40": round(
                    2 * rates.get("rate_2pa_make", 0.0) + 3 * rates.get("rate_3pa_make", 0.0) + ft_makes_per_40, 2,
                ),
                "reb_per_40": round(rates.get("rate_oreb", 0.0) + rates.get("rate_dreb", 0.0), 2),
                "ast_per_40": round(rates.get("rate_assist", 0.0), 2),
                "stl_per_40": round(rates.get("rate_stl", 0.0), 2),
                "blk_per_40": round(rates.get("rate_blk", 0.0), 2),
                "tov_per_40": round(rates.get("rate_tov", 0.0), 2),
            }

        records.append((
            pid, None, season, "neutral",
            round(float(r["value_per_100"]), 3),
            round(float(r["value_ci_lower"]), 3),
            round(float(r["value_ci_upper"]), 3),
            None, None,
            json.dumps(box_score), json.dumps(rates),
            json.dumps(skill_states), json.dumps(skill_pcts),
            json.dumps(uncertainty),
            json.dumps(explanation),
            model_version, computed_at, expires_at,
        ))
    return records


# =============================================================================
# Evaluation & Calibration
# =============================================================================
# The Shrinkage Baseline was in production with no held-out validation at
# all — `off_resid_std`/`def_resid_std` are computed in-sample (fit and
# evaluated on the same rows), and `SHRINKAGE_K`/`RIDGE_ALPHA` were never
# tuned, just hardcoded. This section provides the pieces to do this
# properly: rolling-origin temporal folds, hyperparameter tuning scoped to
# each fold's own validation season, real held-out regression metrics,
# calibration, baseline comparisons, and cohort slicing — see
# docs/models/player_projection_state_space_plan.md §12.
#
# Rolling-origin, not a random split: matches §12's own recommendation and
# the actual deployment scenario (production always predicts a season it
# has no labels for yet). Three folds, each train season block followed by
# one validation season (hyperparameter selection only) and one test season
# (final, single-use metric per fold):
#
#     Fold 1: train [2021,2022]           val 2023   test 2024
#     Fold 2: train [2021,2022,2023]      val 2024   test 2025
#     Fold 3: train [2021,2022,2023,2024] val 2025   test 2026  (headline fold)
#
# Known, accepted limitation: the same player can appear in both a fold's
# train and test seasons as different season-observations. Not eliminable
# without sacrificing most of the test set's size — standard for this kind
# of repeated-measures sports panel. Not a clean player-disjoint holdout.

FOLD_DEFS: list[dict[str, list[int]]] = [
    {"train": [2021, 2022], "val": [2023], "test": [2024]},
    {"train": [2021, 2022, 2023], "val": [2024], "test": [2025]},
    {"train": [2021, 2022, 2023, 2024], "val": [2025], "test": [2026]},
]

K_CANDIDATES = [2.0, 4.0, 8.0, 12.0, 16.0]
ALPHA_CANDIDATES = [0.1, 1.0, 5.0, 10.0, 20.0]

MIN_LABELED_ROWS = 10


def make_rolling_origin_folds(
    df: pd.DataFrame, fold_defs: list[dict[str, list[int]]] = FOLD_DEFS,
) -> list[dict]:
    """Splits df (must have a 'season' column) into the rolling-origin folds.
    Returns a list of dicts with 'train'/'val'/'test' sub-frames and the
    originating 'fold_def' for reporting."""
    folds = []
    for fold_def in fold_defs:
        folds.append({
            "train": df[df["season"].isin(fold_def["train"])].copy(),
            "val": df[df["season"].isin(fold_def["val"])].copy(),
            "test": df[df["season"].isin(fold_def["test"])].copy(),
            "fold_def": fold_def,
        })
    return folds


def compute_regression_metrics(y_true, y_pred) -> dict:
    """RMSE, R-squared, Spearman rank correlation — the three §12 calls for
    under 'Skill/rate metrics' (RMSE/MAE) and 'Rank correlation'."""
    y_true = np.asarray(y_true, dtype=np.float64)
    y_pred = np.asarray(y_pred, dtype=np.float64)
    n = len(y_true)
    resid = y_true - y_pred
    rmse = float(np.sqrt(np.mean(resid**2))) if n > 0 else float("nan")
    r2 = float(r2_score(y_true, y_pred)) if n > 1 else float("nan")
    if n > 1 and np.std(y_true) > 0 and np.std(y_pred) > 0:
        rho, _ = spearmanr(y_true, y_pred)
    else:
        rho = float("nan")
    return {"rmse": rmse, "r2": r2, "spearman": float(rho), "n": n}


def compute_calibration(y_true, ci_lower, ci_upper) -> float:
    """Empirical coverage rate — what fraction of true values fall inside
    the predicted [ci_lower, ci_upper] band. §12's 'Calibration' metric;
    compare against the nominal ~80% project_value's CI_Z=1.2816 targets."""
    y_true = np.asarray(y_true, dtype=np.float64)
    ci_lower = np.asarray(ci_lower, dtype=np.float64)
    ci_upper = np.asarray(ci_upper, dtype=np.float64)
    covered = (y_true >= ci_lower) & (y_true <= ci_upper)
    return float(covered.mean()) if len(covered) > 0 else float("nan")


def _fold_combined_val_rmse(
    train_df: pd.DataFrame, val_df: pd.DataFrame, k: float | None, alpha: float, skip_shrinkage: bool = False,
) -> dict | None:
    if skip_shrinkage:
        # Gap G (Issue #37 reconciliation, 2026-06-24): the Cross-Season
        # state frame already has skill_<x> columns (smoothed Kalman states)
        # and no games_played/min_pct columns at all -- shrink_skills()
        # would KeyError on it (the Shrinkage Baseline's raw-rate-only
        # sample_weight() needs games_played). There's nothing for shrinkage
        # to do here: the season-grain Kalman layer already *is* the
        # shrinkage-equivalent step for these states. Use the frames as-is;
        # k is not meaningful in this mode and is not grid-searched (see
        # tune_hyperparameters).
        shrunk_train, shrunk_val = train_df, val_df
    else:
        shrunk_train = shrink_skills(train_df, k=k)
        shrunk_val = shrink_skills(val_df, k=k)
    try:
        off_model, off_resid_std = fit_value_model(shrunk_train, "off_adj_rapm", alpha=alpha)
        def_model, def_resid_std = fit_value_model(shrunk_train, "def_adj_rapm", alpha=alpha)
    except ValueError:
        return None  # too few HE-labeled rows in this fold's train at this split

    projected_val = project_value(shrunk_val, off_model, def_model, off_resid_std, def_resid_std)
    labeled_val = projected_val.dropna(subset=list(VALUE_TARGETS))
    if len(labeled_val) < MIN_LABELED_ROWS:
        return None

    off_metrics = compute_regression_metrics(labeled_val["off_adj_rapm"], labeled_val["off_value_per_100"])
    def_metrics = compute_regression_metrics(labeled_val["def_adj_rapm"], labeled_val["def_value_per_100"])
    combined_rmse = float(np.sqrt(off_metrics["rmse"] ** 2 + def_metrics["rmse"] ** 2))
    return {
        "k": k, "alpha": alpha, "val_rmse": combined_rmse,
        "off_rmse": off_metrics["rmse"], "def_rmse": def_metrics["rmse"], "n_val_labeled": len(labeled_val),
    }


def tune_hyperparameters(
    train_df: pd.DataFrame, val_df: pd.DataFrame,
    k_candidates: list[float] = K_CANDIDATES, alpha_candidates: list[float] = ALPHA_CANDIDATES,
    skip_shrinkage: bool = False,
) -> tuple[float | None, float, pd.DataFrame]:
    """Grid search over (SHRINKAGE_K, RIDGE_ALPHA) on this fold's validation
    season only — selection criterion is the combined off/def target RMSE
    (sqrt(off_rmse^2 + def_rmse^2), the same two-target summary
    scripts/run_player_projection.py already uses for its MLflow promotion
    metric). Falls back to production's current defaults (SHRINKAGE_K,
    RIDGE_ALPHA) if no grid cell has enough labeled rows to fit at all (e.g.
    a very small early fold).

    `skip_shrinkage` (Gap G, 2026-06-24): set True for the Cross-Season
    state frame, which has no `games_played`/raw-rate columns for
    `shrink_skills` to act on — k is meaningless in this mode and is not
    grid-searched (only `alpha` is), and the returned `k` is `None`."""
    if skip_shrinkage:
        results = [
            result
            for alpha in alpha_candidates
            if (result := _fold_combined_val_rmse(train_df, val_df, None, alpha, skip_shrinkage=True)) is not None
        ]
        grid_df = pd.DataFrame(results)
        if grid_df.empty:
            return None, RIDGE_ALPHA, grid_df
        best = grid_df.loc[grid_df["val_rmse"].idxmin()]
        return None, float(best["alpha"]), grid_df

    results = [
        result
        for k in k_candidates
        for alpha in alpha_candidates
        if (result := _fold_combined_val_rmse(train_df, val_df, k, alpha)) is not None
    ]
    grid_df = pd.DataFrame(results)
    if grid_df.empty:
        return SHRINKAGE_K, RIDGE_ALPHA, grid_df
    best = grid_df.loc[grid_df["val_rmse"].idxmin()]
    return float(best["k"]), float(best["alpha"]), grid_df


def compare_to_baselines(train_df: pd.DataFrame, eval_df: pd.DataFrame, target_col: str) -> dict:
    """Two baselines, both fit on train only and evaluated on eval_df:
    (1) predict the train population's mean target value for everyone;
    (2) predict the train population's mean target value *within position*
    (HE pos_class) — a "prior-only" baseline that uses position but no
    skill features and no Ridge regression at all. If Ridge doesn't beat
    these, it isn't earning its complexity."""
    eval_labeled = eval_df.dropna(subset=[target_col])
    train_labeled = train_df.dropna(subset=[target_col])

    global_mean = float(train_labeled[target_col].mean())
    global_pred = np.full(len(eval_labeled), global_mean)
    global_metrics = compute_regression_metrics(eval_labeled[target_col], global_pred)

    position_means = train_labeled.groupby("position")[target_col].mean()
    position_pred = eval_labeled["position"].map(position_means).fillna(global_mean).to_numpy()
    position_metrics = compute_regression_metrics(eval_labeled[target_col], position_pred)

    return {"predict_train_mean": global_metrics, "predict_position_mean": position_metrics}


def evaluate_cohort_slices(
    df: pd.DataFrame, target_col: str, pred_col: str, slice_defs: dict[str, pd.Series], min_n: int = 5,
) -> pd.DataFrame:
    """One row per named boolean-mask slice in slice_defs, with RMSE/R²/
    Spearman/n. Slices with fewer than min_n labeled rows are skipped, not
    reported with misleadingly small-sample metrics."""
    rows = []
    for name, mask in slice_defs.items():
        sub = df[mask].dropna(subset=[target_col, pred_col])
        if len(sub) < min_n:
            continue
        metrics = compute_regression_metrics(sub[target_col], sub[pred_col])
        rows.append({"slice": name, **metrics})
    return pd.DataFrame(rows)


def join_archetype_metadata(df: pd.DataFrame, archetypes_df: pd.DataFrame) -> pd.DataFrame:
    """Left-joins `player_archetypes` (archetype_id/archetype_label/
    confidence) onto a projection/eval frame by (player_id, season) — Issue
    #37's Gap E (Issue #37 reconciliation): archetypes as evaluation/
    explanation/comparable-player metadata only.

    Deliberately a pure function taking `archetypes_df` as a plain frame, not
    an engine — the caller queries `player_archetypes` (e.g.
    `SELECT player_id, season, archetype_id, archetype_label, confidence
    FROM player_archetypes`), this function never touches the DB itself,
    same convention as the rest of this section.

    Left join, not inner: a player missing an archetype row must not be
    dropped or block evaluation — per Issue #37, "Missing archetype labels
    do not block projections for players with sufficient statistical
    history." Their archetype columns are simply NaN.
    """
    cols = ["player_id", "season", "archetype_id", "archetype_label", "confidence"]
    missing = [c for c in cols if c not in archetypes_df.columns]
    if missing:
        raise ValueError(f"archetypes_df missing expected columns: {missing}")
    return df.merge(archetypes_df[cols], on=["player_id", "season"], how="left")


def find_comparable_players(
    df: pd.DataFrame, player_id: int, season: int, skill_cols: list[str], n: int = 5,
) -> pd.DataFrame:
    """Nearest neighbors by Euclidean distance over `skill_cols` (the shared
    neutral skill-state representation — never archetype, even though
    archetype_label is reported alongside for context if present). Issue
    #37 Gap E: archetype is explanation metadata here, not part of the
    similarity metric itself — two players can be "comparable" by skill
    profile regardless of which archetype cluster they fell into.
    """
    target_row = df[(df["player_id"] == player_id) & (df["season"] == season)]
    if target_row.empty:
        raise ValueError(f"No row for player_id={player_id}, season={season}")
    target_vec = target_row[skill_cols].to_numpy(dtype=np.float64)[0]

    candidates = df[~((df["player_id"] == player_id) & (df["season"] == season))].dropna(subset=skill_cols).copy()
    diffs = candidates[skill_cols].to_numpy(dtype=np.float64) - target_vec
    candidates["_distance"] = np.sqrt((diffs**2).sum(axis=1))
    result_cols = ["player_id", "season", "_distance"] + [c for c in ("archetype_label",) if c in candidates.columns]
    return candidates.nsmallest(n, "_distance")[result_cols].reset_index(drop=True)
