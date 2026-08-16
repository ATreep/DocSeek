from fastapi.testclient import TestClient

from backend.app.main import app


def test_health_is_public_and_does_not_expose_secrets():
    response = TestClient(app).get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "docseek-api"}
