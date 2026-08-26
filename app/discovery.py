from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

from playwright.async_api import Browser, Page, Request, Route, async_playwright


@dataclass(frozen=True)
class DiscoveryBoundary:
    base_url: str
    allowed_path: str
    excluded_paths: tuple[str, ...]
    max_pages: int = 10

    @property
    def origin(self) -> str:
        parsed = urlparse(self.base_url)
        return f"{parsed.scheme}://{parsed.netloc}"

    def permits_document(self, url: str) -> bool:
        parsed = urlparse(url)
        if f"{parsed.scheme}://{parsed.netloc}" != self.origin:
            return False
        path = parsed.path or "/"
        allowed = self.allowed_path.rstrip("/") or "/"
        if allowed != "/" and path != allowed and not path.startswith(f"{allowed}/"):
            return False
        return not any(path == item or path.startswith(f"{item.rstrip('/')}/") for item in self.excluded_paths)


async def discover(boundary: DiscoveryBoundary) -> list[dict]:
    """Inventory permitted public pages without submitting or modifying anything."""
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            return await _crawl(browser, boundary)
        finally:
            await browser.close()


async def _crawl(browser: Browser, boundary: DiscoveryBoundary) -> list[dict]:
    context = await browser.new_context(service_workers="block")
    page = await context.new_page()

    async def enforce_boundary(route: Route, request: Request) -> None:
        parsed = urlparse(request.url)
        same_origin = f"{parsed.scheme}://{parsed.netloc}" == boundary.origin
        safe_method = request.method in {"GET", "HEAD"}
        permitted_document = request.resource_type != "document" or boundary.permits_document(request.url)
        if same_origin and safe_method and permitted_document:
            await route.continue_()
        else:
            await route.abort("blockedbyclient")

    await page.route("**/*", enforce_boundary)
    start_url = urljoin(f"{boundary.base_url.rstrip('/')}/", boundary.allowed_path.lstrip("/"))
    queue = [start_url]
    visited: set[str] = set()
    results: list[dict] = []

    while queue and len(results) < boundary.max_pages:
        target = queue.pop(0).split("#", 1)[0]
        if target in visited or not boundary.permits_document(target):
            continue
        visited.add(target)
        response = await page.goto(target, wait_until="domcontentloaded", timeout=15_000)
        if response is None:
            continue
        inventory = await _inventory_page(page)
        results.append(
            {
                "url": page.url,
                "title": await page.title(),
                "status_code": response.status,
                "links": inventory["links"],
                "buttons": inventory["buttons"],
                "forms": inventory["forms"],
            }
        )
        for href in inventory["links"]:
            candidate = urljoin(page.url, href).split("#", 1)[0]
            if candidate not in visited and boundary.permits_document(candidate):
                queue.append(candidate)

    await context.close()
    return results


async def _inventory_page(page: Page) -> dict[str, list]:
    return await page.evaluate(
        """() => ({
          links: [...document.querySelectorAll('a[href]')].map((item) => item.getAttribute('href')).filter(Boolean),
          buttons: [...document.querySelectorAll('button')].map((item) => (item.innerText || item.getAttribute('aria-label') || '').trim()).filter(Boolean),
          forms: [...document.querySelectorAll('form')].map((form) => ({
            action: form.getAttribute('action') || '',
            method: (form.getAttribute('method') || 'get').toUpperCase(),
            fields: [...form.querySelectorAll('input, select, textarea')].map((field) => field.getAttribute('name') || field.getAttribute('type') || field.tagName.toLowerCase())
          }))
        })"""
    )
