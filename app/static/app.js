const rows = document.querySelector("#site-rows");
const message = document.querySelector("#dashboard-message");
const refreshButton = document.querySelector("#refresh-button");

function renderSites(sites) {
  rows.replaceChildren();

  for (const site of sites) {
    const row = document.createElement("tr");
    const lastCheck = site.last_check ?? "Not yet run";

    row.innerHTML = `
      <td><span class="site-name"></span><span class="site-environment"></span></td>
      <td><span class="status-pill"></span></td>
      <td class="last-check"></td>
      <td class="passed"></td>
      <td class="failed"></td>
      <td><button class="disabled-button" type="button" disabled title="Available in a later milestone">Run now</button></td>
    `;

    row.querySelector(".site-name").textContent = site.name;
    row.querySelector(".site-environment").textContent = site.environment;
    row.querySelector(".status-pill").textContent = site.status.replaceAll("_", " ");
    row.querySelector(".last-check").textContent = lastCheck;
    row.querySelector(".passed").textContent = site.passed;
    row.querySelector(".failed").textContent = site.failed;
    rows.append(row);
  }
}

async function loadSites() {
  refreshButton.disabled = true;
  message.classList.remove("error");
  message.textContent = "Refreshing local demonstration data…";

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

refreshButton.addEventListener("click", loadSites);
loadSites();
