from __future__ import annotations

import pandas as pd
import pytest
from pydantic import ValidationError

from portalpoint.api.schemas.projection import TeamRatingComparisonRequest
from portalpoint.api.services.context_staleness import context_staleness_payload
from portalpoint.modeling import player_clustering, team_clustering


def test_context_staleness_lists_every_affected_model() -> None:
    result = context_staleness_payload(True, "coaching_change")

    assert result.is_stale is True
    assert result.reason == "coaching_change"
    assert result.affected_models == [
        "scheme_fit",
        "gap_match",
        "team_rating_projection",
        "playing_time",
    ]


def test_team_rating_comparison_requires_distinct_players() -> None:
    with pytest.raises(ValidationError, match="distinct"):
        TeamRatingComparisonRequest(player_ids=[1, 1], school_id=10)


def test_player_cluster_records_persist_explanation() -> None:
    frame = pd.DataFrame([{
        "player_id": 1,
        "season": 2027,
        "cluster_id": 2,
        "archetype_label": "Skilled Stretch Forward",
        "confidence": 0.7,
        "archetype_memberships": [{"cluster_id": 2, "score": 0.7}],
        "cluster_explanation": {"version": 1, "method": "centroid_separation"},
    }])

    record = player_clustering.build_archetype_records(frame, "m1-v1")[0]

    assert record[6].adapted["method"] == "centroid_separation"
    assert "explanation" in player_clustering.UPSERT_SQL


def test_team_cluster_records_persist_explanation_and_clear_staleness() -> None:
    row = {
        "school_id": 10,
        "season": 2027,
        "cluster_id": 1,
        "system_label": "Rim Pressure Offense / Controlled Half-Court Defense",
        "offense_cluster_id": 1,
        "defense_cluster_id": 4,
        "offense_memberships": [],
        "defense_memberships": [],
        "system_memberships": [],
        "cluster_explanation": {"version": 1, "method": "centroid_separation"},
        **{feature: 0.25 for feature in team_clustering.BART_FEATURES},
    }

    record = team_clustering.build_team_profile_records(pd.DataFrame([row]), "m2-v1")[0]

    assert record[10].adapted["method"] == "centroid_separation"
    assert "stale_flag = false" in team_clustering.UPSERT_SQL
