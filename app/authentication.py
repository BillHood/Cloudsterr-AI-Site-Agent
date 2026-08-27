from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

from playwright.async_api import Request, Route, async_playwright


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


async def execute_approved_login(login: ApprovedLogin, username: str, password: str) -> dict:
    """Execute exactly one approved login submission and return sanitized evidence."""
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        context = await browser.new_context(service_workers="block")
        page = await context.new_page()
        submission_used = False

        async def enforce(route: Route, request: Request) -> None:
            nonlocal submission_used
            same_origin = urlparse(request.url).netloc == urlparse(login.base_url).netloc
            permitted = same_origin and login.permitted_url(request.url)
            if request.method in {"GET", "HEAD"} and permitted:
                await route.continue_()
                return
            if request.method == "POST" and request.resource_type == "document" and permitted and not submission_used:
                submission_used = True
                await route.continue_()
                return
            await route.abort("blockedbyclient")

        await page.route("**/*", enforce)
        try:
            await page.goto(urljoin(f"{login.base_url.rstrip('/')}/", login.login_path.lstrip("/")), wait_until="domcontentloaded", timeout=15_000)
            await page.locator(login.username_selector).fill(username, timeout=5_000)
            await page.locator(login.password_selector).fill(password, timeout=5_000)
            await page.locator(login.submit_selector).click(timeout=5_000)
            await page.wait_for_load_state("domcontentloaded", timeout=15_000)
            final_url = page.url
            expected_url = urljoin(f"{login.base_url.rstrip('/')}/", login.success_path.lstrip("/"))
            path_matches = urlparse(final_url).path.rstrip("/") == urlparse(expected_url).path.rstrip("/")
            text_matches = await page.get_by_text(login.success_text, exact=False).count() > 0
            passed = path_matches and text_matches
            return {
                "status": "PASS" if passed else "FAIL",
                "final_url": final_url if login.permitted_url(final_url) else login.origin,
                "path_matches": path_matches,
                "text_matches": text_matches,
                "submission_count": 1 if submission_used else 0,
            }
        finally:
            await context.close()
            await browser.close()
