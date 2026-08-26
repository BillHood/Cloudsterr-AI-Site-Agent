from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles


APP_ROOT = Path(__file__).resolve().parent
STATIC_ROOT = APP_ROOT / "static"

app = FastAPI(
    title="Cloudsterr AI Site Agent",
    description="Authorized functional website monitoring from an end user's perspective.",
    version="0.1.0",
)
app.mount("/static", StaticFiles(directory=STATIC_ROOT), name="static")


@app.get("/", include_in_schema=False)
async def dashboard() -> FileResponse:
    return FileResponse(STATIC_ROOT / "index.html")


@app.get("/api/health", tags=["system"])
async def health() -> dict[str, str]:
    return {"status": "ok", "version": app.version}


@app.get("/api/sites", tags=["sites"])
async def list_sites() -> dict[str, list[dict[str, str | int | None]]]:
    """Return demonstration data only; Milestone 1 performs no external checks."""
    return {
        "sites": [
            {
                "id": "demo-site",
                "name": "Demonstration Site",
                "environment": "Demo only",
                "status": "BASELINE REQUIRED",
                "last_check": None,
                "passed": 0,
                "failed": 0,
            }
        ]
    }
