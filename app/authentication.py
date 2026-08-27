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
        "path": parsed.path or "/",
        "resource_type": resource_type,
    }


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
    external_auth_url: str | None = None
    external_followup_url: str | None = None

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

    def success_path_matches(self, url: str) -> bool:
        actual = urlparse(url)
        expected = urlparse(urljoin(f"{self.base_url.rstrip('/')}/", self.success_path.lstrip("/")))
        return (
            actual.scheme == expected.scheme
            and actual.netloc == expected.netloc
            and actual.path.rstrip("/") == expected.path.rstrip("/")
        )


async def execute_approved_login(login: ApprovedLogin, username: str, password: str) -> dict:
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

        def record_auth_response(response) -> None:
            if response.request.method == "POST" and login.permits_auth_submission(response.url):
                parsed = urlparse(response.url)
                auth_responses.append(
                    {
                        "status": response.status,
                        "hostname": parsed.hostname or "unknown",
                        "path": parsed.path or "/",
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
            if request.method == "POST" and (approved_document_post or approved_external_auth_post):
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
            success_evidence_matches = path_matches and (login.success_mode == "exact_path" or bool(text_matches))
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
            }
        finally:
            await context.close()
            await browser.close()
