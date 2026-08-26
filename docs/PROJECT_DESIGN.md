# Cloudsterr-AI-Site-Agent Product Design

## 1. Purpose

Cloudsterr-AI-Site-Agent continuously verifies whether monitored websites work from an end user's perspective. It goes beyond DNS, ports, HTTP status, and page-load checks by exercising approved user capabilities such as navigation, menus, search, forms, authentication, account functions, filters, sorting, pagination, downloads, authorized uploads, safe cart or checkout paths, API-backed interactions, confirmation messages, and expected page transitions.

The product question is:

> Can an actual user successfully use this website right now?

## 2. Authorization boundary

The system may monitor only customer-owned or customer-operated websites, or websites for which the customer has explicit authorization to conduct automated functional testing. It must not probe arbitrary third-party websites.

Consequential actions require explicit configuration of the test environment, test identity, permitted action, test data, expected side effect, cleanup, and approval. This includes purchases, payments, account deletion, password changes, publishing, messaging, creating production records, uploads, and submitting real applications or orders.

## 3. Core operating model

Every monitored site has a versioned Functional Baseline describing:

1. What the site contains.
2. What an end user can do.
3. How each interaction is performed.
4. What outcome is expected.
5. How success or failure is determined.

The monitoring cycle is:

> Discover -> Baseline -> Exercise -> Verify -> Record -> Report

AI may propose interpretations and tests, but a human must approve the baseline before it becomes authoritative. Execution outcomes must remain grounded in observable evidence.

## 4. Site registration

Initial site configuration should include:

- site name and base URL;
- environment, owner, and description;
- test frequency and maximum duration;
- authentication requirements and a credential reference, never embedded credential values;
- allowed boundaries and excluded URLs;
- allowed and prohibited interaction types; and
- test-data requirements.

A site without an approved baseline has status `BASELINE REQUIRED`.

## 5. Discovery and baseline

Discovery should inventory pages, links, navigation paths, menus, buttons, forms and fields, search, authentication, tabs, dropdowns, selection controls, filters, sorting, pagination, downloads, authorized uploads, dialogs, JavaScript behavior, dynamic content, and user workflows.

The initial release supports automated discovery followed by human review. Each approved change produces a new immutable baseline version while historical baselines remain available.

A baseline contains at least:

- baseline, site, and version identifiers;
- creation date, creator, and approval status;
- browser and environment;
- page, interaction, and workflow inventories;
- expected results and useful screenshots;
- test data and dependencies; and
- allowed and prohibited actions.

## 6. Structured functional tests

Each interaction becomes a structured test with starting state, ordered actions, expected outcome, failure conditions, timeout, evidence requirements, and continuation rules.

Representative tests include login and search. A login test may enter configured test credentials, submit the form, and require the dashboard and account identity to appear while the login page disappears. Failure modes include rejected authentication, timeout, wrong destination, missing dashboard, and blocking JavaScript errors.

## 7. Execution engine

Playwright is the proposed initial browser automation engine. For each approved interaction, the engine should:

1. Open the required page.
2. Confirm the starting state.
3. Perform the interaction.
4. Wait for the expected response.
5. Evaluate the outcome.
6. Capture evidence.
7. Record a result.
8. Continue according to workflow rules.

Initial result types are `PASS`, `FAIL`, `WARNING`, `SKIPPED`, and `BLOCKED`.

Every result records the site, run and baseline identifiers, test and interaction, timestamps, duration, expected and observed outcomes, error, failure screenshot, browser console evidence, relevant network errors, and execution log.

## 8. Monitoring runs

Every scheduled or manual site check receives a unique run identifier.

Normal lifecycle:

> QUEUED -> STARTING -> TESTING -> EVALUATING -> COMPLETED

Failure states include `NEEDS_ATTENTION`, `PARTIAL_FAILURE`, and `FAILED`.

Each run summarizes total, passed, failed, warning, skipped, duration, and baseline version. Simultaneous runs against the same site are prohibited unless explicitly configured. Interaction delays should be configurable to control load.

Initial schedules include manual, hourly, every X hours, daily, and weekly.

## 9. User interface

The initial dashboard should remain simple and answer:

> Which website needs my attention?

Primary site statuses are:

- `BASELINE REQUIRED`
- `BASELINE REVIEW`
- `HEALTHY`
- `TESTING`
- `NEEDS ATTENTION`
- `FAILED`
- `DISABLED`

The dashboard lists site, status, last check, pass and fail counts, and an action. A site detail view presents current status, last run, baseline, interactions, failures, run history, logs, configuration, and Run Now.

## 10. Evidence and history

The system maintains an interaction history. Failure detail should preserve screenshot, URL, test step, expected and observed outcomes, console and network errors, timing, relevant DOM evidence, and the previous successful execution.

Each run has an isolated evidence directory. The proposed local storage areas are:

```text
sites/
runs/
baselines/
screenshots/
logs/
```

Runtime evidence and credentials must be excluded from source control according to an approved data classification and retention policy.

## 11. AI role and baseline drift

AI may assist with discovery, proposed baseline tests, interface interpretation, failure categorization, historical comparison, incident summaries, likely causes, new-capability detection, and baseline-update recommendations.

AI does not replace deterministic browser execution or observable evidence.

A later drift capability should distinguish site failure from site change and report `POSSIBLE BASELINE CHANGE`. A reviewer may ignore, add to baseline, investigate, or mark the change expected.

## 12. Proposed initial architecture

- Frontend: plain HTML, CSS, and JavaScript
- Application API: FastAPI and Python
- Browser automation: Playwright
- Database: SQLite initially, with possible PostgreSQL support later
- Scheduler: application-level scheduler initially
- Storage: isolated local run directories

Initial entities:

- Site
- Baseline
- Workflow
- Test
- TestStep
- TestRun
- TestResult
- Evidence
- Schedule
- User

The model should support growth without requiring an immediate redesign, while Version 1 remains intentionally bounded.

## 13. Version 1 scope

Version 1 should support:

1. Register a public website.
2. Configure authorized testing boundaries.
3. Discover basic navigation and interactions.
4. Review the proposed baseline.
5. Store an approved baseline version.
6. Execute Playwright browser tests.
7. Exercise only approved interactions.
8. Record PASS or FAIL with supporting outcomes.
9. Capture screenshots for failures.
10. Preserve run history.
11. Display monitored sites and current health.
12. Run tests manually.
13. Run tests on an approved schedule.
14. Review detailed execution logs.

Email notification is explicitly excluded from Version 1.

## 14. Deferred capabilities

Deferred work includes email, SMS, Slack, Teams, external identity providers, multiple browsers, mobile simulation, geographic execution, performance baselines, API testing, visual regression, accessibility testing, security-safe checks, AI root-cause analysis, automatic drift detection, trends, SLA reporting, customer dashboards, multi-tenant SaaS, AWS hosting, and distributed workers.

Each deferred capability requires its own boundary, data model, security review, operational controls, acceptance criteria, and approval.

## 15. Product principle

Cloudsterr-AI-Site-Agent is not an uptime monitor.

Its fundamental cycle is:

> Establish Expected Behavior -> Interact Like a User -> Observe Actual Behavior -> Compare -> Preserve Evidence -> Report

