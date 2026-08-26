import os
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import FastAPI, HTTPException, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, HttpUrl, field_validator
from starlette.middleware.trustedhost import TrustedHostMiddleware

APP_ROOT = Path(__file__).resolve().parent
STATIC_ROOT = APP_ROOT / "static"
DEFAULT_DATA_ROOT = APP_ROOT.parent / "sites"


class SiteRegistration(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    base_url: HttpUrl
    environment: Literal["Development", "Staging", "Production", "Other"]
    owner: str = Field(min_length=2, max_length=120)
    description: str = Field(default="", max_length=500)
    allowed_path: str = Field(default="/", max_length=500)
    excluded_paths: list[str] = Field(default_factory=list, max_length=20)
    authorization_confirmed: bool

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: HttpUrl) -> HttpUrl:
        if value.username or value.password:
            raise ValueError("Base URL must not contain credentials")
        if value.query or value.fragment:
            raise ValueError("Base URL must not contain a query string or fragment")
        if value.path not in (None, "", "/"):
            raise ValueError("Put path boundaries in the allowed path field")
        return value

    @field_validator("name", "owner", "description")
    @classmethod
    def trim_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("allowed_path")
    @classmethod
    def validate_allowed_path(cls, value: str) -> str:
        value = value.strip()
        if not value.startswith("/"):
            raise ValueError("Allowed path must begin with /")
        return value

    @field_validator("excluded_paths")
    @classmethod
    def validate_excluded_paths(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip() for value in values if value.strip()]
        if any(not value.startswith("/") for value in cleaned):
            raise ValueError("Each excluded path must begin with /")
        return list(dict.fromkeys(cleaned))


def database_path() -> Path:
    data_root = Path(os.environ.get("CLOUDSTERR_DATA_DIR", DEFAULT_DATA_ROOT))
    data_root.mkdir(parents=True, exist_ok=True)
    return data_root / "cloudsterr.db"


def connect_database() -> sqlite3.Connection:
    connection = sqlite3.connect(database_path())
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS sites (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            base_url TEXT NOT NULL UNIQUE,
            environment TEXT NOT NULL,
            owner TEXT NOT NULL,
            description TEXT NOT NULL,
            allowed_path TEXT NOT NULL,
            excluded_paths TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    connection.commit()
    return connection


def serialize_site(row: sqlite3.Row) -> dict[str, str | int | None | list[str]]:
    return {
        "id": row["id"],
        "name": row["name"],
        "base_url": row["base_url"],
        "environment": row["environment"],
        "owner": row["owner"],
        "description": row["description"],
        "allowed_path": row["allowed_path"],
        "excluded_paths": [item for item in row["excluded_paths"].split("\n") if item],
        "status": row["status"],
        "created_at": row["created_at"],
        "last_check": None,
        "passed": 0,
        "failed": 0,
    }


app = FastAPI(
    title="Cloudsterr AI Site Agent",
    description="Authorized functional website monitoring from an end user's perspective.",
    version="0.2.0",
)
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=["127.0.0.1", "localhost", "testserver"],
)
app.mount("/static", StaticFiles(directory=STATIC_ROOT), name="static")


@app.get("/", include_in_schema=False)
async def dashboard() -> FileResponse:
    return FileResponse(STATIC_ROOT / "index.html")


@app.get("/api/health", tags=["system"])
async def health() -> dict[str, str]:
    return {"status": "ok", "version": app.version}


@app.get("/api/sites", tags=["sites"])
async def list_sites() -> dict[str, list[dict[str, str | int | None | list[str]]]]:
    with closing(connect_database()) as connection:
        rows = connection.execute("SELECT * FROM sites ORDER BY created_at DESC").fetchall()
    return {"sites": [serialize_site(row) for row in rows]}


@app.post("/api/sites", tags=["sites"], status_code=status.HTTP_201_CREATED)
async def register_site(site: SiteRegistration) -> dict[str, str | int | None | list[str]]:
    if not site.authorization_confirmed:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Explicit authorization confirmation is required.",
        )

    normalized_url = str(site.base_url).rstrip("/")
    site_id = str(uuid4())
    created_at = datetime.now(UTC).isoformat()

    try:
        with closing(connect_database()) as connection:
            connection.execute(
                """
                INSERT INTO sites (
                    id, name, base_url, environment, owner, description,
                    allowed_path, excluded_paths, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    site_id,
                    site.name,
                    normalized_url,
                    site.environment,
                    site.owner,
                    site.description,
                    site.allowed_path,
                    "\n".join(site.excluded_paths),
                    "BASELINE REQUIRED",
                    created_at,
                ),
            )
            connection.commit()
            row = connection.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone()
    except sqlite3.IntegrityError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This base URL is already registered.") from error

    return serialize_site(row)
