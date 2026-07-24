import pytest

# Pinned to the season scripts/seed_test_data.py's player_team_fit_scores rows
# use (2026) rather than relying on get_current_season()'s global MAX across the
# whole table — a shared dev DB with real ingested data at a later season would
# otherwise resolve to a season the test school has no seeded rows for.
SEASON = 2026


def test_requires_auth(client):
    assert client.get("/api/recommendations?user_id=1001").status_code == 401


def test_returns_200(client, H, user_id):
    assert (
        client.get(f"/api/recommendations?user_id={user_id}&season={SEASON}", headers=H).status_code
        == 200
    )


def test_missing_user_id_returns_422(client, H):
    assert client.get("/api/recommendations", headers=H).status_code == 422


def test_response_schema(client, H, user_id):
    data = client.get(f"/api/recommendations?user_id={user_id}&season={SEASON}", headers=H).json()
    for field in ("program_id", "recommendations", "total", "generated_at", "model_version"):
        assert field in data
    assert data["total"] == len(data["recommendations"])


def test_returns_ten_items(client, H, user_id):
    items = client.get(f"/api/recommendations?user_id={user_id}&season={SEASON}", headers=H).json()[
        "recommendations"
    ]
    assert len(items) == 10


def test_ranks_are_sequential(client, H, user_id):
    items = client.get(f"/api/recommendations?user_id={user_id}&season={SEASON}", headers=H).json()[
        "recommendations"
    ]
    assert [i["rank"] for i in items] == list(range(1, 11))


def test_sorted_by_personalized_fit_descending(client, H, user_id):
    fits = [
        i["personalized_fit"]
        for i in client.get(
            f"/api/recommendations?user_id={user_id}&season={SEASON}", headers=H
        ).json()["recommendations"]
    ]
    assert fits == sorted(fits, reverse=True)


def test_item_schema(client, H, user_id):
    item = client.get(f"/api/recommendations?user_id={user_id}&season={SEASON}", headers=H).json()[
        "recommendations"
    ][0]
    for field in (
        "player_id",
        "player_name",
        "position",
        "overall_fit",
        "personalized_fit",
        "components",
        "reasoning",
        "is_portal_candidate",
    ):
        assert field in item


def test_is_portal_candidate_always_true(client, H, user_id):
    # CANDIDATE_SQL already filters WHERE ptf.is_portal_candidate = true, so
    # every returned item should carry that through as true.
    items = client.get(f"/api/recommendations?user_id={user_id}&season={SEASON}", headers=H).json()[
        "recommendations"
    ]
    for item in items:
        assert item["is_portal_candidate"] is True


def test_component_scores_in_range(client, H, user_id):
    items = client.get(f"/api/recommendations?user_id={user_id}&season={SEASON}", headers=H).json()[
        "recommendations"
    ]
    for item in items:
        assert 0 <= item["overall_fit"] <= 100
        for key in ("gap_match", "scheme_fit", "role_fit", "team_impact_fit"):
            assert 0 <= item["components"][key] <= 100, f"{key} out of range in rank {item['rank']}"


def test_reasoning_nonempty(client, H, user_id):
    items = client.get(f"/api/recommendations?user_id={user_id}&season={SEASON}", headers=H).json()[
        "recommendations"
    ]
    for item in items:
        assert len(item["reasoning"]) > 0


def test_other_users_recommendations_forbidden(client, H, user_id):
    assert client.get(f"/api/recommendations?user_id={user_id + 1}", headers=H).status_code == 403


def test_selected_recommendation_explanation_matches_live_ranking(client, H, user_id):
    recommendations = client.get(
        f"/api/recommendations?user_id={user_id}&season={SEASON}", headers=H
    ).json()["recommendations"]
    selected = recommendations[0]

    response = client.get(
        "/api/recommendations/explanation",
        params={"user_id": user_id, "player_id": selected["player_id"], "season": SEASON},
        headers=H,
    )

    assert response.status_code == 200
    explanation = response.json()["explanation"]
    assert explanation["selected"] is True
    assert explanation["final_rank"] == selected["rank"]
    assert explanation["personalized_fit"] == pytest.approx(selected["personalized_fit"])
    assert explanation["risk_tolerance"] == "medium"
