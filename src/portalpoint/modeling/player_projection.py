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
     `_prod`/`_pred`-split fields the doc originally assumed). Hoop
     Explorer's defensive adjusted RAPM is lower-is-better; total value
     follows adj_rapm_margin = off_adj_rapm - def_adj_rapm.
  3. The fitted value model is applied to every player with season stats,
     not just the Hoop-Explorer-matched subset — that's what makes this a
     projection rather than a label lookup.

foul_discipline is absent from this module's `SKILLS` (Phase 0, season-grain)
because no foul-rate column exists in `player_season_stats` — `INVERTED_SKILLS`
includes it anyway since Phase 1/2 (game-grain, `player_projection_kalman.py`)
do have `hoopr_player_game_logs.fouls` and use this same sign convention
(2026-06-24).

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
import pickle
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sqlalchemy import Engine, text

from portalpoint.modeling.db_writers import upsert_with_season_replace

MODEL_VERSION = "player-projection-shrinkage-v2"
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
INVERTED_SKILLS = {"turnover_avoidance", "foul_discipline"}
SKILLS = list(SKILL_COLUMNS)

# Offense/defense feature-set split for the value-translation model
# (2026-06-24, user-initiated). off_adj_rapm is regressed on OFFENSE_SKILLS
# only, def_adj_rapm on DEFENSE_SKILLS only -- position dummies are the only
# "shared" feature (informs both offensive and defensive role expectations),
# already handled separately from skill_cols in build_design_matrix.
# turnover_avoidance is classified Offense (a turnover is an offensive-
# possession event by definition, not a defensive one). foul_discipline is
# Phase 1/2-only (see INVERTED_SKILLS/module docstring) -- not present in
# Phase 0's SKILLS at all, so it can never appear in a Phase 0 design matrix
# regardless of this classification; the classification only matters once a
# Phase 2a frame (which does have skill_foul_discipline) is fit/projected.
OFFENSE_SKILLS = [
    "shooting_3p", "shooting_2p_finishing", "free_throw_touch", "shot_creation_usage",
    "passing_creation", "turnover_avoidance", "offensive_rebounding",
]
DEFENSE_SKILLS = ["defensive_rebounding", "steal_disruption", "block_rim_protection", "foul_discipline"]
VALUE_TARGETS = ("off_adj_rapm", "def_adj_rapm")
DEF_VALUE_TARGET_DIRECTION = "raw_hoop_explorer_lower_is_better"
TOTAL_VALUE_FORMULA = "off_value_per_100 - def_value_per_100"

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
    """Loads the Phase 0 player-season frame (all seasons at once —
    `shrink_skills` groups by season internally, so there's no need to
    query per-season). Shared by `run_player_projection.py`, the notebook,
    and `player_projection_phase2.py`'s Gap D prior-sourcing (Issue #37
    reconciliation) — single source of truth instead of three copies of the
    same SQL."""
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


def skill_percentiles(df: pd.DataFrame, season_col: str = "season", skills: list[str] = SKILLS) -> pd.DataFrame:
    """Within-season percentile rank (0-100) per shrunk skill. Percentile
    direction is flipped for turnover_avoidance (and any other
    `INVERTED_SKILLS` member) so 100 always means "better".

    `skills` defaults to Phase 0's `SKILLS` (10) for backward compatibility,
    but Phase 2a's `phase2_states` frame has 11 (`player_projection_kalman.SKILLS`
    includes `foul_discipline`, which Phase 0 structurally lacks) — callers
    building percentiles for a Phase 2a frame must pass `ppk.SKILLS` explicitly
    (real bug found 2026-06-24: this function silently used the hardcoded
    10-skill module constant regardless of what the input frame actually had,
    so a Phase 2a frame's `skill_foul_discipline` column was silently never
    percentiled — `build_phase2_records` then KeyError'd looking for
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
    skills: list[str] = SKILLS,
    extra_features: list[str] | None = None,
) -> pd.DataFrame:
    """`skills` defaults to `SKILLS` (Phase 0's full list) for backward
    compatibility. `fit_value_model`/`project_value` pass `OFFENSE_SKILLS`/
    `DEFENSE_SKILLS` explicitly (2026-06-24 offense/defense split) — any
    other caller (e.g. Gap C's `_stage_2a_design_matrix`, which has its own
    independent feature set) is unaffected.

    A requested skill whose `skill_<s>` column doesn't exist in `df` (e.g.
    `foul_discipline` — in `DEFENSE_SKILLS`, but absent from every Phase 0
    frame, which has no season-grain fouls column at all) is zero-padded via
    the `reindex` below rather than raising — Phase 0's def-model gets an
    always-0 `foul_discipline` feature (a real but harmless dead coefficient
    slot, no information, fits to ~0), Phase 2a's def-model gets the real
    column. Found as a real bug 2026-06-24: the original version selected
    `df[skill_cols]` directly, which `KeyError`'d on every Phase 0 call to
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
    real call site (Phase 0, Gap D refit, Gap G per-fold fit, the MLflow
    pyfunc wrapper all call fit_value_model/project_value per-target) --
    no caller-visible signature change needed for this split."""
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
    (fitted model, residual std) — residual std is the Phase 0 uncertainty
    proxy; real cross-validated intervals are Phase 1+ work.

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
    has no `skill_var_*` columns, this returns zeros and preserves Phase 0's
    prior constant-width behavior.
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
            for s in SKILLS
        }
        skill_pcts = {s: float(r[f"pctile_{s}"]) for s in SKILLS}
        explanation = {
            "prior_skill_estimate": {s: round(float(r[f"prior_{s}"]), 4) for s in SKILLS},
            "observed_performance_signal": {s: round(float(r[f"raw_{s}"]), 4) for s in SKILLS},
            "sample_size_weight": round(float(r["_weight"]), 2),
            "skill_state_direction": {
                s: "higher_is_better" if s not in INVERTED_SKILLS else "stored_as_negative_rate_so_higher_is_better"
                for s in SKILLS
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
