def test_requires_auth(client):
    assert client.get("/api/recommendations?user_id=1001").status_code == 401


def test_returns_200(client, H):
    assert client.get("/api/recommendations?user_id=1001", headers=H).status_code == 200


def test_missing_user_id_returns_422(client, H):
    assert client.get("/api/recommendations", headers=H).status_code == 422


def test_response_schema(client, H):
    data = client.get("/api/recommendations?user_id=1001", headers=H).json()
    for field in ("program_id", "recommendations", "total", "generated_at", "model_version"):
        assert field in data
    assert data["total"] == len(data["recommendations"])


def test_returns_ten_items(client, H):
    items = client.get("/api/recommendations?user_id=1001", headers=H).json()["recommendations"]
    assert len(items) == 10


def test_ranks_are_sequential(client, H):
    items = client.get("/api/recommendations?user_id=1001", headers=H).json()["recommendations"]
    assert [i["rank"] for i in items] == list(range(1, 11))


def test_sorted_by_overall_fit_descending(client, H):
    fits = [
        i["overall_fit"]
        for i in client.get("/api/recommendations?user_id=1001", headers=H).json()["recommendations"]
    ]
    assert fits == sorted(fits, reverse=True)


def test_item_schema(client, H):
    item = client.get("/api/recommendations?user_id=1001", headers=H).json()["recommendations"][0]
    for field in ("player_id", "player_name", "position", "overall_fit", "components", "reasoning"):
        assert field in item


def test_component_scores_in_range(client, H):
    items = client.get("/api/recommendations?user_id=1001", headers=H).json()["recommendations"]
    for item in items:
        assert 0 <= item["overall_fit"] <= 100
        for key in ("gap_match", "scheme_fit", "role_fit", "program_fit"):
            assert 0 <= item["components"][key] <= 100, f"{key} out of range in rank {item['rank']}"


def test_reasoning_nonempty(client, H):
    items = client.get("/api/recommendations?user_id=1001", headers=H).json()["recommendations"]
    for item in items:
        assert len(item["reasoning"]) > 0
