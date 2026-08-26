from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_health_endpoint() -> None:
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": "0.1.0"}


def test_sites_endpoint_returns_safe_demonstration_record() -> None:
    response = client.get("/api/sites")

    assert response.status_code == 200
    site = response.json()["sites"][0]
    assert site["status"] == "BASELINE REQUIRED"
    assert site["environment"] == "Demo only"
    assert "url" not in site


def test_dashboard_is_served() -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert "Know whether your website actually works" in response.text
    assert "not visit, test, or transmit" in response.text
