from fastapi.testclient import TestClient

from app.main import create_app


def test_health_is_ok() -> None:
    response = TestClient(create_app()).get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_openapi_document_is_generated() -> None:
    schema = TestClient(create_app()).get("/openapi.json").json()
    assert "/api/v1/health" in schema["paths"]
