from fastapi.testclient import TestClient

from evaluation_api.main import app


def test_health() -> None:
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["service"] == "medical-evaluation-api"
