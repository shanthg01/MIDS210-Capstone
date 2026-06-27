import pytest

VALID_ROLES = {"starter", "rotation", "bench", "reserve"}


def test_requires_auth(client):
    assert client.get("/api/predictions?player_id=101&school_id=9900301").status_code == 401


def test_returns_200(client, H):
    assert client.get("/api/predictions?player_id=101&school_id=9900301", headers=H).status_code == 200


def test_requires_both_params(client, H):
    assert client.get("/api/predictions?player_id=101", headers=H).status_code == 422


def test_schema(client, H):
    data = client.get("/api/predictions?player_id=101&school_id=9900301", headers=H).json()
    for field in (
        "player_id", "school_id", "predicted_per_change", "predicted_minutes",
        "predicted_role", "confidence", "similar_transfers", "model_version",
    ):
        assert field in data, f"missing field: {field}"


def test_confidence_in_range(client, H):
    confidence = client.get("/api/predictions?player_id=101&school_id=9900301", headers=H).json()["confidence"]
    assert 0 <= confidence <= 1


def test_role_is_valid_enum(client, H):
    role = client.get("/api/predictions?player_id=101&school_id=9900301", headers=H).json()["predicted_role"]
    assert role in VALID_ROLES


def test_predicted_minutes_positive(client, H):
    assert client.get("/api/predictions?player_id=101&school_id=9900301", headers=H).json()["predicted_minutes"] > 0


def test_similar_transfers_nonempty(client, H):
    transfers = client.get("/api/predictions?player_id=101&school_id=9900301", headers=H).json()["similar_transfers"]
    assert len(transfers) > 0


def test_similar_transfers_shape(client, H):
    transfers = client.get("/api/predictions?player_id=101&school_id=9900301", headers=H).json()["similar_transfers"]
    for t in transfers:
        for field in ("player_name", "season", "from_school", "to_school", "per_before", "per_after", "per_change"):
            assert field in t, f"missing field {field} in similar transfer"
        assert t["per_change"] == pytest.approx(t["per_after"] - t["per_before"], abs=0.15)
        assert 0 <= t["outcome_score"] <= 5


def test_shap_explanations_present(client, H):
    shap = client.get("/api/predictions?player_id=101&school_id=9900301", headers=H).json()["shap_explanations"]
    assert isinstance(shap, list)
    assert len(shap) > 0
    for s in shap:
        assert "feature" in s
        assert "impact" in s
        assert "description" in s


def test_deterministic(client, H):
    r1 = client.get("/api/predictions?player_id=55&school_id=305", headers=H).json()
    r2 = client.get("/api/predictions?player_id=55&school_id=305", headers=H).json()
    assert r1["predicted_per_change"] == r2["predicted_per_change"]
    assert r1["predicted_role"] == r2["predicted_role"]
    assert r1["confidence"] == r2["confidence"]
