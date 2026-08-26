import pytest
from fastapi.testclient import TestClient

from app.discovery import DiscoveryBoundary
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def isolated_data_directory(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CLOUDSTERR_DATA_DIR", str(tmp_path))


def registration_payload(**overrides) -> dict:
    payload = {
        "name": "Authorized Example",
        "base_url": "https://example.com",
        "environment": "Staging",
        "owner": "Example Site Team",
        "description": "Registration test only",
        "allowed_path": "/public",
        "excluded_paths": ["/admin", "/checkout"],
        "authorization_confirmed": True,
    }
    payload.update(overrides)
    return payload


def test_health_endpoint() -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": "0.3.0"}


def test_register_and_list_site_without_running_it() -> None:
    created = client.post("/api/sites", json=registration_payload())
    assert created.status_code == 201
    assert created.json()["status"] == "BASELINE REQUIRED"
    assert created.json()["allowed_path"] == "/public"
    assert created.json()["excluded_paths"] == ["/admin", "/checkout"]
    assert created.json()["last_check"] is None

    listed = client.get("/api/sites")
    assert listed.status_code == 200
    assert len(listed.json()["sites"]) == 1
    assert listed.json()["sites"][0]["base_url"] == "https://example.com"


def test_registration_requires_explicit_authorization() -> None:
    response = client.post(
        "/api/sites",
        json=registration_payload(authorization_confirmed=False),
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "Explicit authorization confirmation is required."


def test_registration_rejects_invalid_boundary_path() -> None:
    response = client.post("/api/sites", json=registration_payload(allowed_path="admin"))
    assert response.status_code == 422


@pytest.mark.parametrize(
    "base_url",
    [
        "https://user:password@example.com",
        "https://example.com/private",
        "https://example.com?token=secret",
        "https://example.com#section",
    ],
)
def test_registration_rejects_unsafe_base_url_content(base_url: str) -> None:
    response = client.post("/api/sites", json=registration_payload(base_url=base_url))
    assert response.status_code == 422


def test_registration_rejects_duplicate_base_url() -> None:
    first = client.post("/api/sites", json=registration_payload())
    second = client.post("/api/sites", json=registration_payload(name="Duplicate"))
    assert first.status_code == 201
    assert second.status_code == 409


def test_boundary_blocks_external_and_excluded_documents() -> None:
    boundary = DiscoveryBoundary(
        base_url="https://example.com",
        allowed_path="/public",
        excluded_paths=("/public/private",),
    )
    assert boundary.permits_document("https://example.com/public")
    assert boundary.permits_document("https://example.com/public/about")
    assert not boundary.permits_document("https://example.com/admin")
    assert not boundary.permits_document("https://example.com/public/private/report")
    assert not boundary.permits_document("https://other.example/public")


def test_discovery_records_inventory_without_form_execution(monkeypatch) -> None:
    created = client.post(
        "/api/sites",
        json=registration_payload(base_url="http://127.0.0.1:8127", allowed_path="/demo-site"),
    )
    site_id = created.json()["id"]

    async def fake_discover(boundary):
        assert boundary.allowed_path == "/demo-site"
        return [
            {
                "url": "http://127.0.0.1:8127/demo-site",
                "title": "Demo",
                "status_code": 200,
                "links": ["/demo-site/about"],
                "buttons": ["Demonstration button"],
                "forms": [{"action": "/demo-site/search", "method": "POST", "fields": ["query"]}],
            }
        ]

    monkeypatch.setattr("app.main.discover", fake_discover)
    response = client.post(f"/api/sites/{site_id}/discover")

    assert response.status_code == 200
    assert response.json()["status"] == "COMPLETED"
    assert response.json()["page_count"] == 1
    listed = client.get(f"/api/sites/{site_id}/discoveries")
    assert listed.json()["runs"][0]["pages"][0]["forms"][0]["method"] == "POST"


def test_dashboard_is_served() -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "Register a site" in response.text
    assert "does not start discovery or monitoring" in response.text
