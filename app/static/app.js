const rows = document.querySelector("#site-rows");
const message = document.querySelector("#dashboard-message");
const refreshButton = document.querySelector("#refresh-button");
const siteForm = document.querySelector("#site-form");
const formMessage = document.querySelector("#form-message");
const discoveryResults = document.querySelector("#discovery-results");
const discoveryPages = document.querySelector("#discovery-pages");
const baselineForm = document.querySelector("#baseline-form");
const baselineMessage = document.querySelector("#baseline-message");
const runHistory = document.querySelector("#run-history");
const runHistoryList = document.querySelector("#run-history-list");
const schedulePanel = document.querySelector("#schedule-panel");
const scheduleForm = document.querySelector("#schedule-form");
const scheduleMessage = document.querySelector("#schedule-message");
const authenticationPanel = document.querySelector("#authentication-panel");
const authenticationForm = document.querySelector("#authentication-form");
const authenticationMessage = document.querySelector("#authentication-message");
const loginJourneyForm = document.querySelector("#login-journey-form");
const loginJourneyMessage = document.querySelector("#login-journey-message");
const loginTestButton = document.querySelector("#login-test-button");
const loginTestMessage = document.querySelector("#login-test-message");
const loginTestEvidence = document.querySelector("#login-test-evidence");

function appendEvidenceDetail(parent, label, value) {
  const detail = document.createElement("p");
  detail.className = "evidence-detail";
  const strong = document.createElement("strong");
  strong.textContent = `${label}: `;
  detail.append(strong, document.createTextNode(value));
  parent.append(detail);
}

function renderLoginEvidence(runs) {
  loginTestEvidence.replaceChildren();
  if (runs.length === 0) {
    loginTestEvidence.textContent = "No login tests have been recorded.";
    return;
  }
  for (const run of runs.slice(0, 5)) {
    const card = document.createElement("article");
    card.className = `run-card result-${run.status.toLowerCase()}`;
    const heading = document.createElement("h4");
    heading.textContent = `${run.status} · ${run.evidence.outcome || "LEGACY RESULT"}`;
    card.append(heading);
    appendEvidenceDetail(card, "Time", new Date(run.completed_at).toLocaleString());
    appendEvidenceDetail(card, "Run ID", run.id);
    appendEvidenceDetail(card, "Interaction version", run.interaction_version ? `Login v${run.interaction_version}` : "Legacy run");
    appendEvidenceDetail(card, "Final path", run.evidence.final_url ? new URL(run.evidence.final_url).pathname : "Not reached");
    appendEvidenceDetail(card, "Approved responses", (run.evidence.auth_responses || []).map((item) => `${item.status} ${item.hostname}${item.path}`).join("; ") || "None");
    if (run.evidence.shell_checks) {
      appendEvidenceDetail(card, "Authenticated shell", Object.entries(run.evidence.shell_checks).map(([key, value]) => `${key.replaceAll("_", " ")}: ${value ? "yes" : "no"}`).join("; "));
    }
    appendEvidenceDetail(card, "Blocked dependencies", (run.evidence.blocked_requests || []).map((item) => `${item.method} ${item.hostname}${item.path}`).join("; ") || "None");
    loginTestEvidence.append(card);
  }
}

async function loadLoginEvidence(siteId) {
  const response = await fetch(`/api/sites/${siteId}/login-tests`, {headers: {Accept: "application/json"}});
  const data = await response.json();
  if (!response.ok) throw new Error(formatApiError(data, "Login evidence could not be loaded."));
  renderLoginEvidence(data.runs);
}

function formatApiError(data, fallback) {
  const detail = data?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((item) => {
        const field = Array.isArray(item.loc) ? item.loc.filter((part) => part !== "body").join(" → ") : "Input";
        const rawMessage = item.msg || "is invalid";
        const readableMessage = rawMessage.replace(/^Value error,\s*/i, "");
        return `${field || "Input"}: ${readableMessage}`;
      })
      .join("; ");
  }
  if (detail && typeof detail.message === "string") return detail.message;
  return fallback;
}

function renderSites(sites) {
  rows.replaceChildren();

  if (sites.length === 0) {
    const emptyRow = document.createElement("tr");
    const emptyCell = document.createElement("td");
    emptyCell.colSpan = 6;
    emptyCell.className = "empty-state";
    emptyCell.textContent = "No sites are registered yet.";
    emptyRow.append(emptyCell);
    rows.append(emptyRow);
    return;
  }

  for (const site of sites) {
    const row = document.createElement("tr");
    const lastCheck = site.last_check ? new Date(site.last_check).toLocaleString() : "Not yet run";

    row.innerHTML = `
      <td><span class="site-name"></span><span class="site-environment"></span></td>
      <td><span class="status-pill"></span></td>
      <td class="last-check"></td>
      <td class="passed"></td>
      <td class="failed"></td>
      <td><div class="row-actions"><button class="discover-button" type="button">Discover</button><button class="review-button" type="button">Review</button><button class="run-button" type="button">Run now</button><button class="history-button" type="button">History</button><button class="schedule-button" type="button">Schedule</button><button class="auth-button" type="button">Auth</button></div></td>
    `;

    row.querySelector(".site-name").textContent = site.name;
    row.querySelector(".site-environment").textContent = `${site.environment} · ${site.base_url}`;
    row.querySelector(".status-pill").textContent = site.status.replaceAll("_", " ");
    row.querySelector(".last-check").textContent = lastCheck;
    row.querySelector(".passed").textContent = site.passed;
    row.querySelector(".failed").textContent = site.failed;
    const discoverButton = row.querySelector(".discover-button");
    discoverButton.dataset.siteId = site.id;
    discoverButton.dataset.siteName = site.name;
    const reviewButton = row.querySelector(".review-button");
    reviewButton.dataset.siteId = site.id;
    reviewButton.dataset.siteName = site.name;
    reviewButton.disabled = site.status === "BASELINE REQUIRED";
    const runButton = row.querySelector(".run-button");
    runButton.dataset.siteId = site.id;
    runButton.dataset.siteName = site.name;
    runButton.disabled = !["HEALTHY", "NEEDS ATTENTION"].includes(site.status);
    const historyButton = row.querySelector(".history-button");
    historyButton.dataset.siteId = site.id;
    historyButton.dataset.siteName = site.name;
    historyButton.disabled = site.last_check === null;
    const scheduleButton = row.querySelector(".schedule-button");
    scheduleButton.dataset.siteId = site.id;
    scheduleButton.dataset.siteName = site.name;
    scheduleButton.disabled = !["HEALTHY", "NEEDS ATTENTION"].includes(site.status);
    const authButton = row.querySelector(".auth-button");
    authButton.dataset.siteId = site.id;
    authButton.dataset.siteName = site.name;
    rows.append(row);
  }
}

async function runDiscovery(button) {
  const approved = window.confirm(
    `Start read-only discovery for ${button.dataset.siteName}? Cloudsterr will make GET requests only within its saved boundary.`,
  );
  if (!approved) return;

  button.disabled = true;
  message.classList.remove("error");
  message.textContent = `Discovering ${button.dataset.siteName} within its approved boundary…`;
  try {
    const response = await fetch(`/api/sites/${button.dataset.siteId}/discover`, {
      method: "POST",
      headers: {Accept: "application/json"},
    });
    const data = await response.json();
    if (!response.ok) throw new Error(formatApiError(data, "Discovery failed."));
    renderDiscovery(data.pages, button.dataset.siteName, button.dataset.siteId, data.run_id);
    message.textContent = `Discovery completed: ${data.page_count} permitted page${data.page_count === 1 ? "" : "s"} inventoried. No forms were submitted.`;
    await loadSites();
  } catch (error) {
    message.classList.add("error");
    message.textContent = error.message;
  } finally {
    button.disabled = false;
  }
}

function renderDiscovery(pages, siteName, siteId, runId) {
  discoveryPages.replaceChildren();
  for (const page of pages) {
    const article = document.createElement("article");
    article.className = "inventory-card";
    const heading = document.createElement("h3");
    heading.textContent = page.title || "Untitled page";
    const url = document.createElement("p");
    url.className = "inventory-url";
    url.textContent = page.url;
    const summary = document.createElement("p");
    summary.textContent = `${page.links.length} links · ${page.buttons.length} buttons · ${page.forms.length} forms`;
    article.append(heading, url, summary);
    discoveryPages.append(article);
  }
  document.querySelector("#discovery-results-title").textContent = `${siteName} discovery inventory`;
  baselineForm.dataset.siteId = siteId;
  baselineForm.dataset.runId = runId;
  baselineForm.reset();
  baselineMessage.textContent = "";
  discoveryResults.hidden = false;
  discoveryResults.scrollIntoView({behavior: "smooth", block: "start"});
}

async function reviewDiscovery(button) {
  message.classList.remove("error");
  message.textContent = `Loading the latest ${button.dataset.siteName} discovery…`;
  try {
    const response = await fetch(`/api/sites/${button.dataset.siteId}/discoveries`, {headers: {Accept: "application/json"}});
    const data = await response.json();
    if (!response.ok) throw new Error(formatApiError(data, "Discovery history could not be loaded."));
    if (data.runs.length === 0) throw new Error("No completed discovery is available for review.");
    const latest = data.runs[0];
    renderDiscovery(latest.pages, button.dataset.siteName, button.dataset.siteId, latest.id);
    message.textContent = "Latest discovery loaded for human review.";
  } catch (error) {
    message.classList.add("error");
    message.textContent = error.message;
  }
}

async function approveBaseline(event) {
  event.preventDefault();
  const formData = new FormData(baselineForm);
  const approved = window.confirm("Create an immutable baseline from this discovery inventory?");
  if (!approved) return;
  const submitButton = baselineForm.querySelector("button[type='submit']");
  submitButton.disabled = true;
  baselineMessage.classList.remove("error");
  baselineMessage.textContent = "Creating the immutable baseline…";
  try {
    const response = await fetch(`/api/sites/${baselineForm.dataset.siteId}/baselines`, {
      method: "POST",
      headers: {"Content-Type": "application/json", Accept: "application/json"},
      body: JSON.stringify({
        discovery_run_id: baselineForm.dataset.runId,
        reviewer: formData.get("reviewer"),
        approval_confirmed: formData.get("approval_confirmed") === "on",
      }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(formatApiError(data, "Baseline approval failed."));
    baselineMessage.textContent = `Baseline version ${data.version} approved by ${data.reviewer}.`;
    await loadSites();
  } catch (error) {
    baselineMessage.classList.add("error");
    baselineMessage.textContent = error.message;
  } finally {
    submitButton.disabled = false;
  }
}

async function runBaseline(button) {
  const approved = window.confirm(`Run the approved read-only baseline for ${button.dataset.siteName}?`);
  if (!approved) return;
  button.disabled = true;
  message.classList.remove("error");
  message.textContent = `Running approved checks for ${button.dataset.siteName}…`;
  try {
    const response = await fetch(`/api/sites/${button.dataset.siteId}/runs`, {method: "POST", headers: {Accept: "application/json"}});
    const data = await response.json();
    if (!response.ok) throw new Error(formatApiError(data, "Run failed."));
    message.classList.toggle("error", data.failed > 0);
    message.textContent = `Run ${data.status}: ${data.passed} passed, ${data.failed} failed.`;
    await loadSites();
  } catch (error) {
    message.classList.add("error");
    message.textContent = error.message;
  } finally {
    button.disabled = false;
  }
}

function renderRunHistory(runs, siteName) {
  runHistoryList.replaceChildren();
  if (runs.length === 0) {
    const empty = document.createElement("p");
    empty.className = "empty-state";
    empty.textContent = "No baseline runs have been recorded.";
    runHistoryList.append(empty);
  }
  for (const run of runs) {
    const article = document.createElement("article");
    article.className = "run-card";
    const heading = document.createElement("h3");
    heading.textContent = `${run.status} · ${run.passed} passed · ${run.failed} failed`;
    const timing = document.createElement("p");
    const started = new Date(run.started_at);
    const completed = run.completed_at ? new Date(run.completed_at) : null;
    const duration = completed ? Math.max(0, completed - started) : null;
    timing.className = "run-timing";
    timing.textContent = `${started.toLocaleString()}${duration === null ? " · still running" : ` · ${(duration / 1000).toFixed(1)} seconds`}`;
    article.append(heading, timing);
    for (const result of run.details) {
      const detail = document.createElement("div");
      detail.className = `result-detail result-${result.status.toLowerCase()}`;
      const label = document.createElement("strong");
      label.textContent = `${result.status}: `;
      const text = document.createElement("span");
      text.textContent = `${result.url}${result.failures.length ? ` — ${result.failures.join("; ")}` : ""}`;
      detail.append(label, text);
      article.append(detail);
    }
    runHistoryList.append(article);
  }
  document.querySelector("#run-history-title").textContent = `${siteName} run history`;
  runHistory.hidden = false;
  runHistory.scrollIntoView({behavior: "smooth", block: "start"});
}

async function loadRunHistory(button) {
  message.classList.remove("error");
  message.textContent = `Loading ${button.dataset.siteName} run history…`;
  try {
    const response = await fetch(`/api/sites/${button.dataset.siteId}/runs`, {headers: {Accept: "application/json"}});
    const data = await response.json();
    if (!response.ok) throw new Error(formatApiError(data, "Run history could not be loaded."));
    renderRunHistory(data.runs, button.dataset.siteName);
    message.textContent = `${data.runs.length} recorded run${data.runs.length === 1 ? "" : "s"} loaded.`;
  } catch (error) {
    message.classList.add("error");
    message.textContent = error.message;
  }
}

async function openSchedule(button) {
  const response = await fetch(`/api/sites/${button.dataset.siteId}/schedule`, {headers: {Accept: "application/json"}});
  const data = await response.json();
  if (!response.ok) {
    message.classList.add("error");
    message.textContent = formatApiError(data, "Schedule could not be loaded.");
    return;
  }
  scheduleForm.dataset.siteId = button.dataset.siteId;
  scheduleForm.dataset.siteName = button.dataset.siteName;
  scheduleForm.elements.frequency.value = data.frequency;
  scheduleForm.elements.enabled.checked = data.enabled;
  scheduleForm.elements.approval_confirmed.checked = false;
  scheduleMessage.textContent = data.enabled && data.next_run_at ? `Next automatic run: ${new Date(data.next_run_at).toLocaleString()}` : "Automatic checks are disabled.";
  document.querySelector("#schedule-title").textContent = `${button.dataset.siteName} schedule`;
  schedulePanel.hidden = false;
  schedulePanel.scrollIntoView({behavior: "smooth", block: "start"});
}

async function saveSchedule(event) {
  event.preventDefault();
  const formData = new FormData(scheduleForm);
  const enabled = formData.get("enabled") === "on";
  const action = enabled ? "enable recurring website requests" : "disable future automatic requests";
  if (!window.confirm(`Save this schedule and ${action} for ${scheduleForm.dataset.siteName}?`)) return;
  const response = await fetch(`/api/sites/${scheduleForm.dataset.siteId}/schedule`, {
    method: "PUT",
    headers: {"Content-Type": "application/json", Accept: "application/json"},
    body: JSON.stringify({frequency: formData.get("frequency"), enabled, approval_confirmed: formData.get("approval_confirmed") === "on"}),
  });
  const data = await response.json();
  if (!response.ok) {
    scheduleMessage.classList.add("error");
    scheduleMessage.textContent = formatApiError(data, "Schedule could not be saved.");
    return;
  }
  scheduleMessage.classList.remove("error");
  scheduleMessage.textContent = data.enabled ? `Schedule enabled. Next run: ${new Date(data.next_run_at).toLocaleString()}` : "Schedule disabled. No automatic requests are planned.";
}

async function openAuthentication(button) {
  const response = await fetch(`/api/sites/${button.dataset.siteId}/authentication`, {headers: {Accept: "application/json"}});
  const data = await response.json();
  if (!response.ok) {
    message.classList.add("error");
    message.textContent = formatApiError(data, "Authentication references could not be loaded.");
    return;
  }
  authenticationForm.dataset.siteId = button.dataset.siteId;
  authenticationForm.dataset.siteName = button.dataset.siteName;
  authenticationForm.elements.login_path.value = data.login_path || "/login";
  authenticationForm.elements.username_env.value = data.username_env || "";
  authenticationForm.elements.password_env.value = data.password_env || "";
  authenticationForm.elements.test_account_confirmed.checked = false;
  authenticationMessage.textContent = data.configured ? "References are configured. Login execution is disabled." : "No authentication references are configured.";
  const journeyResponse = await fetch(`/api/sites/${button.dataset.siteId}/login-journey`, {headers: {Accept: "application/json"}});
  const journey = await journeyResponse.json();
  loginJourneyForm.dataset.siteId = button.dataset.siteId;
  loginJourneyForm.dataset.siteName = button.dataset.siteName;
  for (const name of ["username_selector", "password_selector", "submit_selector", "success_path", "success_text", "success_mode", "external_auth_url", "external_followup_url", "main_selector", "heading_selector", "navigation_selector"]) {
    loginJourneyForm.elements[name].value = journey[name] || "";
  }
  loginJourneyForm.elements.authenticated_shell_check.checked = Boolean(journey.authenticated_shell_check);
  loginJourneyForm.elements.approval_confirmed.checked = false;
  loginJourneyMessage.textContent = journey.configured ? `Login interaction v${journey.interaction_version} approved. Execution remains disabled.` : "No login definition is approved.";
  loginTestButton.dataset.siteId = button.dataset.siteId;
  loginTestButton.dataset.siteName = button.dataset.siteName;
  loginTestButton.disabled = !(data.configured && journey.configured);
  loginTestMessage.textContent = loginTestButton.disabled ? "Configure references and approve a login definition first." : "Ready for a manually confirmed login test. Credentials will be read from the server environment.";
  await loadLoginEvidence(button.dataset.siteId);
  document.querySelector("#authentication-title").textContent = `${button.dataset.siteName} authentication`;
  authenticationPanel.hidden = false;
  authenticationPanel.scrollIntoView({behavior: "smooth", block: "start"});
}

async function saveLoginJourney(event) {
  event.preventDefault();
  const formData = new FormData(loginJourneyForm);
  if (!window.confirm(`Approve this exact login definition for ${loginJourneyForm.dataset.siteName}? It will not be executed.`)) return;
  const payload = Object.fromEntries(formData.entries());
  payload.external_auth_url = payload.external_auth_url || null;
  payload.external_followup_url = payload.external_followup_url || null;
  payload.authenticated_shell_check = formData.get("authenticated_shell_check") === "on";
  payload.approval_confirmed = formData.get("approval_confirmed") === "on";
  const response = await fetch(`/api/sites/${loginJourneyForm.dataset.siteId}/login-journey`, {
    method: "PUT",
    headers: {"Content-Type": "application/json", Accept: "application/json"},
    body: JSON.stringify(payload),
  });
  const data = await response.json();
  if (!response.ok) {
    loginJourneyMessage.classList.add("error");
    loginJourneyMessage.textContent = formatApiError(data, "Login definition could not be saved.");
    return;
  }
  loginJourneyMessage.classList.remove("error");
  loginJourneyMessage.textContent = `Login interaction v${data.interaction_version} approved. Execution remains disabled.`;
}

async function runLoginTest() {
  if (!window.confirm(`Submit the approved login form once for ${loginTestButton.dataset.siteName}? This will transmit the configured test credentials to that site.`)) return;
  loginTestButton.disabled = true;
  loginTestMessage.classList.remove("error");
  loginTestMessage.textContent = "Running the approved login test…";
  try {
    const response = await fetch(`/api/sites/${loginTestButton.dataset.siteId}/login-test`, {
      method: "POST",
      headers: {"Content-Type": "application/json", Accept: "application/json"},
      body: JSON.stringify({execution_confirmed: true}),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(formatApiError(data, "Login test failed."));
    loginTestMessage.classList.toggle("error", data.status !== "PASS");
    loginTestMessage.textContent = `Login test ${data.status}. No credential values were stored or returned.`;
    await loadLoginEvidence(loginTestButton.dataset.siteId);
  } catch (error) {
    loginTestMessage.classList.add("error");
    loginTestMessage.textContent = error.message;
  } finally {
    loginTestButton.disabled = false;
  }
}

async function saveAuthentication(event) {
  event.preventDefault();
  const formData = new FormData(authenticationForm);
  if (!window.confirm(`Save non-secret test-account references for ${authenticationForm.dataset.siteName}?`)) return;
  const response = await fetch(`/api/sites/${authenticationForm.dataset.siteId}/authentication`, {
    method: "PUT",
    headers: {"Content-Type": "application/json", Accept: "application/json"},
    body: JSON.stringify({
      login_path: formData.get("login_path"),
      username_env: formData.get("username_env"),
      password_env: formData.get("password_env"),
      test_account_confirmed: formData.get("test_account_confirmed") === "on",
    }),
  });
  const data = await response.json();
  if (!response.ok) {
    authenticationMessage.classList.add("error");
    authenticationMessage.textContent = formatApiError(data, "References could not be saved.");
    return;
  }
  authenticationMessage.classList.remove("error");
  authenticationMessage.textContent = "Non-secret references saved. Login execution remains disabled.";
}

async function loadSites() {
  refreshButton.disabled = true;
  message.classList.remove("error");
  message.textContent = "Refreshing local site configurations…";

  try {
    const response = await fetch("/api/sites", {headers: {Accept: "application/json"}});
    if (!response.ok) throw new Error(`Local API returned ${response.status}`);
    const data = await response.json();
    renderSites(data.sites);
    message.textContent = `Local data refreshed at ${new Date().toLocaleTimeString()}.`;
  } catch (error) {
    rows.replaceChildren();
    message.classList.add("error");
    message.textContent = "Local data could not be loaded. Check that the server is running.";
    console.error("Dashboard refresh failed", error);
  } finally {
    refreshButton.disabled = false;
  }
}

async function registerSite(event) {
  event.preventDefault();
  const submitButton = siteForm.querySelector("button[type='submit']");
  const formData = new FormData(siteForm);
  const excludedPaths = String(formData.get("excluded_paths"))
    .split(",")
    .map((path) => path.trim())
    .filter(Boolean);
  const payload = {
    name: formData.get("name"),
    base_url: formData.get("base_url"),
    environment: formData.get("environment"),
    owner: formData.get("owner"),
    allowed_path: formData.get("allowed_path"),
    excluded_paths: excludedPaths,
    description: formData.get("description"),
    authorization_confirmed: formData.get("authorization_confirmed") === "on",
  };

  submitButton.disabled = true;
  formMessage.classList.remove("error");
  formMessage.textContent = "Saving local configuration…";

  try {
    const response = await fetch("/api/sites", {
      method: "POST",
      headers: {"Content-Type": "application/json", Accept: "application/json"},
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(formatApiError(data, "Check the highlighted fields."));
    }
    siteForm.reset();
    siteForm.elements.allowed_path.value = "/";
    formMessage.textContent = `${data.name} was registered locally. No external connection was made.`;
    await loadSites();
  } catch (error) {
    formMessage.classList.add("error");
    formMessage.textContent = error.message;
  } finally {
    submitButton.disabled = false;
  }
}

refreshButton.addEventListener("click", loadSites);
siteForm.addEventListener("submit", registerSite);
rows.addEventListener("click", (event) => {
  const button = event.target.closest(".discover-button");
  if (button) runDiscovery(button);
  const reviewButton = event.target.closest(".review-button");
  if (reviewButton) reviewDiscovery(reviewButton);
  const runButton = event.target.closest(".run-button");
  if (runButton) runBaseline(runButton);
  const historyButton = event.target.closest(".history-button");
  if (historyButton) loadRunHistory(historyButton);
  const scheduleButton = event.target.closest(".schedule-button");
  if (scheduleButton) openSchedule(scheduleButton);
  const authButton = event.target.closest(".auth-button");
  if (authButton) openAuthentication(authButton);
});
baselineForm.addEventListener("submit", approveBaseline);
scheduleForm.addEventListener("submit", saveSchedule);
authenticationForm.addEventListener("submit", saveAuthentication);
loginJourneyForm.addEventListener("submit", saveLoginJourney);
loginTestButton.addEventListener("click", runLoginTest);
loadSites();
