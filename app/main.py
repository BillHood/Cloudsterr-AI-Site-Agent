import asyncio
import os
import json
import sqlite3
import re
from contextlib import asynccontextmanager, closing, suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import FastAPI, HTTPException, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, HttpUrl, field_validator, model_validator
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.discovery import DiscoveryBoundary, discover
from app.authentication import ApprovedLogin, execute_approved_login, sanitize_evidence

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


class BaselineApproval(BaseModel):
    discovery_run_id: str
    reviewer: str = Field(min_length=2, max_length=120)
    approval_confirmed: bool

    @field_validator("reviewer")
    @classmethod
    def trim_reviewer(cls, value: str) -> str:
        return value.strip()


class ScheduleConfiguration(BaseModel):
    frequency: Literal["hourly", "daily", "weekly"]
    enabled: bool
    approval_confirmed: bool


class AuthenticationProfile(BaseModel):
    login_path: str = Field(min_length=1, max_length=500)
    username_env: str = Field(min_length=3, max_length=64)
    password_env: str = Field(min_length=3, max_length=64)
    test_account_confirmed: bool

    @field_validator("login_path")
    @classmethod
    def validate_login_path(cls, value: str) -> str:
        value = value.strip()
        if not value.startswith("/") or value.startswith("//"):
            raise ValueError("Login path must be a site-relative path beginning with /")
        return value

    @field_validator("username_env", "password_env")
    @classmethod
    def validate_environment_name(cls, value: str) -> str:
        value = value.strip()
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{2,63}", value):
            raise ValueError("Use an uppercase environment-variable name")
        return value


class LoginJourney(BaseModel):
    username_selector: str = Field(min_length=1, max_length=300)
    password_selector: str = Field(min_length=1, max_length=300)
    submit_selector: str = Field(min_length=1, max_length=300)
    success_path: str = Field(min_length=1, max_length=500)
    success_text: str = Field(default="", max_length=300)
    success_mode: Literal["path_and_text", "exact_path"] = "path_and_text"
    authenticated_shell_check: bool = False
    main_selector: str = Field(default="", max_length=300)
    heading_selector: str = Field(default="", max_length=300)
    navigation_selector: str = Field(default="", max_length=300)
    external_auth_url: HttpUrl | None = None
    external_followup_url: HttpUrl | None = None
    approval_confirmed: bool

    @field_validator("username_selector", "password_selector", "submit_selector", "success_text", "main_selector", "heading_selector", "navigation_selector")
    @classmethod
    def trim_journey_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("success_path")
    @classmethod
    def validate_success_path(cls, value: str) -> str:
        value = value.strip()
        if not value.startswith("/") or value.startswith("//"):
            raise ValueError("Success path must be a site-relative path beginning with /")
        return value

    @field_validator("external_auth_url", "external_followup_url")
    @classmethod
    def validate_external_auth_url(cls, value: HttpUrl | None) -> HttpUrl | None:
        if value is None:
            return None
        if value.scheme != "https" or value.username or value.password or value.query or value.fragment:
            raise ValueError("External authentication URL must be HTTPS and contain no credentials, query, or fragment")
        if value.path in (None, "", "/"):
            raise ValueError("External authentication URL must include the exact endpoint path")
        return value

    @model_validator(mode="after")
    def require_distinct_external_endpoints(self):
        if self.success_mode == "path_and_text" and not self.success_text:
            raise ValueError("Success text is required when path-and-text mode is selected")
        if self.authenticated_shell_check and not all((self.main_selector, self.heading_selector, self.navigation_selector)):
            raise ValueError("Main, heading, and navigation selectors are required for authenticated shell checks")
        if self.external_auth_url and self.external_followup_url:
            primary = str(self.external_auth_url).rstrip("/")
            followup = str(self.external_followup_url).rstrip("/")
            if primary == followup:
                raise ValueError("Primary and post-login endpoints must be distinct")
        return self


class LoginExecutionRequest(BaseModel):
    execution_confirmed: bool


LOGIN_DEFINITION_FIELDS = (
    "username_selector", "password_selector", "submit_selector", "success_path", "success_text",
    "success_mode", "authenticated_shell_check", "main_selector", "heading_selector",
    "navigation_selector", "external_auth_url", "external_followup_url",
)


def login_definition(source) -> dict:
    definition = {}
    for field in LOGIN_DEFINITION_FIELDS:
        value = source[field] if isinstance(source, sqlite3.Row) else getattr(source, field)
        if field == "authenticated_shell_check":
            value = bool(value)
        if field in {"external_auth_url", "external_followup_url"}:
            value = str(value) if value else None
        definition[field] = value
    return definition

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
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS authentication_runs (
            id TEXT PRIMARY KEY,
            site_id TEXT NOT NULL,
            started_at TEXT NOT NULL,
            completed_at TEXT NOT NULL,
            status TEXT NOT NULL,
            evidence_json TEXT NOT NULL,
            FOREIGN KEY (site_id) REFERENCES sites(id)
        )
        """
    )
    authentication_run_columns = {row[1] for row in connection.execute("PRAGMA table_info(authentication_runs)")}
    if "interaction_definition_id" not in authentication_run_columns:
        connection.execute("ALTER TABLE authentication_runs ADD COLUMN interaction_definition_id TEXT")
    if "interaction_version" not in authentication_run_columns:
        connection.execute("ALTER TABLE authentication_runs ADD COLUMN interaction_version INTEGER")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS login_journeys (
            site_id TEXT PRIMARY KEY,
            username_selector TEXT NOT NULL,
            password_selector TEXT NOT NULL,
            submit_selector TEXT NOT NULL,
            success_path TEXT NOT NULL,
            success_text TEXT NOT NULL,
            success_mode TEXT NOT NULL DEFAULT 'path_and_text',
            authenticated_shell_check INTEGER NOT NULL DEFAULT 0,
            main_selector TEXT NOT NULL DEFAULT '',
            heading_selector TEXT NOT NULL DEFAULT '',
            navigation_selector TEXT NOT NULL DEFAULT '',
            external_auth_url TEXT,
            external_followup_url TEXT,
            approved_at TEXT NOT NULL,
            execution_enabled INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (site_id) REFERENCES sites(id),
            FOREIGN KEY (site_id) REFERENCES authentication_profiles(site_id)
        )
        """
    )
    login_journey_columns = {row[1] for row in connection.execute("PRAGMA table_info(login_journeys)")}
    if "external_auth_url" not in login_journey_columns:
        connection.execute("ALTER TABLE login_journeys ADD COLUMN external_auth_url TEXT")
    if "external_followup_url" not in login_journey_columns:
        connection.execute("ALTER TABLE login_journeys ADD COLUMN external_followup_url TEXT")
    if "success_mode" not in login_journey_columns:
        connection.execute("ALTER TABLE login_journeys ADD COLUMN success_mode TEXT NOT NULL DEFAULT 'path_and_text'")
    if "authenticated_shell_check" not in login_journey_columns:
        connection.execute("ALTER TABLE login_journeys ADD COLUMN authenticated_shell_check INTEGER NOT NULL DEFAULT 0")
    if "main_selector" not in login_journey_columns:
        connection.execute("ALTER TABLE login_journeys ADD COLUMN main_selector TEXT NOT NULL DEFAULT ''")
    if "heading_selector" not in login_journey_columns:
        connection.execute("ALTER TABLE login_journeys ADD COLUMN heading_selector TEXT NOT NULL DEFAULT ''")
    if "navigation_selector" not in login_journey_columns:
        connection.execute("ALTER TABLE login_journeys ADD COLUMN navigation_selector TEXT NOT NULL DEFAULT ''")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS authentication_profiles (
            site_id TEXT PRIMARY KEY,
            login_path TEXT NOT NULL,
            username_env TEXT NOT NULL,
            password_env TEXT NOT NULL,
            execution_enabled INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (site_id) REFERENCES sites(id)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS schedules (
            site_id TEXT PRIMARY KEY,
            frequency TEXT NOT NULL,
            enabled INTEGER NOT NULL,
            next_run_at TEXT,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (site_id) REFERENCES sites(id)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS execution_runs (
            id TEXT PRIMARY KEY,
            site_id TEXT NOT NULL,
            baseline_id TEXT NOT NULL,
            started_at TEXT NOT NULL,
            completed_at TEXT,
            status TEXT NOT NULL,
            passed INTEGER NOT NULL DEFAULT 0,
            failed INTEGER NOT NULL DEFAULT 0,
            details_json TEXT NOT NULL,
            FOREIGN KEY (site_id) REFERENCES sites(id),
            FOREIGN KEY (baseline_id) REFERENCES baselines(id)
        )
        """
    )
    connection.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS one_running_execution_per_site ON execution_runs(site_id) WHERE status = 'RUNNING'"
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS baselines (
            id TEXT PRIMARY KEY,
            site_id TEXT NOT NULL,
            discovery_run_id TEXT NOT NULL,
            version INTEGER NOT NULL,
            reviewer TEXT NOT NULL,
            approved_at TEXT NOT NULL,
            snapshot_json TEXT NOT NULL,
            UNIQUE(site_id, version),
            UNIQUE(discovery_run_id),
            FOREIGN KEY (site_id) REFERENCES sites(id),
            FOREIGN KEY (discovery_run_id) REFERENCES discovery_runs(id)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS discovery_runs (
            id TEXT PRIMARY KEY,
            site_id TEXT NOT NULL,
            started_at TEXT NOT NULL,
            completed_at TEXT NOT NULL,
            status TEXT NOT NULL,
            page_count INTEGER NOT NULL,
            inventory_json TEXT NOT NULL,
            FOREIGN KEY (site_id) REFERENCES sites(id)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS interaction_definitions (
            id TEXT PRIMARY KEY,
            site_id TEXT NOT NULL,
            interaction_type TEXT NOT NULL,
            version INTEGER NOT NULL,
            definition_json TEXT NOT NULL,
            approved_at TEXT NOT NULL,
            supersedes_id TEXT,
            UNIQUE(site_id, interaction_type, version),
            FOREIGN KEY (site_id) REFERENCES sites(id),
            FOREIGN KEY (supersedes_id) REFERENCES interaction_definitions(id)
        )
        """
    )
    existing_journeys = connection.execute("SELECT * FROM login_journeys").fetchall()
    for journey in existing_journeys:
        existing_definition = connection.execute(
            "SELECT id FROM interaction_definitions WHERE site_id = ? AND interaction_type = 'login' LIMIT 1",
            (journey["site_id"],),
        ).fetchone()
        if existing_definition is None:
            connection.execute(
                "INSERT INTO interaction_definitions (id, site_id, interaction_type, version, definition_json, approved_at, supersedes_id) VALUES (?, ?, 'login', 1, ?, ?, NULL)",
                (str(uuid4()), journey["site_id"], json.dumps(login_definition(journey), sort_keys=True), journey["approved_at"]),
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


def next_scheduled_time(frequency: str, from_time: datetime | None = None) -> datetime:
    current = from_time or datetime.now(UTC)
    delta = {"hourly": timedelta(hours=1), "daily": timedelta(days=1), "weekly": timedelta(weeks=1)}[frequency]
    return current + delta


async def scheduler_loop() -> None:
    while True:
        now = datetime.now(UTC)
        with closing(connect_database()) as connection:
            due = connection.execute(
                "SELECT site_id, frequency FROM schedules WHERE enabled = 1 AND next_run_at <= ?",
                (now.isoformat(),),
            ).fetchall()
            for schedule in due:
                connection.execute(
                    "UPDATE schedules SET next_run_at = ?, updated_at = ? WHERE site_id = ?",
                    (next_scheduled_time(schedule["frequency"], now).isoformat(), now.isoformat(), schedule["site_id"]),
                )
            connection.commit()
        for schedule in due:
            with suppress(HTTPException, Exception):
                await run_approved_baseline(schedule["site_id"])
        await asyncio.sleep(30)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    task = asyncio.create_task(scheduler_loop())
    yield
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


app = FastAPI(
    title="Cloudsterr AI Site Agent",
    description="Authorized functional website monitoring from an end user's perspective.",
    version="0.0.19",
    lifespan=lifespan,
)
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=["127.0.0.1", "localhost", "testserver"],
)
app.mount("/static", StaticFiles(directory=STATIC_ROOT), name="static")


@app.get("/", include_in_schema=False)
async def dashboard() -> FileResponse:
    return FileResponse(STATIC_ROOT / "index.html")


@app.get("/demo-site", include_in_schema=False)
async def demonstration_site() -> FileResponse:
    return FileResponse(STATIC_ROOT / "demo-site.html")


@app.get("/demo-site/about", include_in_schema=False)
async def demonstration_about() -> FileResponse:
    return FileResponse(STATIC_ROOT / "demo-about.html")


@app.get("/api/health", tags=["system"])
async def health() -> dict[str, str]:
    return {"status": "ok", "version": app.version}


@app.get("/api/sites", tags=["sites"])
async def list_sites() -> dict[str, list[dict[str, str | int | None | list[str]]]]:
    with closing(connect_database()) as connection:
        rows = connection.execute("SELECT * FROM sites ORDER BY created_at DESC").fetchall()
        sites = []
        for row in rows:
            item = serialize_site(row)
            latest = connection.execute(
                "SELECT completed_at, passed, failed FROM execution_runs WHERE site_id = ? AND status != 'RUNNING' ORDER BY started_at DESC LIMIT 1",
                (row["id"],),
            ).fetchone()
            if latest:
                item["last_check"] = latest["completed_at"]
                item["passed"] = latest["passed"]
                item["failed"] = latest["failed"]
            sites.append(item)
    return {"sites": sites}


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


@app.post("/api/sites/{site_id}/discover", tags=["discovery"])
async def discover_site(site_id: str) -> dict:
    with closing(connect_database()) as connection:
        site = connection.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone()
    if site is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Site not found.")

    boundary = DiscoveryBoundary(
        base_url=site["base_url"],
        allowed_path=site["allowed_path"],
        excluded_paths=tuple(item for item in site["excluded_paths"].split("\n") if item),
    )
    started_at = datetime.now(UTC).isoformat()
    try:
        pages = await discover(boundary)
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Read-only discovery failed: {type(error).__name__}",
        ) from error
    if not pages:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Discovery returned no permitted pages.")

    completed_at = datetime.now(UTC).isoformat()
    run_id = str(uuid4())
    with closing(connect_database()) as connection:
        connection.execute(
            """
            INSERT INTO discovery_runs (
                id, site_id, started_at, completed_at, status, page_count, inventory_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (run_id, site_id, started_at, completed_at, "COMPLETED", len(pages), json.dumps(pages)),
        )
        connection.execute("UPDATE sites SET status = ? WHERE id = ?", ("BASELINE REVIEW", site_id))
        connection.commit()

    return {"run_id": run_id, "status": "COMPLETED", "page_count": len(pages), "pages": pages}


@app.get("/api/sites/{site_id}/discoveries", tags=["discovery"])
async def list_discoveries(site_id: str) -> dict:
    with closing(connect_database()) as connection:
        rows = connection.execute(
            "SELECT * FROM discovery_runs WHERE site_id = ? ORDER BY started_at DESC",
            (site_id,),
        ).fetchall()
    return {
        "runs": [
            {
                "id": row["id"],
                "status": row["status"],
                "started_at": row["started_at"],
                "completed_at": row["completed_at"],
                "page_count": row["page_count"],
                "pages": json.loads(row["inventory_json"]),
            }
            for row in rows
        ]
    }


@app.post("/api/sites/{site_id}/baselines", tags=["baselines"], status_code=status.HTTP_201_CREATED)
async def approve_baseline(site_id: str, approval: BaselineApproval) -> dict:
    if not approval.approval_confirmed:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Explicit baseline approval is required.",
        )

    with closing(connect_database()) as connection:
        run = connection.execute(
            "SELECT * FROM discovery_runs WHERE id = ? AND site_id = ? AND status = 'COMPLETED'",
            (approval.discovery_run_id, site_id),
        ).fetchone()
        if run is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Completed discovery run not found.")
        existing = connection.execute(
            "SELECT id FROM baselines WHERE discovery_run_id = ?",
            (approval.discovery_run_id,),
        ).fetchone()
        if existing:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This discovery is already approved.")
        version = connection.execute(
            "SELECT COALESCE(MAX(version), 0) + 1 FROM baselines WHERE site_id = ?",
            (site_id,),
        ).fetchone()[0]
        baseline_id = str(uuid4())
        approved_at = datetime.now(UTC).isoformat()
        connection.execute(
            """
            INSERT INTO baselines (
                id, site_id, discovery_run_id, version, reviewer, approved_at, snapshot_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                baseline_id,
                site_id,
                approval.discovery_run_id,
                version,
                approval.reviewer,
                approved_at,
                run["inventory_json"],
            ),
        )
        connection.execute("UPDATE sites SET status = ? WHERE id = ?", ("HEALTHY", site_id))
        connection.commit()
    return {
        "id": baseline_id,
        "site_id": site_id,
        "version": version,
        "reviewer": approval.reviewer,
        "approved_at": approved_at,
        "pages": json.loads(run["inventory_json"]),
    }


@app.get("/api/sites/{site_id}/baselines", tags=["baselines"])
async def list_baselines(site_id: str) -> dict:
    with closing(connect_database()) as connection:
        rows = connection.execute(
            "SELECT * FROM baselines WHERE site_id = ? ORDER BY version DESC",
            (site_id,),
        ).fetchall()
    return {
        "baselines": [
            {
                "id": row["id"],
                "version": row["version"],
                "reviewer": row["reviewer"],
                "approved_at": row["approved_at"],
                "pages": json.loads(row["snapshot_json"]),
            }
            for row in rows
        ]
    }


def compare_to_baseline(expected_pages: list[dict], observed_pages: list[dict]) -> list[dict]:
    observed_by_url = {page["url"].rstrip("/"): page for page in observed_pages}
    results = []
    for expected in expected_pages:
        observed = observed_by_url.get(expected["url"].rstrip("/"))
        failures = []
        if observed is None:
            failures.append("Expected page was not discovered")
        else:
            if observed["status_code"] >= 400:
                failures.append(f"HTTP {observed['status_code']}")
            if observed["title"] != expected["title"]:
                failures.append("Page title changed")
            for key in ("links", "buttons"):
                missing = sorted(set(expected[key]) - set(observed[key]))
                if missing:
                    failures.append(f"Missing {key}: {', '.join(missing[:5])}")
            expected_forms = {(form["action"], form["method"], tuple(form["fields"])) for form in expected["forms"]}
            observed_forms = {(form["action"], form["method"], tuple(form["fields"])) for form in observed["forms"]}
            if expected_forms - observed_forms:
                failures.append("One or more expected forms changed or disappeared")
        results.append({"url": expected["url"], "status": "FAIL" if failures else "PASS", "failures": failures})
    return results


@app.post("/api/sites/{site_id}/runs", tags=["execution"])
async def run_approved_baseline(site_id: str) -> dict:
    with closing(connect_database()) as connection:
        site = connection.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone()
        baseline = connection.execute(
            "SELECT * FROM baselines WHERE site_id = ? ORDER BY version DESC LIMIT 1",
            (site_id,),
        ).fetchone()
        if site is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Site not found.")
        if baseline is None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="An approved baseline is required.")
        run_id = str(uuid4())
        started_at = datetime.now(UTC).isoformat()
        try:
            connection.execute(
                "INSERT INTO execution_runs (id, site_id, baseline_id, started_at, status, details_json) VALUES (?, ?, ?, ?, 'RUNNING', '[]')",
                (run_id, site_id, baseline["id"], started_at),
            )
            connection.execute("UPDATE sites SET status = 'TESTING' WHERE id = ?", (site_id,))
            connection.commit()
        except sqlite3.IntegrityError as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A run is already in progress.") from error

    boundary = DiscoveryBoundary(
        base_url=site["base_url"],
        allowed_path=site["allowed_path"],
        excluded_paths=tuple(item for item in site["excluded_paths"].split("\n") if item),
    )
    try:
        observed = await discover(boundary)
        details = compare_to_baseline(json.loads(baseline["snapshot_json"]), observed)
        passed = sum(item["status"] == "PASS" for item in details)
        failed = sum(item["status"] == "FAIL" for item in details)
        run_status = "PASS" if failed == 0 else "FAIL"
    except Exception as error:
        details = [{"url": site["base_url"], "status": "BLOCKED", "failures": [type(error).__name__]}]
        passed, failed, run_status = 0, 1, "BLOCKED"

    completed_at = datetime.now(UTC).isoformat()
    site_status = "HEALTHY" if run_status == "PASS" else "NEEDS ATTENTION"
    with closing(connect_database()) as connection:
        connection.execute(
            "UPDATE execution_runs SET completed_at = ?, status = ?, passed = ?, failed = ?, details_json = ? WHERE id = ?",
            (completed_at, run_status, passed, failed, json.dumps(details), run_id),
        )
        connection.execute("UPDATE sites SET status = ? WHERE id = ?", (site_status, site_id))
        connection.commit()
    return {"run_id": run_id, "status": run_status, "passed": passed, "failed": failed, "details": details}


@app.get("/api/sites/{site_id}/runs", tags=["execution"])
async def list_execution_runs(site_id: str) -> dict:
    with closing(connect_database()) as connection:
        rows = connection.execute(
            "SELECT * FROM execution_runs WHERE site_id = ? ORDER BY started_at DESC",
            (site_id,),
        ).fetchall()
    return {
        "runs": [
            {
                "id": row["id"],
                "baseline_id": row["baseline_id"],
                "started_at": row["started_at"],
                "completed_at": row["completed_at"],
                "status": row["status"],
                "passed": row["passed"],
                "failed": row["failed"],
                "details": json.loads(row["details_json"]),
            }
            for row in rows
        ]
    }


@app.get("/api/sites/{site_id}/schedule", tags=["schedules"])
async def get_schedule(site_id: str) -> dict:
    with closing(connect_database()) as connection:
        site = connection.execute("SELECT id FROM sites WHERE id = ?", (site_id,)).fetchone()
        row = connection.execute("SELECT * FROM schedules WHERE site_id = ?", (site_id,)).fetchone()
    if site is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Site not found.")
    if row is None:
        return {"site_id": site_id, "frequency": "daily", "enabled": False, "next_run_at": None}
    return {
        "site_id": site_id,
        "frequency": row["frequency"],
        "enabled": bool(row["enabled"]),
        "next_run_at": row["next_run_at"],
    }


@app.put("/api/sites/{site_id}/schedule", tags=["schedules"])
async def configure_schedule(site_id: str, schedule: ScheduleConfiguration) -> dict:
    if schedule.enabled and not schedule.approval_confirmed:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Explicit approval is required to enable automatic website requests.",
        )
    now = datetime.now(UTC)
    with closing(connect_database()) as connection:
        site = connection.execute("SELECT id FROM sites WHERE id = ?", (site_id,)).fetchone()
        baseline = connection.execute("SELECT id FROM baselines WHERE site_id = ? LIMIT 1", (site_id,)).fetchone()
        if site is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Site not found.")
        if schedule.enabled and baseline is None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="An approved baseline is required.")
        next_run_at = next_scheduled_time(schedule.frequency, now).isoformat() if schedule.enabled else None
        connection.execute(
            """
            INSERT INTO schedules (site_id, frequency, enabled, next_run_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(site_id) DO UPDATE SET
                frequency = excluded.frequency,
                enabled = excluded.enabled,
                next_run_at = excluded.next_run_at,
                updated_at = excluded.updated_at
            """,
            (site_id, schedule.frequency, int(schedule.enabled), next_run_at, now.isoformat()),
        )
        connection.commit()
    return {"site_id": site_id, "frequency": schedule.frequency, "enabled": schedule.enabled, "next_run_at": next_run_at}


@app.get("/api/sites/{site_id}/authentication", tags=["authentication"])
async def get_authentication_profile(site_id: str) -> dict:
    with closing(connect_database()) as connection:
        site = connection.execute("SELECT id FROM sites WHERE id = ?", (site_id,)).fetchone()
        row = connection.execute("SELECT * FROM authentication_profiles WHERE site_id = ?", (site_id,)).fetchone()
    if site is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Site not found.")
    if row is None:
        return {"configured": False, "execution_enabled": False}
    return {
        "configured": True,
        "login_path": row["login_path"],
        "username_env": row["username_env"],
        "password_env": row["password_env"],
        "execution_enabled": False,
    }


@app.put("/api/sites/{site_id}/authentication", tags=["authentication"])
async def configure_authentication_profile(site_id: str, profile: AuthenticationProfile) -> dict:
    if not profile.test_account_confirmed:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Confirm that the references belong to a dedicated limited-permission test account.",
        )
    if profile.username_env == profile.password_env:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Username and password must use different environment-variable names.",
        )
    now = datetime.now(UTC).isoformat()
    with closing(connect_database()) as connection:
        site = connection.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone()
        if site is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Site not found.")
        allowed = site["allowed_path"].rstrip("/") or "/"
        if allowed != "/" and profile.login_path != allowed and not profile.login_path.startswith(f"{allowed}/"):
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Login path is outside the allowed boundary.")
        connection.execute(
            """
            INSERT INTO authentication_profiles (
                site_id, login_path, username_env, password_env, execution_enabled, updated_at
            ) VALUES (?, ?, ?, ?, 0, ?)
            ON CONFLICT(site_id) DO UPDATE SET
                login_path = excluded.login_path,
                username_env = excluded.username_env,
                password_env = excluded.password_env,
                execution_enabled = 0,
                updated_at = excluded.updated_at
            """,
            (site_id, profile.login_path, profile.username_env, profile.password_env, now),
        )
        connection.commit()
    return {
        "configured": True,
        "login_path": profile.login_path,
        "username_env": profile.username_env,
        "password_env": profile.password_env,
        "execution_enabled": False,
    }


@app.get("/api/sites/{site_id}/login-journey", tags=["authentication"])
async def get_login_journey(site_id: str) -> dict:
    with closing(connect_database()) as connection:
        site = connection.execute("SELECT id FROM sites WHERE id = ?", (site_id,)).fetchone()
        row = connection.execute("SELECT * FROM login_journeys WHERE site_id = ?", (site_id,)).fetchone()
        interaction = connection.execute(
            "SELECT id, version FROM interaction_definitions WHERE site_id = ? AND interaction_type = 'login' ORDER BY version DESC LIMIT 1",
            (site_id,),
        ).fetchone()
    if site is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Site not found.")
    if row is None:
        return {"configured": False, "execution_enabled": False}
    return {
        "configured": True,
        "username_selector": row["username_selector"],
        "password_selector": row["password_selector"],
        "submit_selector": row["submit_selector"],
        "success_path": row["success_path"],
        "success_text": row["success_text"],
        "success_mode": row["success_mode"],
        "authenticated_shell_check": bool(row["authenticated_shell_check"]),
        "main_selector": row["main_selector"],
        "heading_selector": row["heading_selector"],
        "navigation_selector": row["navigation_selector"],
        "external_auth_url": row["external_auth_url"] or "",
        "external_followup_url": row["external_followup_url"] or "",
        "approved_at": row["approved_at"],
        "interaction_definition_id": interaction["id"] if interaction else None,
        "interaction_version": interaction["version"] if interaction else None,
        "execution_enabled": False,
    }


@app.put("/api/sites/{site_id}/login-journey", tags=["authentication"])
async def configure_login_journey(site_id: str, journey: LoginJourney) -> dict:
    if not journey.approval_confirmed:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Explicit approval of the deterministic login journey is required.",
        )
    with closing(connect_database()) as connection:
        site = connection.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone()
        profile = connection.execute("SELECT site_id FROM authentication_profiles WHERE site_id = ?", (site_id,)).fetchone()
        if site is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Site not found.")
        if profile is None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Authentication references must be configured first.")
        allowed = site["allowed_path"].rstrip("/") or "/"
        if allowed != "/" and journey.success_path != allowed and not journey.success_path.startswith(f"{allowed}/"):
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Success path is outside the allowed boundary.")
        approved_at = datetime.now(UTC).isoformat()
        definition_json = json.dumps(login_definition(journey), sort_keys=True)
        latest_interaction = connection.execute(
            "SELECT * FROM interaction_definitions WHERE site_id = ? AND interaction_type = 'login' ORDER BY version DESC LIMIT 1",
            (site_id,),
        ).fetchone()
        if latest_interaction is not None and latest_interaction["definition_json"] == definition_json:
            interaction_id = latest_interaction["id"]
            interaction_version = latest_interaction["version"]
        else:
            interaction_id = str(uuid4())
            interaction_version = (latest_interaction["version"] if latest_interaction else 0) + 1
            connection.execute(
                "INSERT INTO interaction_definitions (id, site_id, interaction_type, version, definition_json, approved_at, supersedes_id) VALUES (?, ?, 'login', ?, ?, ?, ?)",
                (
                    interaction_id,
                    site_id,
                    interaction_version,
                    definition_json,
                    approved_at,
                    latest_interaction["id"] if latest_interaction else None,
                ),
            )
        connection.execute(
            """
            INSERT INTO login_journeys (
                site_id, username_selector, password_selector, submit_selector,
                success_path, success_text, success_mode, authenticated_shell_check,
                main_selector, heading_selector, navigation_selector,
                external_auth_url, external_followup_url, approved_at, execution_enabled
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
            ON CONFLICT(site_id) DO UPDATE SET
                username_selector = excluded.username_selector,
                password_selector = excluded.password_selector,
                submit_selector = excluded.submit_selector,
                success_path = excluded.success_path,
                success_text = excluded.success_text,
                success_mode = excluded.success_mode,
                authenticated_shell_check = excluded.authenticated_shell_check,
                main_selector = excluded.main_selector,
                heading_selector = excluded.heading_selector,
                navigation_selector = excluded.navigation_selector,
                external_auth_url = excluded.external_auth_url,
                external_followup_url = excluded.external_followup_url,
                approved_at = excluded.approved_at,
                execution_enabled = 0
            """,
            (
                site_id,
                journey.username_selector,
                journey.password_selector,
                journey.submit_selector,
                journey.success_path,
                journey.success_text,
                journey.success_mode,
                int(journey.authenticated_shell_check),
                journey.main_selector,
                journey.heading_selector,
                journey.navigation_selector,
                str(journey.external_auth_url) if journey.external_auth_url else None,
                str(journey.external_followup_url) if journey.external_followup_url else None,
                approved_at,
            ),
        )
        connection.commit()
    return {
        "configured": True,
        "approved_at": approved_at,
        "execution_enabled": False,
        "success_path": journey.success_path,
        "success_text": journey.success_text,
        "success_mode": journey.success_mode,
        "authenticated_shell_check": journey.authenticated_shell_check,
        "main_selector": journey.main_selector,
        "heading_selector": journey.heading_selector,
        "navigation_selector": journey.navigation_selector,
        "external_auth_url": str(journey.external_auth_url) if journey.external_auth_url else "",
        "external_followup_url": str(journey.external_followup_url) if journey.external_followup_url else "",
        "interaction_definition_id": interaction_id,
        "interaction_version": interaction_version,
    }


@app.get("/api/sites/{site_id}/interactions", tags=["authentication"])
async def list_interaction_definitions(site_id: str) -> dict:
    with closing(connect_database()) as connection:
        site = connection.execute("SELECT id FROM sites WHERE id = ?", (site_id,)).fetchone()
        rows = connection.execute(
            """
            SELECT interaction_definitions.id, interaction_definitions.interaction_type,
                   interaction_definitions.version, interaction_definitions.approved_at,
                   interaction_definitions.supersedes_id, COUNT(authentication_runs.id) AS linked_run_count
            FROM interaction_definitions
            LEFT JOIN authentication_runs
              ON authentication_runs.interaction_definition_id = interaction_definitions.id
            WHERE interaction_definitions.site_id = ?
            GROUP BY interaction_definitions.id
            ORDER BY interaction_definitions.interaction_type, interaction_definitions.version DESC
            """,
            (site_id,),
        ).fetchall()
        legacy_run_count = connection.execute(
            "SELECT COUNT(*) FROM authentication_runs WHERE site_id = ? AND interaction_definition_id IS NULL",
            (site_id,),
        ).fetchone()[0]
    if site is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Site not found.")
    return {
        "interactions": [
            {
                "id": row["id"],
                "type": row["interaction_type"],
                "version": row["version"],
                "approved_at": row["approved_at"],
                "supersedes_id": row["supersedes_id"],
                "linked_run_count": row["linked_run_count"],
            }
            for row in rows
        ],
        "legacy_run_count": legacy_run_count,
    }


@app.post("/api/sites/{site_id}/login-test", tags=["authentication"])
async def run_login_test(site_id: str, request: LoginExecutionRequest) -> dict:
    if not request.execution_confirmed:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Explicit confirmation is required for each login attempt.")
    with closing(connect_database()) as connection:
        site = connection.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone()
        profile = connection.execute("SELECT * FROM authentication_profiles WHERE site_id = ?", (site_id,)).fetchone()
        interaction = connection.execute(
            "SELECT * FROM interaction_definitions WHERE site_id = ? AND interaction_type = 'login' ORDER BY version DESC LIMIT 1",
            (site_id,),
        ).fetchone()
    if site is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Site not found.")
    if profile is None or interaction is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Authentication references and an approved login definition are required.")
    journey = json.loads(interaction["definition_json"])

    username = os.environ.get(profile["username_env"])
    password = os.environ.get(profile["password_env"])
    if not username or not password:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Set {profile['username_env']} and {profile['password_env']} in the server environment before testing.",
        )

    approved = ApprovedLogin(
        base_url=site["base_url"],
        allowed_path=site["allowed_path"],
        excluded_paths=tuple(item for item in site["excluded_paths"].split("\n") if item),
        login_path=profile["login_path"],
        username_selector=journey["username_selector"],
        password_selector=journey["password_selector"],
        submit_selector=journey["submit_selector"],
        success_path=journey["success_path"],
        success_text=journey["success_text"],
        success_mode=journey["success_mode"],
        authenticated_shell_check=bool(journey["authenticated_shell_check"]),
        main_selector=journey["main_selector"],
        heading_selector=journey["heading_selector"],
        navigation_selector=journey["navigation_selector"],
        external_auth_url=journey["external_auth_url"],
        external_followup_url=journey["external_followup_url"],
    )
    run_id = str(uuid4())
    started_at = datetime.now(UTC).isoformat()
    try:
        evidence = await execute_approved_login(approved, username, password)
    except Exception as error:
        evidence = {"status": "BLOCKED", "failure": type(error).__name__}
    completed_at = datetime.now(UTC).isoformat()
    with closing(connect_database()) as connection:
        connection.execute(
            "INSERT INTO authentication_runs (id, site_id, started_at, completed_at, status, evidence_json, interaction_definition_id, interaction_version) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (run_id, site_id, started_at, completed_at, evidence["status"], json.dumps(evidence), interaction["id"], interaction["version"]),
        )
        connection.commit()
    return {"run_id": run_id, "interaction_definition_id": interaction["id"], "interaction_version": interaction["version"], **evidence}


@app.get("/api/sites/{site_id}/login-tests", tags=["authentication"])
async def list_login_tests(site_id: str) -> dict:
    with closing(connect_database()) as connection:
        rows = connection.execute(
            "SELECT * FROM authentication_runs WHERE site_id = ? ORDER BY started_at DESC",
            (site_id,),
        ).fetchall()
    return {
        "runs": [
            {
                "id": row["id"],
                "started_at": row["started_at"],
                "completed_at": row["completed_at"],
                "status": row["status"],
                "interaction_definition_id": row["interaction_definition_id"],
                "interaction_version": row["interaction_version"],
                "evidence": sanitize_evidence(json.loads(row["evidence_json"])),
            }
            for row in rows
        ]
    }
