from dataclasses import dataclass
import re
from urllib.parse import urljoin, urlparse

from playwright.async_api import Request, Route, async_playwright


def _safe_page_url(url: str, origin: str) -> str:
    parsed = urlparse(url)
    if f"{parsed.scheme}://{parsed.netloc}" != origin:
        return origin
    return f"{origin}{parsed.path or '/'}"


def _redact(text: str, secrets: tuple[str, ...]) -> str:
    sanitized = " ".join(text.split())[:300]
    for secret in secrets:
        if secret:
            sanitized = sanitized.replace(secret, "[REDACTED]")
    return sanitized


def sanitized_request_evidence(url: str, method: str, resource_type: str) -> dict:
    parsed = urlparse(url)
    return {
        "method": method,
        "hostname": parsed.hostname or "unknown",
        "path": sanitized_path(parsed.path or "/"),
        "resource_type": resource_type,
    }


def sanitized_path(path: str) -> str:
    segments = path.split("/")
    cleaned = [
        "[REDACTED]" if len(segment) >= 16 and re.search(r"[A-Za-z]", segment) and re.search(r"\d", segment) else segment
        for segment in segments
    ]
    return "/".join(cleaned)


def sanitized_dom_attribute(value: str | None) -> str | None:
    if not value:
        return None
    value = " ".join(value.split())[:120]
    if len(value) >= 16 and re.search(r"[A-Za-z]", value) and re.search(r"\d", value):
        return "[REDACTED]"
    return value


def sanitize_control_inventory(items: list[dict]) -> list[dict]:
    allowed = {
        "tag", "type", "id", "name", "role", "test_id", "contenteditable",
        "classes", "disabled", "visible", "form_id", "parent_tag", "parent_id",
        "parent_role", "parent_test_id",
    }
    return [
        {key: sanitized_dom_attribute(str(value)) for key, value in item.items() if key in allowed and value not in (None, "")}
        for item in items[:50]
    ]


def sanitize_evidence(evidence: dict) -> dict:
    sanitized = dict(evidence)
    for key in ("blocked_requests", "auth_responses"):
        sanitized[key] = [
            {**item, "path": sanitized_path(item.get("path", "/"))}
            for item in evidence.get(key, [])
        ]
    if evidence.get("control_inventory") is not None:
        sanitized["control_inventory"] = sanitize_control_inventory(evidence["control_inventory"])
    return sanitized


def classify_login_result(
    *,
    path_matches: bool,
    text_matches: bool,
    submission_used: bool,
    blocked_requests: list[dict],
    visible_errors: list[str],
    auth_responses: list[dict] | None = None,
) -> tuple[str, str]:
    auth_responses = auth_responses or []
    if path_matches and text_matches:
        return "PASS", "SUCCESS"
    if any(item["status"] >= 400 for item in auth_responses):
        return "FAIL", "AUTH_REJECTED"
    if any(200 <= item["status"] < 400 for item in auth_responses):
        return "FAIL", "SUCCESS_EVIDENCE_MISMATCH"
    if any(item["method"] not in {"GET", "HEAD"} for item in blocked_requests):
        return "FAIL", "EXTERNAL_AUTH_BLOCKED"
    joined_errors = " ".join(visible_errors)
    if re.search(r"invalid|incorrect|unauthorized|wrong|credentials|not match", joined_errors, re.IGNORECASE):
        return "FAIL", "BAD_CREDENTIALS"
    if visible_errors or not submission_used:
        return "FAIL", "VALIDATION_FAILED"
    return "FAIL", "SUCCESS_EVIDENCE_MISMATCH"


@dataclass(frozen=True)
class ApprovedLogin:
    base_url: str
    allowed_path: str
    excluded_paths: tuple[str, ...]
    login_path: str
    username_selector: str
    password_selector: str
    submit_selector: str
    success_path: str
    success_text: str
    success_mode: str = "path_and_text"
    authenticated_shell_check: bool = False
    main_selector: str = ""
    heading_selector: str = ""
    navigation_selector: str = ""
    external_auth_url: str | None = None
    external_followup_url: str | None = None
    inventory_navigation_selector: str = ""
    inventory_navigation_index: int = 0
    inventory_destination_path: str = ""
    firestore_listen_enabled: bool = False

    @property
    def origin(self) -> str:
        parsed = urlparse(self.base_url)
        return f"{parsed.scheme}://{parsed.netloc}"

    def permitted_url(self, url: str) -> bool:
        parsed = urlparse(url)
        if f"{parsed.scheme}://{parsed.netloc}" != self.origin:
            return False
        path = parsed.path or "/"
        allowed = self.allowed_path.rstrip("/") or "/"
        if allowed != "/" and path != allowed and not path.startswith(f"{allowed}/"):
            return False
        return not any(path == item or path.startswith(f"{item.rstrip('/')}/") for item in self.excluded_paths)

    @staticmethod
    def _endpoint_key(url: str) -> tuple[str, str, int, str]:
        parsed = urlparse(url)
        return (parsed.scheme, parsed.hostname or "", parsed.port or 443, parsed.path.rstrip("/"))

    def approved_auth_endpoint(self, url: str) -> tuple[str, str, int, str] | None:
        requested = urlparse(url)
        requested_key = self._endpoint_key(url)
        if requested.scheme != "https":
            return None
        for endpoint in (self.external_auth_url, self.external_followup_url):
            if endpoint and requested_key == self._endpoint_key(endpoint):
                return requested_key
        return None

    def permits_auth_submission(self, url: str) -> bool:
        return self.approved_auth_endpoint(url) is not None

    def permits_firestore_listen(self, url: str) -> bool:
        parsed = urlparse(url)
        return (
            self.firestore_listen_enabled
            and parsed.scheme == "https"
            and parsed.hostname == "firestore.googleapis.com"
            and parsed.path.rstrip("/").endswith("/Listen/channel")
        )

    def success_path_matches(self, url: str) -> bool:
        actual = urlparse(url)
        expected = urlparse(urljoin(f"{self.base_url.rstrip('/')}/", self.success_path.lstrip("/")))
        return (
            actual.scheme == expected.scheme
            and actual.netloc == expected.netloc
            and actual.path.rstrip("/") == expected.path.rstrip("/")
        )


async def execute_approved_login(login: ApprovedLogin, username: str, password: str, collect_control_inventory: bool = False) -> dict:
    """Execute exactly one approved login submission and return sanitized evidence."""
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        context = await browser.new_context(service_workers="block")
        page = await context.new_page()
        document_submission_used = False
        used_auth_endpoints: set[tuple[str, str, int, str]] = set()
        stage = "BROWSER_STARTED"
        blocked_requests: list[dict] = []
        auth_responses: list[dict] = []
        inventory_navigation_matches = None

        def record_auth_response(response) -> None:
            if response.request.method == "POST" and login.permits_auth_submission(response.url):
                parsed = urlparse(response.url)
                auth_responses.append(
                    {
                        "status": response.status,
                        "hostname": parsed.hostname or "unknown",
                        "path": sanitized_path(parsed.path or "/"),
                    }
                )

        page.on("response", record_auth_response)

        async def enforce(route: Route, request: Request) -> None:
            nonlocal document_submission_used
            same_origin = urlparse(request.url).netloc == urlparse(login.base_url).netloc
            permitted = same_origin and login.permitted_url(request.url)
            if request.method in {"GET", "HEAD"} and permitted:
                await route.continue_()
                return
            approved_document_post = request.resource_type == "document" and permitted and not document_submission_used
            auth_endpoint = login.approved_auth_endpoint(request.url) if request.resource_type in {"fetch", "xhr"} else None
            approved_external_auth_post = auth_endpoint is not None and auth_endpoint not in used_auth_endpoints
            approved_firestore_listen = (
                request.method == "POST"
                and request.resource_type in {"fetch", "xhr"}
                and login.permits_firestore_listen(request.url)
            )
            if request.method == "POST" and (approved_document_post or approved_external_auth_post or approved_firestore_listen):
                if approved_document_post:
                    document_submission_used = True
                if auth_endpoint is not None:
                    used_auth_endpoints.add(auth_endpoint)
                await route.continue_()
                return
            blocked = sanitized_request_evidence(request.url, request.method, request.resource_type)
            if blocked not in blocked_requests:
                blocked_requests.append(blocked)
            await route.abort("blockedbyclient")

        await page.route("**/*", enforce)
        try:
            await page.goto(urljoin(f"{login.base_url.rstrip('/')}/", login.login_path.lstrip("/")), wait_until="domcontentloaded", timeout=15_000)
            stage = "PAGE_LOADED"
            await page.locator(login.username_selector).fill(username, timeout=5_000)
            await page.locator(login.password_selector).fill(password, timeout=5_000)
            stage = "FIELDS_FILLED"
            await page.locator(login.submit_selector).click(timeout=5_000)
            stage = "SUBMIT_CLICKED"
            await page.wait_for_timeout(2_000)
            final_url = page.url
            path_matches = login.success_path_matches(final_url)
            text_matches = None if login.success_mode == "exact_path" else await page.get_by_text(login.success_text, exact=False).count() > 0
            shell_checks = None
            if login.authenticated_shell_check:
                shell_checks = {
                    "main_visible": await page.locator(login.main_selector).first.is_visible(),
                    "heading_visible": await page.locator(login.heading_selector).first.is_visible(),
                    "navigation_visible": await page.locator(login.navigation_selector).first.is_visible(),
                }
            shell_matches = shell_checks is None or all(shell_checks.values())
            success_evidence_matches = path_matches and (login.success_mode == "exact_path" or bool(text_matches)) and shell_matches
            control_inventory = None
            if collect_control_inventory and success_evidence_matches:
                if login.inventory_destination_path:
                    inventory_navigation_matches = False
                    destination_url = urljoin(
                        f"{login.base_url.rstrip('/')}/",
                        login.inventory_destination_path.lstrip("/"),
                    )
                    if not login.permitted_url(destination_url):
                        raise RuntimeError("Approved inventory destination is outside the permitted boundary")
                    stage = "INVENTORY_DESTINATION_REQUESTED"
                    await page.goto(destination_url, wait_until="domcontentloaded", timeout=10_000)
                    inventory_navigation_matches = (
                        login.permitted_url(page.url)
                        and urlparse(page.url).path.rstrip("/") == login.inventory_destination_path.rstrip("/")
                    )
                    if not inventory_navigation_matches:
                        raise RuntimeError("Approved inventory destination was not reached")
                    final_url = page.url
                    stage = "INVENTORY_DESTINATION_REACHED"
                control_selector = "input, textarea, button, [contenteditable='true'], [role='log'], [role='status'], [aria-live]"
                stage = "INVENTORY_CONTROLS_PENDING"
                await page.locator(control_selector).first.wait_for(state="attached", timeout=10_000)
                stage = "INVENTORY_CONTROLS_READY"
                raw_controls = await page.locator(control_selector).evaluate_all(
                    """elements => elements.map(element => ({
                        tag: element.tagName.toLowerCase(),
                        type: element.getAttribute('type'),
                        id: element.getAttribute('id'),
                        name: element.getAttribute('name'),
                        role: element.getAttribute('role'),
                        test_id: element.getAttribute('data-testid'),
                        contenteditable: element.getAttribute('contenteditable'),
                        classes: Array.from(element.classList).slice(0, 5).join(' '),
                        disabled: element.matches(':disabled') ? 'true' : 'false',
                        visible: element.getClientRects().length > 0 ? 'true' : 'false',
                        form_id: element.form?.getAttribute('id'),
                        parent_tag: element.parentElement?.tagName.toLowerCase(),
                        parent_id: element.parentElement?.getAttribute('id'),
                        parent_role: element.parentElement?.getAttribute('role'),
                        parent_test_id: element.parentElement?.getAttribute('data-testid')
                    }))"""
                )
                control_inventory = sanitize_control_inventory(raw_controls)
            visible_errors = []
            if not collect_control_inventory:
                error_locator = page.locator("[role='alert'], [aria-live='assertive'], .error, .alert")
                visible_errors = [
                    _redact(item, (username, password))
                    for item in await error_locator.all_inner_texts()
                    if item.strip()
                ][:5]
            status, outcome = classify_login_result(
                path_matches=path_matches,
                text_matches=success_evidence_matches,
                submission_used=document_submission_used or bool(used_auth_endpoints),
                blocked_requests=blocked_requests,
                visible_errors=visible_errors,
                auth_responses=auth_responses,
            )
            return {
                "status": status,
                "outcome": outcome,
                "stage": stage,
                "final_url": _safe_page_url(final_url, login.origin),
                "path_matches": path_matches,
                "text_matches": text_matches,
                "shell_checks": shell_checks,
                "control_inventory": control_inventory,
                "inventory_navigation_matches": inventory_navigation_matches,
                "submission_count": int(document_submission_used) + len(used_auth_endpoints),
                "visible_errors": visible_errors,
                "blocked_requests": blocked_requests[:20],
                "auth_responses": auth_responses[:5],
            }
        except Exception as error:
            return {
                "status": "BLOCKED",
                "outcome": "EXECUTION_BLOCKED",
                "stage": stage,
                "failure": type(error).__name__,
                "submission_count": int(document_submission_used) + len(used_auth_endpoints),
                "blocked_requests": blocked_requests[:20],
                "auth_responses": auth_responses[:5],
                "inventory_navigation_matches": inventory_navigation_matches,
            }
        finally:
            await context.close()
            await browser.close()
