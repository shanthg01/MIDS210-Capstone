def test_preferences_require_auth(client, user_id):
    assert client.get(f"/api/users/{user_id}/preferences").status_code == 401


def test_get_preferences_returns_200(client, user_id, H):
    assert client.get(f"/api/users/{user_id}/preferences", headers=H).status_code == 200


def test_preferences_schema(client, user_id, H):
    data = client.get(f"/api/users/{user_id}/preferences", headers=H).json()
    for field in ("importance_weights", "filters", "fit_weights"):
        assert field in data


def test_importance_weights_in_range(client, user_id, H):
    w = client.get(f"/api/users/{user_id}/preferences", headers=H).json()["importance_weights"]
    for key in ("scheme_fit", "role_fit", "gap_match", "program_fit"):
        assert 1 <= w[key] <= 10, f"importance_weights.{key} out of [1, 10]"


def test_fit_weights_sum_to_one(client, user_id, H):
    w = client.get(f"/api/users/{user_id}/preferences", headers=H).json()["fit_weights"]
    total = w["gap"] + w["scheme"] + w["role_fit"] + w["program_fit"]
    assert abs(total - 1.0) < 0.01


def test_update_preferences_require_auth(client, user_id):
    assert client.put(f"/api/users/{user_id}/preferences", json={}).status_code == 401


def test_update_preferences_merges_fields(client, user_id, H):
    r = client.put(
        f"/api/users/{user_id}/preferences",
        json={"importance_weights": {"scheme_fit": 10, "role_fit": 3, "gap_match": 7, "program_fit": 5}},
        headers=H,
    )
    assert r.status_code == 200
    w = r.json()["importance_weights"]
    assert w["scheme_fit"] == 10
    assert w["role_fit"] == 3


def test_update_preferences_rejects_weight_out_of_range(client, user_id, H):
    r = client.put(
        f"/api/users/{user_id}/preferences",
        json={"importance_weights": {"scheme_fit": 11, "role_fit": 5, "gap_match": 5, "program_fit": 5}},
        headers=H,
    )
    assert r.status_code == 422


def test_update_fit_weights(client, user_id, H):
    r = client.put(
        f"/api/users/{user_id}/preferences",
        json={"fit_weights": {"gap": 0.25, "scheme": 0.25, "role_fit": 0.25, "program_fit": 0.25}},
        headers=H,
    )
    assert r.status_code == 200
    w = r.json()["fit_weights"]
    assert w["gap"] == 0.25


def test_shortlist_requires_auth(client, user_id):
    assert client.get(f"/api/users/{user_id}/shortlist").status_code == 401


def test_get_shortlist_returns_200(client, user_id, H):
    assert client.get(f"/api/users/{user_id}/shortlist", headers=H).status_code == 200


def test_shortlist_schema(client, user_id, H):
    data = client.get(f"/api/users/{user_id}/shortlist", headers=H).json()
    assert "players" in data
    assert "total" in data
    assert data["total"] == len(data["players"])


def test_shortlist_items_shape(client, user_id, H):
    # Add an item first so the list is guaranteed non-empty
    client.post(f"/api/users/{user_id}/shortlist/1", headers=H)
    players = client.get(f"/api/users/{user_id}/shortlist", headers=H).json()["players"]
    assert len(players) > 0
    for p in players:
        assert "player_id" in p
        assert "player_name" in p
        assert "added_at" in p


def test_add_to_shortlist_requires_auth(client, user_id):
    assert client.post(f"/api/users/{user_id}/shortlist/305").status_code == 401


def test_add_to_shortlist_returns_201(client, user_id, H):
    # Use player_id=2 — guaranteed to exist in real DB (2500+ portal players loaded)
    r = client.post(f"/api/users/{user_id}/shortlist/2", headers=H)
    assert r.status_code in (201, 409)  # 201 first time, 409 if re-run
    if r.status_code == 201:
        data = r.json()
        assert data["player_id"] == 2
        assert "added_at" in data


def test_remove_from_shortlist_requires_auth(client, user_id):
    assert client.delete(f"/api/users/{user_id}/shortlist/1").status_code == 401


def test_remove_from_shortlist_returns_204(client, user_id, H):
    # Ensure item exists before deleting
    client.post(f"/api/users/{user_id}/shortlist/3", headers=H)
    assert client.delete(f"/api/users/{user_id}/shortlist/3", headers=H).status_code == 204


_PROFILE_BODY = {
    "name": "Wing search",
    "fit_weights": {"gap": 0.1, "scheme": 0.5, "role_fit": 0.2, "program_fit": 0.2},
    "importance_weights": {"scheme_fit": 9, "role_fit": 4, "gap_match": 3, "program_fit": 5},
    "filters": {
        "recruiting_regions": [], "conferences": [], "positions": ["SF"],
        "target_archetypes": [], "nil_budget_min": None, "nil_budget_max": None, "min_stats": None,
    },
}


def test_preference_profiles_require_auth(client, user_id):
    assert client.get(f"/api/users/{user_id}/preference-profiles").status_code == 401


def test_create_and_list_preference_profile(client, user_id, H):
    r = client.post(f"/api/users/{user_id}/preference-profiles", json=_PROFILE_BODY, headers=H)
    assert r.status_code == 201
    created = r.json()
    assert created["name"] == "Wing search"
    assert created["fit_weights"]["scheme"] == 0.5

    listed = client.get(f"/api/users/{user_id}/preference-profiles", headers=H).json()["profiles"]
    assert any(p["id"] == created["id"] for p in listed)

    client.delete(f"/api/users/{user_id}/preference-profiles/{created['id']}", headers=H)


def test_create_preference_profile_duplicate_name_conflicts(client, user_id, H):
    body = {**_PROFILE_BODY, "name": "Duplicate name test"}
    first = client.post(f"/api/users/{user_id}/preference-profiles", json=body, headers=H)
    assert first.status_code == 201
    second = client.post(f"/api/users/{user_id}/preference-profiles", json=body, headers=H)
    assert second.status_code == 409
    client.delete(f"/api/users/{user_id}/preference-profiles/{first.json()['id']}", headers=H)


def test_activate_preference_profile_overwrites_active_preferences(client, user_id, H):
    created = client.post(
        f"/api/users/{user_id}/preference-profiles",
        json={**_PROFILE_BODY, "name": "Activate test"},
        headers=H,
    ).json()

    r = client.post(f"/api/users/{user_id}/preference-profiles/{created['id']}/activate", headers=H)
    assert r.status_code == 200
    active = r.json()
    assert active["fit_weights"]["scheme"] == 0.5
    assert active["importance_weights"]["scheme_fit"] == 9

    # Confirms activation persisted to the single UserPreference row, not just the response.
    refetched = client.get(f"/api/users/{user_id}/preferences", headers=H).json()
    assert refetched["fit_weights"]["scheme"] == 0.5

    client.delete(f"/api/users/{user_id}/preference-profiles/{created['id']}", headers=H)


def test_delete_preference_profile_returns_404_when_missing(client, user_id, H):
    r = client.delete(f"/api/users/{user_id}/preference-profiles/999999", headers=H)
    assert r.status_code == 404
