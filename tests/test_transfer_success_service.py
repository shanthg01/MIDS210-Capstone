from portalpoint.api.schemas.prediction import PredictionResponse
from portalpoint.api.services.transfer_success_service import map_similar_transfers, row_to_prediction
from portalpoint.db.models import TransferSuccessScore


def test_map_similar_transfers_parses_model_shape():
    raw = [{
        "player_name": "Jordan Hayes",
        "season": 2023,
        "success_label": True,
        "actual_value_per_100": 5.0,
        "projected_value_per_100": 3.0,
        "value_vs_projection": 2.0,
        "minutes_drift": 3.2,
        "usage_drift": 1.5,
        "post_minutes_per_game": 26.4,
        "projected_minutes": 22.1,
        "post_usage_rate": 24.0,
        "projected_usage": 20.5,
    }]
    comps = map_similar_transfers(raw)
    assert len(comps) == 1
    assert comps[0].player_name == "Jordan Hayes"
    assert comps[0].season == 2023
    assert comps[0].success_label is True


def test_map_similar_transfers_skips_invalid_rows():
    comps = map_similar_transfers([{"player_name": "Incomplete"}])
    assert comps == []


def test_row_to_prediction_maps_db_row():
    row = TransferSuccessScore(
        player_id=101,
        to_school_id=9900301,
        season=2027,
        success_probability=0.68,
        success_tier="Moderate",
        explanation="Test explanation",
        similar_transfers=[{
            "player_name": "Jordan Hayes",
            "season": 2023,
            "success_label": True,
            "actual_value_per_100": 5.0,
            "projected_value_per_100": 3.0,
            "value_vs_projection": 2.0,
        }],
        model_version="transfer-success-eb-v2",
        expires_at=None,  # type: ignore[arg-type]
    )
    response = row_to_prediction(row)
    assert isinstance(response, PredictionResponse)
    assert response.player_id == "101"
    assert response.school_id == 9900301
    assert response.success_probability == 0.68
    assert response.model_version == "transfer-success-eb-v2"
    assert len(response.similar_transfers) == 1
