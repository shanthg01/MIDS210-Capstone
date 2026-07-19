import json

import pandas as pd
import pytest

from portalpoint.modeling import gap_matching as gm


def test_score_gap_matches_scores_all_pairs_and_preserves_scheme_context():
    season = 2026
    rows = []
    for player_id, offset in [(1, 0.0), (2, 1.0)]:
        row = {
            "player_id": player_id,
            "season": season,
            "archetype_id": player_id,
            gm.GAP_RELIABILITY_COL: 0.5,
            gm.POSITION_SOURCE_COL: "hoop_explorer",
            gm.POSITION_RELIABILITY_COL: 1.0,
            gm.SAMPLE_RELIABILITY_COL: 1.0,
            gm.FEATURE_RELIABILITY_COL: 1.0,
        }
        for i, feature in enumerate(gm.GAP_FEATURES):
            row[feature] = float(i + 1) + offset
        for pos_col in gm.POS_COLS:
            row[pos_col] = 0.0
        row["pos_confidence_pg"] = 1.0
        rows.append(row)
    df = pd.DataFrame(rows)

    scaler = gm.fit_gap_scaler(df)
    gap_data = {season: {}}
    for school_id, multiplier in [(10, 1.0), (20, 2.0)]:
        gap_vecs = pd.DataFrame(0.0, index=range(5), columns=gm.GAP_FEATURES).to_numpy()
        gap_vecs[0, 0] = multiplier
        gap_vecs[0, 1] = multiplier / 2.0
        gap_data[season][school_id] = {"gap_vecs": gap_vecs, "depth": [1.0, 2.0, 3.0, 4.0, 5.0]}
    gap_scaled = gm.prescale_gap_tensors(gap_data, scaler, [season])
    arch_deficit = {season: {10: {1}, 20: set()}}
    existing = {
        (1, 10, season): {
            "scheme_fit": 80.0,
            "breakdown": {"scheme": {"source": "seeded"}},
        }
    }

    records = gm.score_gap_matches(df, scaler, gap_scaled, gap_data, arch_deficit, existing, [season])

    assert {(r[0], r[1], r[2]) for r in records} == {
        (1, 10, season),
        (1, 20, season),
        (2, 10, season),
        (2, 20, season),
    }

    seeded = next(r for r in records if r[:3] == (1, 10, season))
    seeded_breakdown = json.loads(seeded[5])
    assert seeded_breakdown["scheme"] == {"source": "seeded"}
    assert seeded_breakdown["gap"]["archetype_needed"] is True
    assert seeded[4] >= 49.0
    gap_bd = seeded_breakdown["gap"]
    assert gap_bd["reliability_baseline_contribution"] == pytest.approx(7.5)
    raw_sum = sum(item["contribution"] for item in gap_bd["cosine_contributions"])
    assert raw_sum + gap_bd["raw_score_adjustment"] == pytest.approx(
        gap_bd["raw_gap_match"], abs=2e-6
    )
    calibrated_sum = sum(
        item["calibrated_contribution"] for item in gap_bd["cosine_contributions"]
    )
    assert (
        calibrated_sum
        + gap_bd["reliability_baseline_contribution"]
        + gap_bd["calibrated_score_adjustment"]
    ) == pytest.approx(gap_bd["calibrated_gap_match"], abs=2e-6)

    unseeded = next(r for r in records if r[:3] == (1, 20, season))
    unseeded_breakdown = json.loads(unseeded[5])
    assert "scheme" not in unseeded_breakdown
    assert "gap" in unseeded_breakdown


def test_delete_stale_gap_scores_deletes_only_prior_gap_versions():
    class Cursor:
        rowcount = 7

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, sql, params):
            self.sql = sql
            self.params = params

    class Connection:
        def __init__(self):
            self.cursor_obj = Cursor()
            self.committed = False
            self.closed = False

        def cursor(self):
            return self.cursor_obj

        def commit(self):
            self.committed = True

        def close(self):
            self.closed = True

    class Engine:
        def __init__(self):
            self.conn = Connection()

        def raw_connection(self):
            return self.conn

    engine = Engine()

    deleted = gm.delete_stale_gap_scores(engine, [2021, 2022], "gap-cos-v4")

    assert deleted == 7
    assert "model_version LIKE 'gap-cos-%%'" in engine.conn.cursor_obj.sql
    assert engine.conn.cursor_obj.params == ([2021, 2022], "gap-cos-v4")
    assert engine.conn.committed is True
    assert engine.conn.closed is True
