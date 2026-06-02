def test_preferences_require_auth(client):
    assert client.get("/api/users/1001/preferences").status_code == 401


def test_get_preferences_returns_200(client, H):
    assert client.get("/api/users/1001/preferences", headers=H).status_code == 200


def test_preferences_schema(client, H):
    data = client.get("/api/users/1001/preferences", headers=H).json()
    for field in ("importance_weights", "filters", "fit_weights"):
        assert field in data


def test_importance_weights_in_range(client, H):
    w = client.get("/api/users/1001/preferences", headers=H).json()["importance_weights"]
    for key in ("playing_time", "nil", "academics", "location"):
        assert 1 <= w[key] <= 10, f"importance_weights.{key} out of [1, 10]"


def test_fit_weights_sum_to_one(client, H):
    w = client.get("/api/users/1001/preferences", headers=H).json()["fit_weights"]
    total = w["gap"] + w["scheme"] + w["opportunity"] + w["personal"]
    assert abs(total - 1.0) < 0.01


def test_update_preferences_require_auth(client):
    assert client.put("/api/users/1001/preferences", json={}).status_code == 401


def test_update_preferences_merges_fields(client, H):
    r = client.put(
        "/api/users/1001/preferences",
        json={"importance_weights": {"playing_time": 10, "nil": 3, "academics": 7, "location": 5}},
        headers=H,
    )
    assert r.status_code == 200
    w = r.json()["importance_weights"]
    assert w["playing_time"] == 10
    assert w["nil"] == 3


def test_update_preferences_rejects_weight_out_of_range(client, H):
    r = client.put(
        "/api/users/1001/preferences",
        json={"importance_weights": {"playing_time": 11, "nil": 5, "academics": 5, "location": 5}},
        headers=H,
    )
    assert r.status_code == 422


def test_update_fit_weights(client, H):
    r = client.put(
        "/api/users/1001/preferences",
        json={"fit_weights": {"gap": 0.25, "scheme": 0.25, "opportunity": 0.25, "personal": 0.25}},
        headers=H,
    )
    assert r.status_code == 200
    w = r.json()["fit_weights"]
    assert w["gap"] == 0.25


def test_shortlist_requires_auth(client):
    assert client.get("/api/users/1001/shortlist").status_code == 401


def test_get_shortlist_returns_200(client, H):
    assert client.get("/api/users/1001/shortlist", headers=H).status_code == 200


def test_shortlist_schema(client, H):
    data = client.get("/api/users/1001/shortlist", headers=H).json()
    assert "schools" in data
    assert "total" in data
    assert data["total"] == len(data["schools"])


def test_shortlist_items_shape(client, H):
    schools = client.get("/api/users/1001/shortlist", headers=H).json()["schools"]
    assert len(schools) > 0
    for s in schools:
        assert "school_id" in s
        assert "school_name" in s
        assert "added_at" in s


def test_add_to_shortlist_requires_auth(client):
    assert client.post("/api/users/1001/shortlist/305").status_code == 401


def test_add_to_shortlist_returns_201(client, H):
    r = client.post("/api/users/1001/shortlist/305", headers=H)
    assert r.status_code == 201
    data = r.json()
    assert data["school_id"] == 305
    assert "added_at" in data


def test_remove_from_shortlist_requires_auth(client):
    assert client.delete("/api/users/1001/shortlist/301").status_code == 401


def test_remove_from_shortlist_returns_204(client, H):
    assert client.delete("/api/users/1001/shortlist/301", headers=H).status_code == 204
