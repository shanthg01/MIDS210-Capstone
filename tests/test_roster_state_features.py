import pandas as pd

from portalpoint.modeling import roster_state_features as rsf


def test_safe_bigint_series_preserves_precision_on_large_ids():
    # 63-bit BigInteger player_ids (see db/player_ids.py) exceed float64's
    # 52-bit mantissa — pd.DataFrame(rows, columns=...) would silently corrupt
    # these if the column also contains None (real bug, confirmed live 2026-07-14).
    big_ids = [9113337278589210007, 8230803996430582001, 2907721130448780123]
    values = [big_ids[0], None, big_ids[1], big_ids[2]]

    result = rsf.safe_bigint_series(values)

    assert result.dtype == "Int64"
    assert result.isna().tolist() == [False, True, False, False]
    non_null = result.dropna().tolist()
    assert non_null == big_ids
    for v in non_null:
        assert isinstance(v, int)


def test_safe_bigint_series_upcast_would_have_corrupted_these_values():
    # Documents the actual failure mode this helper fixes: naive float64
    # upcasting loses precision on values this large.
    big_id = 9113337278589210007
    naive_df = pd.DataFrame({"player_id": [big_id, None]})
    assert naive_df["player_id"].dtype == "float64"
    assert int(naive_df["player_id"].iloc[0]) != big_id  # precision lost

    fixed = rsf.safe_bigint_series([big_id, None])
    assert fixed.dropna().tolist() == [big_id]  # precision preserved


def test_impact_col_is_bpm_not_per():
    # player_season_stats.per is hardcoded None in ingest_barttorvik.py (never
    # populated by any source) — confirmed live 2026-07-14: 0/27,050 non-null
    # rows across all seasons. bpm is the real, populated advanced-impact field.
    assert rsf.IMPACT_COL == "bpm"
