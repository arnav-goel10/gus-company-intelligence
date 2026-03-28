const state = {
  runs: [],
  selectedRunId: null,
  selectedArtifactPath: null,
  pollHandle: null,
};

const stageOrder = ["planning", "acquisition", "extraction", "verification", "reporting"];

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.error || `Request failed: ${response.status}`);
  }
  return payload;
}

function escapeHtml(value) {
  return String(value || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll("\"", "&quot;")
    .replaceAll("'", "&#39;");
}

function formatTime(value) {
  if (!value) return "";
  return new Date(value).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  });
}

function statusPill(status) {
  const safeStatus = (status || "pending").toLowerCase();
  return `<span class="status-pill ${safeStatus}">${escapeHtml(safeStatus)}</span>`;
}

function renderHealth(health) {
  const target = document.getElementById("health-pills");
  const ready = health.openai_api_key_present && health.tinyfish_api_key_present;
  const pills = [
    `<span class="health-pill ${ready ? "ready" : ""}">${ready ? "Runtime ready" : "Keys missing"}</span>`,
    `<span class="health-pill muted">Financial workflow</span>`,
  ];

  if (!health.company_name_only_supported) {
    pills.push(
      `<span class="health-pill muted">Discovery still being hardened</span>`
    );
  }

  target.innerHTML = pills.join("");
  syncComposerAvailability(health);
}

function syncComposerAvailability(health) {
  const button = document.getElementById("run-submit");
  const note = document.getElementById("composer-note");
  const missing = health.missing_requirements || [];
  const ready = missing.length === 0;

  button.disabled = !ready;
  button.textContent = ready ? "Run" : "Setup required";
  note.textContent = ready
    ? "Financial mode only for now."
    : `Add ${missing.join(" and ")} to enable live runs.`;
}

function renderRuns() {
  const target = document.getElementById("runs");
  if (!state.runs.length) {
    target.innerHTML = "";
    return;
  }

  target.innerHTML = state.runs.slice(0, 8).map((run) => `
    <button class="run-pill ${state.selectedRunId === run.run_id ? "active" : ""}" data-run-id="${escapeHtml(run.run_id)}" type="button">
      <span>${escapeHtml(run.company_name)}</span>
      ${statusPill(run.status)}
    </button>
  `).join("");

  target.querySelectorAll(".run-pill").forEach((node) => {
    node.addEventListener("click", async () => {
      state.selectedRunId = node.dataset.runId;
      state.selectedArtifactPath = null;
      renderRuns();
      await refreshSelectedRun();
    });
  });
}

function renderStages(run) {
  const target = document.getElementById("stage-rail");
  if (!run) {
    target.innerHTML = `<p class="empty-state">Launch a run to see planning, acquisition, extraction, verification, and reporting unfold.</p>`;
    return;
  }

  target.innerHTML = stageOrder.map((key) => {
    const stage = run.stages[key];
    const detail = stage.total ? `${stage.current}/${stage.total}` : stage.status;
    return `
      <div class="stage ${escapeHtml(stage.status)}">
        <div class="stage-topline">
          <strong>${escapeHtml(stage.label)}</strong>
          <span class="quiet">${escapeHtml(detail)}</span>
        </div>
        <p class="stage-message">${escapeHtml(stage.message || "Waiting…")}</p>
      </div>
    `;
  }).join("");
}

function renderSourceJobs(run) {
  const target = document.getElementById("source-jobs");
  if (!run || !run.source_jobs.length) {
    target.innerHTML = `<p class="empty-state">As sources are planned and fetched, they will show up here with live Tinyfish status.</p>`;
    return;
  }

  target.innerHTML = run.source_jobs.map((job) => `
    <div class="source-row">
      <strong>${escapeHtml(job.label)}</strong>
      <div class="run-meta">
        ${statusPill(job.status)}
        <span>${escapeHtml(job.source_type || job.category)}</span>
      </div>
      ${job.message ? `<div class="quiet">${escapeHtml(job.message)}</div>` : ""}
      ${job.url ? `<div class="quiet">${escapeHtml(job.url)}</div>` : ""}
      ${job.streaming_url ? `<div><a class="source-link" href="${escapeHtml(job.streaming_url)}" target="_blank" rel="noreferrer">Open Tinyfish live preview</a></div>` : ""}
    </div>
  `).join("");
}

function renderEvents(run) {
  const target = document.getElementById("events");
  if (!run || !run.events.length) {
    target.innerHTML = `<p class="empty-state">The system will narrate each step here as the run progresses.</p>`;
    return;
  }

  const recent = run.events.slice(-60).reverse();
  target.innerHTML = recent.map((event) => `
    <div class="event-item">
      <time>${escapeHtml(formatTime(event.timestamp))} · ${escapeHtml(event.stage || event.event)}</time>
      <div>${escapeHtml(event.message || event.event)}</div>
    </div>
  `).join("");
}

function renderReport(run) {
  const title = document.getElementById("run-title");
  const summary = document.getElementById("report-summary");
  const artifactList = document.getElementById("artifact-list");

  if (!run) {
    title.textContent = "Waiting for a company";
    summary.innerHTML = `<p class="empty-state">The verified report will appear here when the run finishes.</p>`;
    artifactList.innerHTML = `<p class="empty-state">Artifact files will appear after the run starts.</p>`;
    return;
  }

  title.textContent = `${run.company_name} · ${run.status}`;

  if (!run.final_report) {
    summary.innerHTML = `<p class="empty-state">The report is still assembling. You can watch the live stream while verification and synthesis finish.</p>`;
  } else {
    const report = run.final_report;
    const findings = (report.key_findings || []).map(
      (item) => `<li>${escapeHtml(item)}</li>`
    ).join("");
    const risks = (report.risk_matrix || []).map(
      (item) => `<li>${escapeHtml(item.section)}: ${escapeHtml(item.issue)}</li>`
    ).join("");
    const sections = (report.sections || []).map((section) => `
      <section class="report-section">
        <h4>${escapeHtml(section.name)}</h4>
        <p>${escapeHtml(section.summary)}</p>
        ${(section.highlights || []).length ? `<ul>${section.highlights.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>` : ""}
      </section>
    `).join("");

    summary.innerHTML = `
      <section class="report-section">
        <h4>Executive Summary</h4>
        <p>${escapeHtml(report.executive_summary || "No executive summary yet.")}</p>
      </section>
      <section class="report-section">
        <h4>Publish Decision</h4>
        <p>${escapeHtml(report.publish_decision)}: ${escapeHtml(report.publish_reason)}</p>
      </section>
      <section class="report-section">
        <h4>Key Findings</h4>
        ${findings ? `<ul>${findings}</ul>` : `<p>No findings captured yet.</p>`}
      </section>
      <section class="report-section">
        <h4>Risk Matrix</h4>
        ${risks ? `<ul>${risks}</ul>` : `<p>No material risks captured.</p>`}
      </section>
      ${sections}
    `;
  }

  if (!run.artifacts.length) {
    artifactList.innerHTML = `<p class="empty-state">Artifact files will appear here.</p>`;
  } else {
    artifactList.innerHTML = run.artifacts.map((artifact) => `
      <button class="artifact-button ${state.selectedArtifactPath === artifact.relative_path ? "active" : ""}" data-path="${escapeHtml(artifact.relative_path)}" type="button">
        <strong>${escapeHtml(artifact.relative_path)}</strong>
        <div class="quiet">${escapeHtml(artifact.run_dir || "")}</div>
      </button>
    `).join("");
    artifactList.querySelectorAll(".artifact-button").forEach((node) => {
      node.addEventListener("click", () => loadArtifact(run.run_id, node.dataset.path));
    });
  }
}

async function loadArtifact(runId, relativePath) {
  state.selectedArtifactPath = relativePath;
  const viewer = document.getElementById("artifact-viewer");
  viewer.textContent = "Loading artifact…";
  try {
    const payload = await api(`/api/runs/${runId}/artifact?path=${encodeURIComponent(relativePath)}`);
    viewer.textContent = JSON.stringify(payload.content, null, 2);
    const run = state.runs.find((item) => item.run_id === runId);
    if (run) {
      const full = await api(`/api/runs/${runId}`);
      renderReport(full);
    }
  } catch (error) {
    viewer.textContent = error.message;
  }
}

async function refreshRuns() {
  const payload = await api("/api/runs");
  state.runs = payload.runs;
  if (!state.selectedRunId && state.runs.length) {
    state.selectedRunId = state.runs[0].run_id;
  }
  renderRuns();
}

async function refreshSelectedRun() {
  if (!state.selectedRunId) {
    renderStages(null);
    renderSourceJobs(null);
    renderEvents(null);
    renderReport(null);
    return;
  }

  const run = await api(`/api/runs/${state.selectedRunId}`);
  renderStages(run);
  renderSourceJobs(run);
  renderEvents(run);
  renderReport(run);
  if (state.selectedArtifactPath) {
    const viewer = document.getElementById("artifact-viewer");
    if (viewer.textContent === "Select an artifact to inspect it.") {
      await loadArtifact(run.run_id, state.selectedArtifactPath);
    }
  }
}

async function submitRun(event) {
  event.preventDefault();
  const button = document.getElementById("run-submit");
  const input = document.getElementById("company_name");
  const companyName = input.value.trim();
  if (!companyName) {
    input.focus();
    return;
  }

  button.disabled = true;
  button.textContent = "Launching…";

  try {
    const health = await api("/api/health");
    if ((health.missing_requirements || []).length) {
      syncComposerAvailability(health);
      throw new Error(
        `Missing runtime configuration: ${(health.missing_requirements || []).join(", ")}`
      );
    }
    const response = await api("/api/runs", {
      method: "POST",
      body: JSON.stringify({
        company_name: companyName,
      }),
    });
    state.selectedRunId = response.run_id;
    state.selectedArtifactPath = null;
    await refreshRuns();
    await refreshSelectedRun();
  } catch (error) {
    alert(error.message);
  } finally {
    try {
      const health = await api("/api/health");
      syncComposerAvailability(health);
    } catch (error) {
      button.disabled = false;
      button.textContent = "Run";
    }
  }
}

async function bootstrap() {
  document.getElementById("run-form").addEventListener("submit", submitRun);
  const health = await api("/api/health");
  renderHealth(health);
  await refreshRuns();
  await refreshSelectedRun();
  state.pollHandle = window.setInterval(async () => {
    await refreshRuns();
    await refreshSelectedRun();
  }, 1200);
}

bootstrap().catch((error) => {
  document.getElementById("health-pills").textContent = error.message;
});
