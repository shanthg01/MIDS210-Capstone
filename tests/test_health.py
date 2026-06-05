def test_health_returns_200(client):
    r = client.get("/health")
    assert r.status_code == 200


def test_health_body(client):
    data = client.get("/health").json()
    assert data["status"] == "ok"
    assert "environment" in data
