import pytest


def test_requires_auth(client):
    r = client.post("/api/compare", json={"player_id": 101, "school_ids": [301, 302]})
    assert r.status_code == 401


def test_returns_200(client, H):
    r = client.post("/api/compare", json={"player_id": 101, "school_ids": [301, 302]}, headers=H)
    assert r.status_code == 200


def test_response_schema(client, H):
    data = client.post("/api/compare", json={"player_id": 101, "school_ids": [301, 302]}, headers=H).json()
    for field in ("player_id", "schools", "comparison_matrix", "trade_offs", "generated_at"):
        assert field in data


def test_school_count_matches_input(client, H):
    for count in (2, 3, 4):
        school_ids = list(range(301, 301 + count))
        data = client.post("/api/compare", json={"player_id": 101, "school_ids": school_ids}, headers=H).json()
        assert len(data["schools"]) == count, f"expected {count} schools"


def test_matrix_has_all_components(client, H):
    matrix = client.post("/api/compare", json={"player_id": 101, "school_ids": [301, 302]}, headers=H).json()["comparison_matrix"]
    for component in ("overall_fit", "gap_match", "scheme_fit", "opportunity", "personal_fit"):
        assert component in matrix


def test_matrix_keys_match_school_names(client, H):
    data = client.post("/api/compare", json={"player_id": 101, "school_ids": [301, 302]}, headers=H).json()
    school_names = {s["school"]["name"] for s in data["schools"]}
    matrix_keys = set(data["comparison_matrix"]["overall_fit"].keys())
    assert school_names == matrix_keys


def test_matrix_scores_in_range(client, H):
    matrix = client.post("/api/compare", json={"player_id": 101, "school_ids": [301, 302]}, headers=H).json()["comparison_matrix"]
    for component, scores in matrix.items():
        for school, score in scores.items():
            assert 0 <= score <= 100, f"{component}[{school}]={score} out of range"


def test_trade_offs_present(client, H):
    trade_offs = client.post("/api/compare", json={"player_id": 101, "school_ids": [301, 302]}, headers=H).json()["trade_offs"]
    assert len(trade_offs) > 0
    for to in trade_offs:
        assert "factor" in to
        assert "description" in to
        assert "best_school_name" in to
        assert "best_school_id" in to


def test_trade_off_winners_in_school_list(client, H):
    data = client.post("/api/compare", json={"player_id": 101, "school_ids": [301, 302]}, headers=H).json()
    school_ids = {s["school"]["school_id"] for s in data["schools"]}
    for to in data["trade_offs"]:
        assert to["best_school_id"] in school_ids, f"winner {to['best_school_id']} not in input schools"


def test_each_entry_has_fit_score_and_prediction(client, H):
    entries = client.post("/api/compare", json={"player_id": 101, "school_ids": [301, 302]}, headers=H).json()["schools"]
    for entry in entries:
        assert "school" in entry
        assert "fit_score" in entry
        assert "prediction" in entry
        assert 0 <= entry["fit_score"]["overall_fit"] <= 100


def test_rejects_single_school(client, H):
    r = client.post("/api/compare", json={"player_id": 101, "school_ids": [301]}, headers=H)
    assert r.status_code == 422


def test_rejects_five_schools(client, H):
    r = client.post("/api/compare", json={"player_id": 101, "school_ids": [301, 302, 303, 304, 305]}, headers=H)
    assert r.status_code == 422


def test_rejects_missing_player_id(client, H):
    r = client.post("/api/compare", json={"school_ids": [301, 302]}, headers=H)
    assert r.status_code == 422


@pytest.mark.parametrize("school_ids", [[301, 302], [303, 304, 305], [301, 302, 303, 304]])
def test_valid_input_sizes(client, H, school_ids):
    r = client.post("/api/compare", json={"player_id": 101, "school_ids": school_ids}, headers=H)
    assert r.status_code == 200
    assert len(r.json()["schools"]) == len(school_ids)
