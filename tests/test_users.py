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
    for key in ("playing_time", "nil", "academics", "location"):
        assert 1 <= w[key] <= 10, f"importance_weights.{key} out of [1, 10]"


def test_fit_weights_sum_to_one(client, user_id, H):
    w = client.get(f"/api/users/{user_id}/preferences", headers=H).json()["fit_weights"]
    total = w["gap"] + w["scheme"] + w["opportunity"] + w["personal"]
    assert abs(total - 1.0) < 0.01


def test_update_preferences_require_auth(client, user_id):
    assert client.put(f"/api/users/{user_id}/preferences", json={}).status_code == 401


def test_update_preferences_merges_fields(client, user_id, H):
    r = client.put(
        f"/api/users/{user_id}/preferences",
        json={"importance_weights": {"playing_time": 10, "nil": 3, "academics": 7, "location": 5}},
        headers=H,
    )
    assert r.status_code == 200
    w = r.json()["importance_weights"]
    assert w["playing_time"] == 10
    assert w["nil"] == 3


def test_update_preferences_rejects_weight_out_of_range(client, user_id, H):
    r = client.put(
        f"/api/users/{user_id}/preferences",
        json={"importance_weights": {"playing_time": 11, "nil": 5, "academics": 5, "location": 5}},
        headers=H,
    )
    assert r.status_code == 422


def test_update_fit_weights(client, user_id, H):
    r = client.put(
        f"/api/users/{user_id}/preferences",
        json={"fit_weights": {"gap": 0.25, "scheme": 0.25, "opportunity": 0.25, "personal": 0.25}},
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
    assert "schools" in data
    assert "total" in data
    assert data["total"] == len(data["schools"])


def test_shortlist_items_shape(client, user_id, H):
    # Add an item first so the list is guaranteed non-empty
    client.post(f"/api/users/{user_id}/shortlist/1", headers=H)
    schools = client.get(f"/api/users/{user_id}/shortlist", headers=H).json()["schools"]
    assert len(schools) > 0
    for s in schools:
        assert "school_id" in s
        assert "school_name" in s
        assert "added_at" in s


def test_add_to_shortlist_requires_auth(client, user_id):
    assert client.post(f"/api/users/{user_id}/shortlist/305").status_code == 401


def test_add_to_shortlist_returns_201(client, user_id, H):
    # Use school_id=2 — guaranteed to exist in real DB (364 schools loaded)
    r = client.post(f"/api/users/{user_id}/shortlist/2", headers=H)
    assert r.status_code in (201, 409)  # 201 first time, 409 if re-run
    if r.status_code == 201:
        data = r.json()
        assert data["school_id"] == 2
        assert "added_at" in data


def test_remove_from_shortlist_requires_auth(client, user_id):
    assert client.delete(f"/api/users/{user_id}/shortlist/1").status_code == 401


def test_remove_from_shortlist_returns_204(client, user_id, H):
    # Ensure item exists before deleting
    client.post(f"/api/users/{user_id}/shortlist/3", headers=H)
    assert client.delete(f"/api/users/{user_id}/shortlist/3", headers=H).status_code == 204
