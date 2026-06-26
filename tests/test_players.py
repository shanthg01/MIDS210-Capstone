def test_get_player_is_public(client):
    r = client.get("/api/players/101")
    assert r.status_code == 200


def test_get_player_response_shape(client):
    data = client.get("/api/players/101").json()
    for field in (
        "player_id",
        "full_name",
        "position",
        "class_year",
        "current_school",
        "current_school_id",
    ):
        assert field in data, f"missing field: {field}"


def test_get_player_stats_present(client):
    stats = client.get("/api/players/101").json()["current_season_stats"]
    assert stats is not None
    assert stats["games_played"] > 0
    assert stats["minutes_per_game"] > 0
    # advanced stats may be 0.0 if not available in source data for this player
    assert stats["per"] >= 0
    assert 0 <= stats["true_shooting_pct"] < 1
    assert 0 <= stats["usage_rate"] < 100


def test_get_player_stat_rates_sum_to_one(client):
    stats = client.get("/api/players/101").json()["current_season_stats"]
    total = stats["three_point_rate"] + stats["rim_rate"] + stats["mid_range_rate"]
    # three components of shot distribution should each be [0,1] and their sum ≤ 1
    assert total <= 1.05


def test_get_player_archetype_present(client):
    arch = client.get("/api/players/101").json()["archetype"]
    assert arch is not None
    assert "label" in arch
    assert 0 <= arch["confidence"] <= 1


def test_get_player_stats_deterministic(client):
    r1 = client.get("/api/players/42")
    r2 = client.get("/api/players/42")
    assert r1.json()["current_season_stats"] == r2.json()["current_season_stats"]


def test_different_player_ids_produce_different_stats(client):
    s1 = client.get("/api/players/1").json()["current_season_stats"]
    s2 = client.get("/api/players/2").json()["current_season_stats"]
    assert s1 != s2


def test_search_is_public(client):
    assert client.get("/api/players/search?name=Marcus").status_code == 200


def test_search_returns_matching_results(client):
    data = client.get("/api/players/search?name=Marcus").json()
    assert data["query"] == "Marcus"
    assert isinstance(data["results"], list)
    assert data["total"] == len(data["results"])
    for player in data["results"]:
        assert "marcus" in player["full_name"].lower()


def test_search_case_insensitive(client):
    upper = client.get("/api/players/search?name=MARCUS").json()["total"]
    lower = client.get("/api/players/search?name=marcus").json()["total"]
    assert upper == lower


def test_search_no_results_for_unknown_name(client):
    data = client.get("/api/players/search?name=ZZZUnknownXXX").json()
    assert data["total"] == 0
    assert data["results"] == []


def test_search_rejects_single_char(client):
    assert client.get("/api/players/search?name=M").status_code == 422


def test_search_requires_name_param(client):
    assert client.get("/api/players/search").status_code == 422


def test_search_available_only_param_accepted(client):
    assert client.get("/api/players/search?name=Marcus&available_only=true").status_code == 200


def test_search_available_only_filters_to_subset(client):
    all_count = client.get("/api/players/search?name=an").json()["total"]
    available_count = client.get("/api/players/search?name=an&available_only=true").json()["total"]
    assert available_count <= all_count


def test_search_min_stat_accepted(client):
    assert client.get("/api/players/search?name=an&min_stat=usage_rate:0").status_code == 200


def test_search_min_stat_rejects_unknown_key(client):
    r = client.get("/api/players/search?name=an&min_stat=not_a_real_stat:20")
    assert r.status_code == 400


def test_search_min_stat_rejects_malformed_value(client):
    r = client.get("/api/players/search?name=an&min_stat=usage_rate:not_a_number")
    assert r.status_code == 400


def test_search_min_stat_filters_to_subset(client):
    all_count = client.get("/api/players/search?name=an").json()["total"]
    filtered_count = client.get("/api/players/search?name=an&min_stat=usage_rate:99").json()["total"]
    assert filtered_count <= all_count


def test_search_min_stat_multiple_ands_together(client):
    one_filter = client.get("/api/players/search?name=an&min_stat=usage_rate:20").json()["total"]
    two_filters = client.get(
        "/api/players/search?name=an&min_stat=usage_rate:20&min_stat=fg3_pct:40"
    ).json()["total"]
    assert two_filters <= one_filter


def test_claim_requires_auth(client):
    assert client.post("/api/players/101/claim", json={"player_id": 101}).status_code == 401


def test_claim_with_auth(client, H):
    r = client.post("/api/players/101/claim", json={"player_id": 101}, headers=H)
    assert r.status_code == 200
    data = r.json()
    assert data["success"] is True
    assert data["player_id"] == "101"
    assert "message" in data


def test_get_player_projection_is_public(client):
    assert client.get("/api/players/101/projection").status_code == 200


def test_get_player_projection_response_shape(client):
    data = client.get("/api/players/101/projection").json()
    assert data["player_id"] == "101"
    assert data["projection_mode"] == "neutral"
    assert isinstance(data["value_per_100"], float)
    assert data["model_version"] == "player-proj-phase2a-fcast-v1"
    assert isinstance(data["projected_box_score"], dict)
    assert isinstance(data["projected_rates"], dict)
    assert isinstance(data["skill_states"], dict)
    assert isinstance(data["skill_percentiles"], dict)
    for pctile in data["skill_percentiles"].values():
        assert 0 <= pctile <= 100


def test_get_player_projection_defaults_to_latest_season(client):
    data = client.get("/api/players/101/projection").json()
    explicit = client.get(f"/api/players/101/projection?season={data['season']}").json()
    assert explicit["season"] == data["season"]
    assert explicit["value_per_100"] == data["value_per_100"]


def test_get_player_projection_not_found_for_unprojected_player(client):
    # Out of range of real ingested ids (~27k players) — see PLAYER_ID in conftest.py.
    r = client.get("/api/players/9900001/projection")
    assert r.status_code == 404


def test_get_player_projection_not_found_for_unprojected_season(client):
    r = client.get("/api/players/101/projection?season=1999")
    assert r.status_code == 404
