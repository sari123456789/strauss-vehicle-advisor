"""M1: route registration and middleware of the shared app, without a database (no lifespan)."""

from fastapi.testclient import TestClient

from app.main import create_app
from app.settings import Settings

ORIGIN = "https://strauss-vehicle-advisor.vercel.app"


def test_business_and_conversation_routes_are_registered_once() -> None:
    paths = TestClient(create_app(Settings())).get("/openapi.json").json()["paths"]
    for path in (
        "/api/v1/health/ready",
        "/api/v1/auth/login",
        "/api/v1/me/profile",
        "/api/v1/cases/{caseId}/comparisons",
        "/api/v1/conversations",
        "/api/v1/conversations/{conversationId}/messages",
    ):
        assert path in paths, path
    operations = [
        (method, path) for path, item in paths.items() for method in item if method != "parameters"
    ]
    assert len(operations) == len(set(operations))


def test_ready_is_503_problem_details_before_the_runtime_is_composed() -> None:
    response = TestClient(create_app(Settings())).get("/api/v1/health/ready")
    assert response.status_code == 503
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == "INFRA_UNAVAILABLE"


def test_routes_answer_503_not_configured_without_the_runtime() -> None:
    client = TestClient(create_app(Settings()))
    response = client.get("/api/v1/me/profile", headers={"Authorization": "Bearer x"})
    assert response.status_code == 503
    assert response.json()["code"] == "INFRA_UNAVAILABLE"
    assert "Traceback" not in response.text


def test_cors_allows_only_the_configured_origin_with_credentials() -> None:
    app = create_app(Settings(auth_allowed_origins=[ORIGIN]))
    client = TestClient(app)
    preflight = client.options(
        "/api/v1/auth/refresh",
        headers={
            "Origin": ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "X-CSRF-Protection",
        },
    )
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == ORIGIN
    assert preflight.headers["access-control-allow-credentials"] == "true"
    foreign = client.options(
        "/api/v1/auth/refresh",
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"},
    )
    assert "access-control-allow-origin" not in foreign.headers


def test_no_cors_middleware_without_configured_origins() -> None:
    client = TestClient(create_app(Settings(auth_allowed_origins=[])))
    response = client.options(
        "/api/v1/auth/login",
        headers={"Origin": ORIGIN, "Access-Control-Request-Method": "POST"},
    )
    assert "access-control-allow-origin" not in response.headers
