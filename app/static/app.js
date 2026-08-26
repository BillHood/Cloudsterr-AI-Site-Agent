const rows = document.querySelector("#site-rows");
const message = document.querySelector("#dashboard-message");
const refreshButton = document.querySelector("#refresh-button");
const siteForm = document.querySelector("#site-form");
const formMessage = document.querySelector("#form-message");
const discoveryResults = document.querySelector("#discovery-results");
const discoveryPages = document.querySelector("#discovery-pages");

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
    const lastCheck = site.last_check ?? "Not yet run";

    row.innerHTML = `
      <td><span class="site-name"></span><span class="site-environment"></span></td>
      <td><span class="status-pill"></span></td>
      <td class="last-check"></td>
      <td class="passed"></td>
      <td class="failed"></td>
      <td><button class="discover-button" type="button">Discover</button></td>
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
    renderDiscovery(data.pages, button.dataset.siteName);
    message.textContent = `Discovery completed: ${data.page_count} permitted page${data.page_count === 1 ? "" : "s"} inventoried. No forms were submitted.`;
    await loadSites();
  } catch (error) {
    message.classList.add("error");
    message.textContent = error.message;
  } finally {
    button.disabled = false;
  }
}

function renderDiscovery(pages, siteName) {
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
  discoveryResults.hidden = false;
  discoveryResults.scrollIntoView({behavior: "smooth", block: "start"});
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
});
loadSites();
