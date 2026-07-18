"""Benchmark the pure all-pairs Scheme Fit and Gap Match scoring loops.

This intentionally excludes database reads/writes so it can be run safely and
repeatedly while measuring the CPU/JSON-serialization path changed by model
explainability work.

Usage:
  uv run python scripts/benchmark_fit_explainability.py --players 1000 --schools 50
"""
from __future__ import annotations

import argparse
import gc
import time

import numpy as np
import pandas as pd

from portalpoint.modeling import gap_matching as gm
from portalpoint.modeling import scheme_fit as sf


def _scheme_frames(
    players: int,
    schools: int,
    rng: np.random.Generator,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    season = 2026
    player_shots = rng.dirichlet(np.ones(3), size=players)
    team_shots = rng.dirichlet(np.ones(3), size=schools)
    player_df = pd.DataFrame(player_shots, columns=sf.PLAYER_SHOT_FEATS)
    player_df["player_id"] = np.arange(1, players + 1)
    player_df["season"] = season
    player_df["current_tempo"] = rng.uniform(62.0, 75.0, size=players)
    team_df = pd.DataFrame(team_shots, columns=sf.TEAM_SHOT_FEATS)
    team_df["school_id"] = np.arange(1, schools + 1)
    team_df["season"] = season
    team_df["adj_tempo"] = rng.uniform(62.0, 75.0, size=schools)
    for feature in sf.HE_FEATS:
        player_df[feature] = rng.uniform(0.0, 1.0, size=players)
        team_df[feature] = rng.uniform(0.0, 1.0, size=schools)
    team_df["_he_covered"] = True
    return player_df, team_df


def _gap_inputs(players: int, schools: int, rng: np.random.Generator) -> tuple:
    season = 2026
    df = pd.DataFrame(rng.normal(size=(players, len(gm.GAP_FEATURES))), columns=gm.GAP_FEATURES)
    df["player_id"] = np.arange(1, players + 1)
    df["season"] = season
    df["archetype_id"] = rng.integers(0, 8, size=players)
    df[gm.GAP_RELIABILITY_COL] = rng.uniform(0.5, 1.0, size=players)
    df[gm.POSITION_SOURCE_COL] = "benchmark"
    df[gm.POSITION_RELIABILITY_COL] = 1.0
    df[gm.SAMPLE_RELIABILITY_COL] = 1.0
    df[gm.FEATURE_RELIABILITY_COL] = 1.0
    weights = rng.dirichlet(np.ones(5), size=players)
    for index, column in enumerate(gm.POS_COLS):
        df[column] = weights[:, index]

    scaler = gm.fit_gap_scaler(df)
    gap_data = {season: {}}
    for school_id in range(1, schools + 1):
        gap_data[season][school_id] = {
            "gap_vecs": rng.normal(size=(5, len(gm.GAP_FEATURES))),
            "depth": rng.uniform(0.0, 5.0, size=5),
        }
    gap_scaled = gm.prescale_gap_tensors(gap_data, scaler, [season])
    deficits = {season: {school_id: set() for school_id in range(1, schools + 1)}}
    return df, scaler, gap_scaled, gap_data, deficits


def _timed(label: str, function, repeats: int) -> None:
    durations = []
    records = 0
    for _ in range(repeats):
        gc.collect()
        started = time.perf_counter()
        result = function()
        durations.append(time.perf_counter() - started)
        records = len(result[0] if isinstance(result, tuple) else result)
    best = min(durations)
    print(
        f"{label}: {records:,} pairs in {best:.3f}s "
        f"({records / best:,.0f} pairs/s best of {repeats})"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--players", type=int, default=1_000)
    parser.add_argument("--schools", type=int, default=50)
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    rng = np.random.default_rng(58)
    player_df, team_df = _scheme_frames(args.players, args.schools, rng)
    gap_args = _gap_inputs(args.players, args.schools, rng)
    season = 2026

    _timed(
        "scheme_fit",
        lambda: sf.score_all_seasons(player_df, team_df, [season], tempo_default=68.0),
        args.repeats,
    )
    _timed(
        "gap_match",
        lambda: gm.score_gap_matches(*gap_args, {}, [season]),
        args.repeats,
    )


if __name__ == "__main__":
    main()
