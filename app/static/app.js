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
      <td><div class="row-actions"><button class="discover-button" type="button">Discover</button><button class="review-button" type="button">Review</button><button class="run-button" type="button">Run now</button><button class="history-button" type="button">History</button></div></td>
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
    if (!response.ok) throw new Error(data.detail || "Discovery failed.");
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
    if (!response.ok) throw new Error(data.detail || "Discovery history could not be loaded.");
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
    if (!response.ok) throw new Error(data.detail || "Baseline approval failed.");
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
    if (!response.ok) throw new Error(data.detail || "Run failed.");
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
    if (!response.ok) throw new Error(data.detail || "Run history could not be loaded.");
    renderRunHistory(data.runs, button.dataset.siteName);
    message.textContent = `${data.runs.length} recorded run${data.runs.length === 1 ? "" : "s"} loaded.`;
  } catch (error) {
    message.classList.add("error");
    message.textContent = error.message;
  }
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
      const detail = typeof data.detail === "string" ? data.detail : "Check the highlighted fields.";
      throw new Error(detail);
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
});
baselineForm.addEventListener("submit", approveBaseline);
loadSites();
