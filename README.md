# Cloudsterr-AI-Site-Agent

Cloudsterr-AI-Site-Agent is a web application for continuously verifying whether authorized websites actually work from an end user's perspective.

It is not merely an uptime monitor. Its central question is:

> Can an actual user successfully use this website right now?

The product cycle is:

> Discover -> Baseline -> Exercise -> Verify -> Record -> Report

## Repository status

This repository currently contains the approved product design and repository foundation only. Application code, dependencies, database files, browser automation, scheduling, and deployment have not been created yet.

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

These are design recommendations, not yet installed or implemented decisions. Dependency versions, directory structure, authentication model, scheduler choice, and deployment target must be approved before scaffolding.

## Product design

See [docs/PROJECT_DESIGN.md](docs/PROJECT_DESIGN.md).

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE).

## Current development rule

Preserve the authorization boundary first. Deterministic browser evidence determines PASS, FAIL, WARNING, SKIPPED, or BLOCKED. AI may propose tests and interpret evidence, but it must not replace observable test results or expand authority.
