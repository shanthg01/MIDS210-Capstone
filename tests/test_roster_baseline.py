import pandas as pd

from portalpoint.modeling import roster_baseline as rb


def test_historical_members_use_next_season_roster_outlook():
    stats = pd.DataFrame([
        {"player_id": 1, "school_id": 10, "season": 2025, "usage_rate": 20.0},
        {"player_id": 2, "school_id": 20, "season": 2025, "usage_rate": 18.0},
        {"player_id": 3, "school_id": 30, "season": 2025, "usage_rate": 16.0},
        {"player_id": 1, "school_id": 10, "season": 2026, "usage_rate": 21.0},
        {"player_id": 2, "school_id": 10, "season": 2026, "usage_rate": 19.0},
    ])

    members = rb.build_historical_members(stats, [2025, 2026])

    observed = set(
        members[["player_id", "baseline_school_id", "season", "baseline_status"]].itertuples(
            index=False,
            name=None,
        )
    )
    assert observed == {
        (1, 10, 2025, rb.BASELINE_STATUS_RETURNING),
        (2, 10, 2025, rb.BASELINE_STATUS_CHANGED_SCHOOL),
    }


def test_apply_members_to_features_reassigns_transfer_in_to_target_school():
    stats = pd.DataFrame([
        {"player_id": 2, "school_id": 20, "season": 2025, "usage_rate": 18.0},
    ])
    members = pd.DataFrame([
        {
            "player_id": 2,
            "baseline_school_id": 10,
            "season": 2025,
            "baseline_status": rb.BASELINE_STATUS_CHANGED_SCHOOL,
        }
    ])

    baseline = rb.apply_members_to_features(stats, members)

    assert len(baseline) == 1
    assert int(baseline.iloc[0]["school_id"]) == 10
    assert int(baseline.iloc[0]["source_school_id"]) == 20
    assert baseline.iloc[0]["usage_rate"] == 18.0


def test_prior_fallback_subtracts_explicit_departures():
    stats = pd.DataFrame([
        {"player_id": 1, "school_id": 10, "season": 2026},
        {"player_id": 2, "school_id": 10, "season": 2026},
        {"player_id": 3, "school_id": 20, "season": 2026},
    ])

    members = rb.build_prior_fallback_members(
        stats,
        season=2026,
        exclude_school_ids={20},
        departed_pairs={(2, 10)},
    )

    observed = set(
        members[["player_id", "baseline_school_id", "season", "baseline_status"]].itertuples(
            index=False,
            name=None,
        )
    )
    assert observed == {
        (1, 10, 2026, rb.BASELINE_STATUS_PRIOR_FALLBACK),
    }


def test_snapshot_members_drop_freshman_transfer_in_name_collision():
    members = pd.DataFrame([
        {
            "player_id": 1,
            "baseline_school_id": 10,
            "season": 2026,
            "returning_status": "transfer_in",
            "class_year": "Fr",
        },
        {
            "player_id": 2,
            "baseline_school_id": 10,
            "season": 2026,
            "returning_status": "transfer_in",
            "class_year": "Jr",
        },
        {
            "player_id": 3,
            "baseline_school_id": 10,
            "season": 2026,
            "returning_status": "returning",
            "class_year": "Fr",
        },
    ])

    filtered = rb.filter_snapshot_members(members)

    assert set(filtered["player_id"]) == {2, 3}


def test_suspicious_snapshot_feature_match_predicate():
    assert rb.is_suspicious_snapshot_feature_match("transfer_in", "Fr") is True
    assert rb.is_suspicious_snapshot_feature_match("transfer_in", "Jr") is False
    assert rb.is_suspicious_snapshot_feature_match("returning", "Fr") is False
