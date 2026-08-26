# Cloudsterr-AI-Site-Agent

Current application version: `v0.0.7`. Subsequent releases increment the patch number sequentially.

Cloudsterr-AI-Site-Agent is a web application for continuously verifying whether authorized websites actually work from an end user's perspective.

It is not merely an uptime monitor. Its central question is:

> Can an actual user successfully use this website right now?

The product cycle is:

> Discover -> Baseline -> Exercise -> Verify -> Record -> Report

## Repository status

Version 0.0.7 presents structured API validation errors as readable field-specific messages instead of JavaScript object strings. Authenticated execution and deployment have not been created yet.

## Safety boundary

Cloudsterr may monitor only websites owned or operated by the customer, or websites for which the customer has explicit authorization to perform automated functional testing.

Destructive or consequential actions require separately configured authorization, a safe test environment, and approved test identities. Examples include purchases, financial transactions, account deletion, password changes, publishing, messaging, production record creation, uploads, and real applications or orders.

## Initial technical direction

The proposed Version 1 architecture is:

- Frontend: plain HTML, CSS, and JavaScript
- API: Python with FastAPI
- Browser automation: Playwright
- Database: SQLite
- Scheduling: application-level scheduler
- Evidence storage: isolated local run directories

Version 0.0.6 adds bounded login-definition approval to the existing dashboard, discovery, baseline, execution, evidence, scheduling, and authentication-reference capabilities.

## Local startup

Requirements:

- Python 3.13
- A local virtual environment

From the repository root:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m playwright install chromium
python -m uvicorn app.main:app --host 127.0.0.1 --port 8127
```

Open <http://127.0.0.1:8127/>. The health check is available at <http://127.0.0.1:8127/api/health>.

The server must remain bound to `127.0.0.1`. Milestone 4 has no user authentication and is not approved for network exposure. Registered configuration, discovery results, reviewer names, and approved baselines are stored in the ignored `sites/cloudsterr.db` file. Never enter credentials or secrets into the application.

Stop the server with `Control-C` in its terminal.

Run the automated checks with:

```sh
python -m pytest
```

## Product design

See [docs/PROJECT_DESIGN.md](docs/PROJECT_DESIGN.md).

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE).

## Current development rule

Preserve the authorization boundary first. Deterministic browser evidence determines PASS, FAIL, WARNING, SKIPPED, or BLOCKED. AI may propose tests and interpret evidence, but it must not replace observable test results or expand authority.
