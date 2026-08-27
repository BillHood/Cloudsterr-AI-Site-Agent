import asyncio
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.discovery import DiscoveryBoundary
from app.authentication import ApprovedLogin, classify_login_result, sanitize_control_inventory, sanitize_evidence, sanitize_response_candidates, sanitize_response_text, sanitized_request_evidence
from app.main import app, run_scheduled_fred_monitor

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
    assert response.json() == {"status": "ok", "version": "0.1.2"}


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

    approval = client.post(
        f"/api/sites/{site_id}/baselines",
        json={
            "discovery_run_id": response.json()["run_id"],
            "reviewer": "Authorized Reviewer",
            "approval_confirmed": True,
        },
    )
    assert approval.status_code == 201
    assert approval.json()["version"] == 1
    baselines = client.get(f"/api/sites/{site_id}/baselines")
    assert baselines.json()["baselines"][0]["pages"][0]["title"] == "Demo"

    execution = client.post(f"/api/sites/{site_id}/runs")
    assert execution.status_code == 200
    assert execution.json()["status"] == "PASS"
    assert execution.json()["passed"] == 1
    history = client.get(f"/api/sites/{site_id}/runs")
    assert history.json()["runs"][0]["status"] == "PASS"

    schedule = client.put(
        f"/api/sites/{site_id}/schedule",
        json={"frequency": "daily", "enabled": True, "approval_confirmed": True},
    )
    assert schedule.status_code == 200
    assert schedule.json()["enabled"] is True
    assert schedule.json()["next_run_at"] is not None


def test_baseline_requires_explicit_approval(monkeypatch) -> None:
    created = client.post("/api/sites", json=registration_payload())
    site_id = created.json()["id"]

    async def fake_discover(_boundary):
        return [{"url": "https://example.com", "title": "Example", "status_code": 200, "links": [], "buttons": [], "forms": []}]

    monkeypatch.setattr("app.main.discover", fake_discover)
    run = client.post(f"/api/sites/{site_id}/discover")
    response = client.post(
        f"/api/sites/{site_id}/baselines",
        json={"discovery_run_id": run.json()["run_id"], "reviewer": "Reviewer", "approval_confirmed": False},
    )
    assert response.status_code == 422


def test_run_requires_approved_baseline() -> None:
    created = client.post("/api/sites", json=registration_payload())
    response = client.post(f"/api/sites/{created.json()['id']}/runs")
    assert response.status_code == 409


def test_schedule_requires_approval_and_baseline() -> None:
    created = client.post("/api/sites", json=registration_payload())
    site_id = created.json()["id"]
    missing_approval = client.put(
        f"/api/sites/{site_id}/schedule",
        json={"frequency": "hourly", "enabled": True, "approval_confirmed": False},
    )
    assert missing_approval.status_code == 422
    missing_baseline = client.put(
        f"/api/sites/{site_id}/schedule",
        json={"frequency": "hourly", "enabled": True, "approval_confirmed": True},
    )
    assert missing_baseline.status_code == 409


def test_authentication_profile_stores_references_not_secrets(monkeypatch) -> None:
    created = client.post("/api/sites", json=registration_payload())
    site_id = created.json()["id"]
    response = client.put(
        f"/api/sites/{site_id}/authentication",
        json={
            "login_path": "/public/login",
            "username_env": "CLOUDSTERR_TEST_USERNAME",
            "password_env": "CLOUDSTERR_TEST_PASSWORD",
            "test_account_confirmed": True,
        },
    )
    assert response.status_code == 200
    assert response.json()["execution_enabled"] is False
    assert "password" not in response.json()
    stored = client.get(f"/api/sites/{site_id}/authentication")
    assert stored.json()["password_env"] == "CLOUDSTERR_TEST_PASSWORD"

    journey = client.put(
        f"/api/sites/{site_id}/login-journey",
        json={
            "username_selector": "#email",
            "password_selector": "#password",
            "submit_selector": "button[type='submit']",
            "success_path": "/public/dashboard",
            "success_text": "Welcome",
            "success_mode": "path_and_text",
            "external_auth_url": "https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword",
            "external_followup_url": "https://identitytoolkit.googleapis.com/v1/accounts:lookup",
            "authenticated_shell_check": True,
            "main_selector": "main",
            "heading_selector": "h1, h2",
            "navigation_selector": "nav",
            "inventory_navigation_selector": ".dashboard-nav-item-primary",
            "inventory_navigation_index": 2,
            "inventory_destination_path": "/public/fred",
            "firestore_listen_enabled": True,
            "firestore_listen_get_enabled": True,
            "revenuecat_subscriber_get_enabled": True,
            "approval_confirmed": True,
        },
    )
    assert journey.status_code == 200
    assert journey.json()["execution_enabled"] is False
    assert journey.json()["interaction_version"] == 1
    stored_journey = client.get(f"/api/sites/{site_id}/login-journey")
    assert stored_journey.json()["success_text"] == "Welcome"
    assert stored_journey.json()["success_mode"] == "path_and_text"
    assert stored_journey.json()["external_auth_url"] == "https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword"
    assert stored_journey.json()["external_followup_url"] == "https://identitytoolkit.googleapis.com/v1/accounts:lookup"
    assert stored_journey.json()["authenticated_shell_check"] is True
    assert stored_journey.json()["inventory_navigation_selector"] == ".dashboard-nav-item-primary"
    assert stored_journey.json()["inventory_navigation_index"] == 2
    assert stored_journey.json()["inventory_destination_path"] == "/public/fred"
    assert stored_journey.json()["firestore_listen_enabled"] is True
    assert stored_journey.json()["firestore_listen_get_enabled"] is True
    assert stored_journey.json()["revenuecat_subscriber_get_enabled"] is True

    monkeypatch.setenv("CLOUDSTERR_TEST_USERNAME", "test-user-value")
    monkeypatch.setenv("CLOUDSTERR_TEST_PASSWORD", "test-password-value")

    async def fake_login(approved, username, password):
        assert approved.success_path == "/public/dashboard"
        assert approved.success_mode == "path_and_text"
        assert approved.external_auth_url == "https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword"
        assert approved.external_followup_url == "https://identitytoolkit.googleapis.com/v1/accounts:lookup"
        assert approved.navigation_selector == "nav"
        assert username == "test-user-value"
        assert password == "test-password-value"
        return {"status": "PASS", "final_url": "https://example.com/public/dashboard", "path_matches": True, "text_matches": True, "submission_count": 1}

    monkeypatch.setattr("app.main.execute_approved_login", fake_login)
    login_test = client.post(f"/api/sites/{site_id}/login-test", json={"execution_confirmed": True})
    assert login_test.status_code == 200
    assert login_test.json()["status"] == "PASS"
    assert login_test.json()["interaction_version"] == 1
    assert "test-user-value" not in login_test.text
    assert "test-password-value" not in login_test.text
    interaction_history = client.get(f"/api/sites/{site_id}/interactions").json()
    assert interaction_history["interactions"][0]["linked_run_count"] == 1


def test_authentication_profile_rejects_secret_like_values_and_missing_confirmation() -> None:
    created = client.post("/api/sites", json=registration_payload())
    site_id = created.json()["id"]
    lowercase_value = client.put(
        f"/api/sites/{site_id}/authentication",
        json={"login_path": "/login", "username_env": "bill@example.com", "password_env": "secret", "test_account_confirmed": True},
    )
    assert lowercase_value.status_code == 422
    missing_confirmation = client.put(
        f"/api/sites/{site_id}/authentication",
        json={"login_path": "/login", "username_env": "TEST_USERNAME", "password_env": "TEST_PASSWORD", "test_account_confirmed": False},
    )
    assert missing_confirmation.status_code == 422


def test_login_journey_requires_authentication_profile_and_approval() -> None:
    created = client.post("/api/sites", json=registration_payload())
    site_id = created.json()["id"]
    payload = {
        "username_selector": "#email",
        "password_selector": "#password",
        "submit_selector": "button",
        "success_path": "/public/dashboard",
        "success_text": "Welcome",
        "approval_confirmed": True,
    }
    missing_profile = client.put(f"/api/sites/{site_id}/login-journey", json=payload)
    assert missing_profile.status_code == 409
    missing_execution_confirmation = client.post(f"/api/sites/{site_id}/login-test", json={"execution_confirmed": False})
    assert missing_execution_confirmation.status_code == 422


def test_dashboard_is_served() -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "Register a site" in response.text
    assert "AI Site Agent <span class=\"version\">v0.1.2</span>" in response.text
    assert "does not start discovery or monitoring" in response.text


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        ({"path_matches": True, "text_matches": True, "submission_used": True, "blocked_requests": [], "visible_errors": []}, ("PASS", "SUCCESS")),
        ({"path_matches": False, "text_matches": False, "submission_used": False, "blocked_requests": [{"method": "POST"}], "visible_errors": []}, ("FAIL", "EXTERNAL_AUTH_BLOCKED")),
        ({"path_matches": False, "text_matches": False, "submission_used": False, "blocked_requests": [], "visible_errors": ["Invalid email or password"]}, ("FAIL", "BAD_CREDENTIALS")),
        ({"path_matches": False, "text_matches": False, "submission_used": False, "blocked_requests": [], "visible_errors": ["Email is required"]}, ("FAIL", "VALIDATION_FAILED")),
        ({"path_matches": False, "text_matches": False, "submission_used": True, "blocked_requests": [], "visible_errors": []}, ("FAIL", "SUCCESS_EVIDENCE_MISMATCH")),
        ({"path_matches": False, "text_matches": False, "submission_used": True, "blocked_requests": [], "visible_errors": [], "auth_responses": [{"status": 400}]}, ("FAIL", "AUTH_REJECTED")),
        ({"path_matches": False, "text_matches": False, "submission_used": True, "blocked_requests": [{"method": "POST"}], "visible_errors": [], "auth_responses": [{"status": 200}]}, ("FAIL", "SUCCESS_EVIDENCE_MISMATCH")),
    ],
)
def test_login_result_classification(arguments: dict, expected: tuple[str, str]) -> None:
    assert classify_login_result(**arguments) == expected


def test_external_auth_boundary_allows_only_exact_https_endpoint() -> None:
    login = ApprovedLogin(
        base_url="https://example.com",
        allowed_path="/",
        excluded_paths=(),
        login_path="/login",
        username_selector="#email",
        password_selector="#password",
        submit_selector="button[type='submit']",
        success_path="/dashboard",
        success_text="Welcome",
        external_auth_url="https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword",
        external_followup_url="https://identitytoolkit.googleapis.com/v1/accounts:lookup",
        firestore_listen_enabled=True,
        revenuecat_subscriber_get_enabled=True,
    )
    assert login.permits_auth_submission(
        "https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key=redacted"
    )
    assert not login.permits_auth_submission("https://identitytoolkit.googleapis.com/v1/accounts:delete")
    assert login.permits_auth_submission("https://identitytoolkit.googleapis.com/v1/accounts:lookup?key=redacted")
    assert not login.permits_auth_submission("https://securetoken.googleapis.com/v1/token")
    assert not login.permits_auth_submission("http://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword")
    assert login.permits_firestore_listen("https://firestore.googleapis.com/google.firestore.v1.Firestore/Listen/channel?database=approved")
    assert not login.permits_firestore_listen("https://firestore.googleapis.com/google.firestore.v1.Firestore/Write/channel?database=approved")
    assert not login.permits_firestore_listen("http://firestore.googleapis.com/google.firestore.v1.Firestore/Listen/channel")
    assert not login.permits_firestore_listen("https://other.example/google.firestore.v1.Firestore/Listen/channel")
    assert login.permits_revenuecat_subscriber_get("https://api.revenuecat.com/v1/subscribers/customer-123")
    assert login.permits_revenuecat_subscriber_get("https://api.revenuecat.com/v1/subscribers/customer-123/offerings?platform=web")
    assert not login.permits_revenuecat_subscriber_get("https://api.revenuecat.com/v1/events")
    assert not login.permits_revenuecat_subscriber_get("https://api.revenuecat.com/v1/subscribers/customer-123/attributes")
    assert not login.permits_revenuecat_subscriber_get("https://e.revenue.cat/v1/events")
    assert login.success_path_matches("https://example.com/dashboard")
    assert not login.success_path_matches("https://example.com/login")
    assert not login.success_path_matches("https://example.com/dashboard/other")
    assert not login.success_path_matches("https://attacker.example/dashboard")


def test_exact_path_mode_allows_empty_success_text() -> None:
    created = client.post("/api/sites", json=registration_payload())
    site_id = created.json()["id"]
    client.put(
        f"/api/sites/{site_id}/authentication",
        json={"login_path": "/public/login", "username_env": "TEST_USERNAME", "password_env": "TEST_PASSWORD", "test_account_confirmed": True},
    )
    response = client.put(
        f"/api/sites/{site_id}/login-journey",
        json={
            "username_selector": "#email",
            "password_selector": "#password",
            "submit_selector": "button",
            "success_path": "/public/dashboard",
            "success_text": "",
            "success_mode": "exact_path",
            "approval_confirmed": True,
        },
    )
    assert response.status_code == 200
    assert response.json()["success_mode"] == "exact_path"


def test_authenticated_shell_check_requires_all_three_selectors() -> None:
    created = client.post("/api/sites", json=registration_payload())
    site_id = created.json()["id"]
    client.put(
        f"/api/sites/{site_id}/authentication",
        json={"login_path": "/public/login", "username_env": "TEST_USERNAME", "password_env": "TEST_PASSWORD", "test_account_confirmed": True},
    )
    response = client.put(
        f"/api/sites/{site_id}/login-journey",
        json={
            "username_selector": "#email",
            "password_selector": "#password",
            "submit_selector": "button",
            "success_path": "/public/dashboard",
            "success_text": "",
            "success_mode": "exact_path",
            "authenticated_shell_check": True,
            "main_selector": "main",
            "heading_selector": "h1, h2",
            "navigation_selector": "",
            "approval_confirmed": True,
        },
    )
    assert response.status_code == 422


def test_login_interactions_are_immutable_and_versioned() -> None:
    created = client.post("/api/sites", json=registration_payload())
    site_id = created.json()["id"]
    client.put(
        f"/api/sites/{site_id}/authentication",
        json={"login_path": "/public/login", "username_env": "TEST_USERNAME", "password_env": "TEST_PASSWORD", "test_account_confirmed": True},
    )
    payload = {
        "username_selector": "#email",
        "password_selector": "#password",
        "submit_selector": "button",
        "success_path": "/public/dashboard",
        "success_text": "",
        "success_mode": "exact_path",
        "approval_confirmed": True,
    }
    first = client.put(f"/api/sites/{site_id}/login-journey", json=payload)
    identical = client.put(f"/api/sites/{site_id}/login-journey", json=payload)
    changed = client.put(
        f"/api/sites/{site_id}/login-journey",
        json={**payload, "heading_selector": "h1", "main_selector": "main", "navigation_selector": "nav", "authenticated_shell_check": True},
    )
    assert first.json()["interaction_version"] == 1
    assert identical.json()["interaction_version"] == 1
    assert changed.json()["interaction_version"] == 2
    response_data = client.get(f"/api/sites/{site_id}/interactions").json()
    versions = response_data["interactions"]
    assert [item["version"] for item in versions] == [2, 1]
    assert versions[0]["supersedes_id"] == versions[1]["id"]
    assert versions[0]["linked_run_count"] == 0
    assert response_data["legacy_run_count"] == 0
    assert "definition" not in str(response_data)
    assert "#email" not in str(response_data)


def test_login_journey_rejects_broad_or_query_bearing_external_auth_url() -> None:
    created = client.post("/api/sites", json=registration_payload())
    site_id = created.json()["id"]
    client.put(
        f"/api/sites/{site_id}/authentication",
        json={"login_path": "/public/login", "username_env": "TEST_USERNAME", "password_env": "TEST_PASSWORD", "test_account_confirmed": True},
    )
    base_payload = {
        "username_selector": "#email",
        "password_selector": "#password",
        "submit_selector": "button",
        "success_path": "/public/dashboard",
        "success_text": "Welcome",
        "approval_confirmed": True,
    }
    assert client.put(f"/api/sites/{site_id}/login-journey", json={**base_payload, "external_auth_url": "https://identity.example"}).status_code == 422
    assert client.put(f"/api/sites/{site_id}/login-journey", json={**base_payload, "external_auth_url": "https://identity.example/v1/login?key=secret"}).status_code == 422
    assert client.put(
        f"/api/sites/{site_id}/login-journey",
        json={
            **base_payload,
            "external_auth_url": "https://identity.example/v1/login",
            "external_followup_url": "https://identity.example/v1/login",
        },
    ).status_code == 422


def test_inventory_navigation_requires_selector_and_bounded_destination() -> None:
    created = client.post("/api/sites", json=registration_payload())
    site_id = created.json()["id"]
    client.put(
        f"/api/sites/{site_id}/authentication",
        json={"login_path": "/public/login", "username_env": "TEST_USERNAME", "password_env": "TEST_PASSWORD", "test_account_confirmed": True},
    )
    base_payload = {
        "username_selector": "#email", "password_selector": "#password", "submit_selector": "button",
        "success_path": "/public/dashboard", "success_text": "", "success_mode": "exact_path", "approval_confirmed": True,
    }
    missing_selector = client.put(
        f"/api/sites/{site_id}/login-journey",
        json={**base_payload, "inventory_destination_path": "/public/fred"},
    )
    outside_boundary = client.put(
        f"/api/sites/{site_id}/login-journey",
        json={**base_payload, "inventory_navigation_selector": ".chat", "inventory_destination_path": "/dashboard/fred"},
    )
    assert missing_selector.status_code == 422
    assert outside_boundary.status_code == 422


def test_blocked_request_evidence_excludes_query_and_fragment() -> None:
    evidence = sanitized_request_evidence(
        "https://identity.example/v1/accounts:lookup?key=secret#fragment",
        "POST",
        "fetch",
    )
    assert evidence == {
        "method": "POST",
        "hostname": "identity.example",
        "path": "/v1/accounts:lookup",
        "resource_type": "fetch",
    }
    assert "secret" not in str(evidence)


def test_historical_evidence_redacts_identifier_like_path_segments() -> None:
    evidence = sanitize_evidence({
        "status": "FAIL",
        "blocked_requests": [{"method": "GET", "hostname": "api.example", "path": "/v1/users/LWP4rPN048gqcH3o1Lr7xROJrcs2"}],
        "auth_responses": [],
    })
    assert evidence["blocked_requests"][0]["path"] == "/v1/users/[REDACTED]"
    assert "LWP4rPN048gqcH3o1Lr7xROJrcs2" not in str(evidence)


def test_response_text_is_capped_and_sanitized() -> None:
    response = sanitize_response_text("READY account-LWP4rPN048gqcH3o1Lr7xROJrcs2 " + "x" * 400, ())
    assert response.startswith("READY [REDACTED]")
    assert len(response) <= 300


def test_response_candidate_inventory_excludes_text() -> None:
    candidates = sanitize_response_candidates([{"tag": "div", "classes": "fred-message assistant", "data_role": "bot", "text": "private reply"}])
    assert candidates == [{"tag": "div", "classes": "fred-message assistant", "data_role": "bot"}]
    assert "private reply" not in str(candidates)


def test_control_inventory_excludes_text_values_and_redacts_identifier_like_attributes() -> None:
    controls = sanitize_control_inventory([
        {
            "tag": "textarea",
            "id": "chat-input",
            "name": "prompt",
            "classes": "composer-input rounded",
            "disabled": "false",
            "visible": "true",
            "parent_tag": "form",
            "value": "private message",
            "placeholder": "Ask Fred anything",
            "text": "private response",
        },
        {"tag": "button", "id": "account-LWP4rPN048gqcH3o1Lr7xROJrcs2", "type": "submit"},
    ])
    assert controls[0] == {
        "tag": "textarea", "id": "chat-input", "name": "prompt",
        "classes": "composer-input rounded", "disabled": "false", "visible": "true",
        "parent_tag": "form",
    }
    assert controls[1]["id"] == "[REDACTED]"
    assert "private" not in str(controls)
    assert "Fred" not in str(controls)


def test_chat_inventory_requires_confirmation_and_submits_no_message(monkeypatch) -> None:
    created = client.post("/api/sites", json=registration_payload())
    site_id = created.json()["id"]
    client.put(
        f"/api/sites/{site_id}/authentication",
        json={"login_path": "/public/login", "username_env": "TEST_USERNAME", "password_env": "TEST_PASSWORD", "test_account_confirmed": True},
    )
    client.put(
        f"/api/sites/{site_id}/login-journey",
        json={
            "username_selector": "#email",
            "password_selector": "#password",
            "submit_selector": "button",
            "success_path": "/public/dashboard",
            "success_text": "",
            "success_mode": "exact_path",
            "approval_confirmed": True,
        },
    )
    assert client.post(f"/api/sites/{site_id}/chat-inventory", json={"execution_confirmed": False}).status_code == 422
    monkeypatch.setenv("TEST_USERNAME", "inventory-user")
    monkeypatch.setenv("TEST_PASSWORD", "inventory-password")

    async def fake_inventory(approved, username, password, collect_control_inventory=False):
        assert collect_control_inventory is True
        assert username == "inventory-user"
        assert password == "inventory-password"
        return {
            "status": "PASS",
            "outcome": "SUCCESS",
            "control_inventory": [{"tag": "textarea", "id": "chat-input"}],
            "visible_errors": ["must be removed"],
            "blocked_requests": [],
            "auth_responses": [],
        }

    monkeypatch.setattr("app.main.execute_approved_login", fake_inventory)
    result = client.post(f"/api/sites/{site_id}/chat-inventory", json={"execution_confirmed": True})
    assert result.status_code == 200
    assert result.json()["chat_message_submitted"] is False
    assert result.json()["page_text_captured"] is False
    assert result.json()["visible_errors"] == []
    stored = client.get(f"/api/sites/{site_id}/chat-inventories").json()["runs"]
    assert stored[0]["evidence"]["control_inventory"][0]["id"] == "chat-input"
    assert "inventory-password" not in str(stored)
    assert "must be removed" not in str(stored)


def test_fixed_chat_probe_is_exact_and_single_use(monkeypatch) -> None:
    created = client.post("/api/sites", json=registration_payload())
    site_id = created.json()["id"]
    client.put(
        f"/api/sites/{site_id}/authentication",
        json={"login_path": "/public/login", "username_env": "TEST_USERNAME", "password_env": "TEST_PASSWORD", "test_account_confirmed": True},
    )
    client.put(
        f"/api/sites/{site_id}/login-journey",
        json={
            "username_selector": "#email", "password_selector": "#password", "submit_selector": "button",
            "success_path": "/public/dashboard", "success_text": "", "success_mode": "exact_path",
            "inventory_navigation_selector": ".nav", "inventory_destination_path": "/public/fred",
            "firestore_listen_enabled": True, "firestore_listen_get_enabled": True,
            "revenuecat_subscriber_get_enabled": True, "approval_confirmed": True,
        },
    )
    monkeypatch.setenv("TEST_USERNAME", "probe-user")
    monkeypatch.setenv("TEST_PASSWORD", "probe-password")

    async def fake_probe(_approved, _username, _password, collect_control_inventory=False, chat_probe=None):
        assert collect_control_inventory is True
        assert chat_probe == {
            "input_selector": "#fred-chat-input",
            "submit_selector": "form:has(#fred-chat-input) button[type='submit']",
            "message": "Cloudsterr functional check. Please reply with READY.",
            "firestore_write_confirmed": True,
        }
        return {"status": "PASS", "outcome": "CHAT_PROBE_ACCEPTED", "chat_message_submitted": True, "probe_input_cleared": True, "blocked_requests": [], "auth_responses": []}

    monkeypatch.setattr("app.main.execute_approved_login", fake_probe)
    missing_confirmation = client.post(f"/api/sites/{site_id}/fixed-chat-probe-once", json={"execution_confirmed": False})
    first = client.post(f"/api/sites/{site_id}/fixed-chat-probe-once", json={"execution_confirmed": True})
    second = client.post(f"/api/sites/{site_id}/fixed-chat-probe-once", json={"execution_confirmed": True})
    assert missing_confirmation.status_code == 422
    assert first.status_code == 200
    assert first.json()["chat_message_submitted"] is True
    assert second.status_code == 409

    async def fake_response(_approved, _username, _password, collect_control_inventory=False, capture_latest_response=False, **_kwargs):
        assert collect_control_inventory is True
        assert capture_latest_response is True
        return {"status": "PASS", "outcome": "EXPECTED_RESPONSE_FOUND", "latest_response": "READY", "response_contains_ready": True, "blocked_requests": [], "auth_responses": []}

    monkeypatch.setattr("app.main.execute_approved_login", fake_response)
    captured = client.post(f"/api/sites/{site_id}/fixed-chat-probe/response", json={"execution_confirmed": True})
    assert captured.status_code == 200
    assert captured.json()["latest_response"] == "READY"
    assert captured.json()["chat_message_submitted"] is False

    async def fake_retry(_approved, _username, _password, collect_control_inventory=False, chat_probe=None, capture_latest_response=False):
        assert collect_control_inventory is True
        assert capture_latest_response is True
        assert chat_probe["retry"] == 1
        assert chat_probe["gemini_post_confirmed"] is True
        assert chat_probe["firestore_write_get_confirmed"] is True
        return {"status": "PASS", "outcome": "EXPECTED_RESPONSE_FOUND", "chat_message_submitted": True, "probe_input_cleared": True, "latest_response": "READY", "response_contains_ready": True, "blocked_requests": [], "auth_responses": []}

    monkeypatch.setattr("app.main.execute_approved_login", fake_retry)
    retry = client.post(f"/api/sites/{site_id}/fixed-chat-probe-retry-once", json={"execution_confirmed": True})
    duplicate_retry = client.post(f"/api/sites/{site_id}/fixed-chat-probe-retry-once", json={"execution_confirmed": True})
    assert retry.status_code == 200
    assert retry.json()["latest_response"] == "READY"
    assert duplicate_retry.status_code == 409

    async def fake_final(_approved, _username, _password, collect_control_inventory=False, chat_probe=None, capture_latest_response=False):
        assert collect_control_inventory is True
        assert capture_latest_response is True
        assert chat_probe["retry"] == 2
        assert chat_probe["corrected_wait_confirmed"] is True
        return {"status": "PASS", "outcome": "EXPECTED_RESPONSE_FOUND", "chat_message_submitted": True, "probe_input_cleared": True, "latest_response": "READY", "response_contains_ready": True, "blocked_requests": [], "auth_responses": []}

    monkeypatch.setattr("app.main.execute_approved_login", fake_final)
    final_attempt = client.post(f"/api/sites/{site_id}/fixed-chat-probe-final-once", json={"execution_confirmed": True})
    duplicate_final = client.post(f"/api/sites/{site_id}/fixed-chat-probe-final-once", json={"execution_confirmed": True})
    assert final_attempt.status_code == 200
    assert final_attempt.json()["probe_version"] == 3
    assert duplicate_final.status_code == 409

    async def fake_online(_approved, _username, _password, collect_control_inventory=False, chat_probe=None, capture_latest_response=False):
        assert collect_control_inventory is True
        assert capture_latest_response is True
        assert chat_probe["message"] == "Hi Fred, Cloudsterr AI Site Agent - checking in?"
        assert chat_probe["same_session_response_required"] is True
        return {"status": "PASS", "outcome": "RESPONSE_CAPTURED", "chat_message_submitted": True, "probe_input_cleared": True, "latest_response": "I'm here, Bill.", "response_contains_ready": False, "blocked_requests": [], "auth_responses": []}

    monkeypatch.setattr("app.main.execute_approved_login", fake_online)
    online = client.post(f"/api/sites/{site_id}/fred-online-check-once", json={"execution_confirmed": True})
    duplicate_online = client.post(f"/api/sites/{site_id}/fred-online-check-once", json={"execution_confirmed": True})
    assert online.status_code == 200
    assert online.json()["probe_version"] == 4
    assert online.json()["latest_response"] == "I'm here, Bill."
    assert duplicate_online.status_code == 409

    schedule_payload = {
        "frequency": "every_minute", "enabled": True, "monthly_limit": 43_200,
        "exact_prompt_confirmed": True, "real_message_confirmed": True, "bounded_network_confirmed": True,
    }
    rejected_schedule = client.put(
        f"/api/sites/{site_id}/fred-monitor-schedule",
        json={**schedule_payload, "exact_prompt_confirmed": False},
    )
    enabled_schedule = client.put(f"/api/sites/{site_id}/fred-monitor-schedule", json=schedule_payload)
    assert rejected_schedule.status_code == 422
    assert enabled_schedule.status_code == 200
    assert enabled_schedule.json()["frequency"] == "every_minute"
    assert enabled_schedule.json()["monthly_limit"] == 43_200
    assert enabled_schedule.json()["next_run_at"] is not None
    capped_schedule = client.put(
        f"/api/sites/{site_id}/fred-monitor-schedule",
        json={**schedule_payload, "monthly_limit": 1},
    )
    assert capped_schedule.status_code == 200
    scheduled_run = asyncio.run(run_scheduled_fred_monitor(site_id))
    assert scheduled_run["status"] == "PASS"
    with pytest.raises(HTTPException):
        asyncio.run(run_scheduled_fred_monitor(site_id))
    disabled_schedule = client.put(
        f"/api/sites/{site_id}/fred-monitor-schedule",
        json={"frequency": "every_minute", "enabled": False, "monthly_limit": 43_200},
    )
    assert disabled_schedule.status_code == 200
    assert disabled_schedule.json()["next_run_at"] is None
