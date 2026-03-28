const state = {
  runs: [],
  selectedRunId: null,
  selectedArtifactPath: null,
  pollHandle: null,
  renderSignatures: {
    health: "",
    runs: "",
    thread: "",
    snapshot: "",
    artifacts: "",
  },
};

const stageOrder = ["planning", "acquisition", "extraction", "verification", "reporting"];
const stageLabels = {
  planning: "Source plan",
  acquisition: "Tinyfish fetch",
  extraction: "Claim extraction",
  verification: "Verification",
  reporting: "Report assembly",
};

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

function pluralize(count, singular, plural = `${singular}s`) {
  return count === 1 ? singular : plural;
}

function signatureFor(value) {
  return JSON.stringify(value ?? null);
}

function renderHealth(health) {
  const target = document.getElementById("health-pills");
  const signature = signatureFor({
    openai: health.openai_api_key_present,
    tinyfish: health.tinyfish_api_key_present,
    missing: health.missing_requirements || [],
    company_name_only_supported: health.company_name_only_supported,
  });
  if (state.renderSignatures.health === signature) {
    syncComposerAvailability(health);
    return;
  }
  state.renderSignatures.health = signature;
  const ready = health.openai_api_key_present && health.tinyfish_api_key_present;
  const pills = [
    `<span class="health-pill ${ready ? "ready" : ""}">${ready ? "Runtime ready" : "Keys missing"}</span>`,
    `<span class="health-pill muted">Financial workflow</span>`,
    `<span class="health-pill muted">${health.company_name_only_supported ? "Discovery beta on" : "Discovery unavailable"}</span>`,
  ];
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
  const signature = signatureFor({
    selectedRunId: state.selectedRunId,
    runs: state.runs.map((run) => ({
      run_id: run.run_id,
      company_name: run.company_name,
      status: run.status,
    })),
  });
  if (state.renderSignatures.runs === signature) {
    return;
  }
  state.renderSignatures.runs = signature;

  if (!state.runs.length) {
    target.innerHTML = `<p class="empty-state">Runs you start here will appear as reusable threads.</p>`;
    return;
  }

  target.innerHTML = state.runs.slice(0, 10).map((run) => `
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

function renderThreadHeader(run) {
  const title = document.getElementById("thread-title");
  const meta = document.getElementById("thread-meta");
  if (!run) {
    title.textContent = "Company intelligence in motion";
    meta.textContent = "Start a run to open the live research thread.";
    return;
  }

  title.textContent = `${run.company_name} financial thread`;
  meta.innerHTML = `
    ${statusPill(run.status)}
    <span>${escapeHtml(stageLabels[run.current_stage] || run.current_stage || "queued")}</span>
    <span>${escapeHtml(formatTime(run.updated_at))}</span>
  `;
}

function stageChips(run) {
  return stageOrder.map((key) => {
    const stage = run.stages[key];
    const detail = stage.total ? `${stage.current}/${stage.total}` : stage.status;
    return `
      <div class="stage-chip ${escapeHtml(stage.status)}">
        <strong>${escapeHtml(stage.label)}</strong>
        <span>${escapeHtml(detail)}</span>
      </div>
    `;
  }).join("");
}

function summarizeSourceJobs(sourceJobs) {
  const counts = { queued: 0, running: 0, completed: 0, failed: 0, retrying: 0 };
  sourceJobs.forEach((job) => {
    const key = (job.status || "queued").toLowerCase();
    counts[key] = (counts[key] || 0) + 1;
  });
  return counts;
}

function summarizeSpecialists(events) {
  const specialists = new Map();
  events
    .filter((event) => event.event === "specialist_update")
    .forEach((event) => {
      const specialist = event.specialist || "unknown_specialist";
      specialists.set(specialist, event);
    });
  return Array.from(specialists.values());
}

function summarizeVerification(events) {
  const updates = events.filter((event) => event.event === "verification_update");
  const counts = { supported: 0, insufficient_evidence: 0, contradicted: 0, unsupported: 0 };
  updates.forEach((event) => {
    const key = String(event.claim_status || "").toLowerCase();
    if (key) {
      counts[key] = (counts[key] || 0) + 1;
    }
  });
  return {
    latest: updates.length ? updates[updates.length - 1] : null,
    counts,
    total: updates.length,
  };
}

function buildSourceCards(sourceJobs) {
  return sourceJobs.map((job) => `
    <div class="detail-card">
      <div class="detail-row">
        <strong>${escapeHtml(job.label)}</strong>
        ${statusPill(job.status)}
      </div>
      <p class="quiet">${escapeHtml(job.source_type || job.category)}</p>
      ${job.message ? `<p>${escapeHtml(job.message)}</p>` : ""}
      <p class="url-text">${escapeHtml(job.url)}</p>
      ${job.streaming_url ? `<a class="source-link" href="${escapeHtml(job.streaming_url)}" target="_blank" rel="noreferrer">Open Tinyfish live preview</a>` : ""}
    </div>
  `).join("");
}

function buildSpecialistCards(specialists) {
  return specialists.map((event) => `
    <div class="detail-card">
      <div class="detail-row">
        <strong>${escapeHtml(event.specialist)}</strong>
        ${statusPill(event.status)}
      </div>
      <p>${escapeHtml(event.message || "")}</p>
      ${typeof event.claims_extracted === "number" ? `<p class="quiet">${escapeHtml(String(event.claims_extracted))} ${pluralize(event.claims_extracted, "claim")} extracted</p>` : ""}
    </div>
  `).join("");
}

function buildTraceList(events) {
  const recent = events
    .filter((event) => event.event !== "artifact_written")
    .slice(-8)
    .reverse();
  return recent.map((event) => `
    <div class="trace-row">
      <time>${escapeHtml(formatTime(event.timestamp))}</time>
      <p>${escapeHtml(event.message || event.event)}</p>
    </div>
  `).join("");
}

function buildReportMarkup(report) {
  const findings = (report.key_findings || []).map((item) => `<li>${escapeHtml(item)}</li>`).join("");
  const sections = (report.sections || []).map((section) => `
    <section class="report-block">
      <div class="detail-row">
        <strong>${escapeHtml(section.name)}</strong>
        ${statusPill(section.status)}
      </div>
      <p>${escapeHtml(section.summary)}</p>
      ${(section.highlights || []).length ? `<ul>${section.highlights.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>` : ""}
    </section>
  `).join("");
  const risks = (report.risk_matrix || []).map((item) => `<li>${escapeHtml(item.section)}: ${escapeHtml(item.issue)}</li>`).join("");

  return `
    <div class="report-stack">
      <section class="report-block">
        <h4>Executive summary</h4>
        <p>${escapeHtml(report.executive_summary || "No executive summary available.")}</p>
      </section>
      <section class="report-block">
        <h4>Scope and limitations</h4>
        <p>${escapeHtml(report.scope_and_limitations || "No scope statement available.")}</p>
      </section>
      <section class="report-block">
        <h4>Methodology</h4>
        <p>${escapeHtml(report.methodology || "No methodology available.")}</p>
      </section>
      <section class="report-block">
        <h4>Entity profile</h4>
        <p>${escapeHtml(report.entity_profile || "No entity profile available.")}</p>
      </section>
      <section class="report-block">
        <h4>Publish decision</h4>
        <p>${escapeHtml(report.publish_decision)}: ${escapeHtml(report.publish_reason)}</p>
      </section>
      <section class="report-block">
        <h4>Decision path</h4>
        <p>${escapeHtml(report.diligence_conclusion || "No decision-path narrative available.")}</p>
      </section>
      <section class="report-block">
        <h4>Key findings</h4>
        ${findings ? `<ul>${findings}</ul>` : `<p>No findings captured yet.</p>`}
      </section>
      <section class="report-block">
        <h4>Risk matrix</h4>
        ${risks ? `<ul>${risks}</ul>` : `<p>No material risks captured.</p>`}
      </section>
      ${sections}
    </div>
  `;
}

function messageMarkup({ role, label, title, body, meta = "", detail = "" }) {
  return `
    <article class="message ${escapeHtml(role)}">
      <div class="avatar">${escapeHtml(label.slice(0, 2).toUpperCase())}</div>
      <div class="bubble">
        <div class="message-topline">
          <p class="message-label">${escapeHtml(label)}</p>
          ${meta ? `<span class="quiet">${meta}</span>` : ""}
        </div>
        ${title ? `<h3>${escapeHtml(title)}</h3>` : ""}
        ${body ? `<p>${escapeHtml(body)}</p>` : ""}
        ${detail}
      </div>
    </article>
  `;
}

function renderThread(run) {
  const target = document.getElementById("thread");
  const signature = signatureFor(run || { empty: true });
  if (state.renderSignatures.thread === signature) {
    renderThreadHeader(run);
    return;
  }
  state.renderSignatures.thread = signature;
  renderThreadHeader(run);

  if (!run) {
    target.innerHTML = `
      <article class="message assistant intro">
        <div class="avatar">G</div>
        <div class="bubble">
          <p class="message-label">Orchestrator</p>
          <h3>Enter a company name to start the financial swarm.</h3>
          <p>
            The thread will show source discovery, Tinyfish acquisition,
            specialist extraction, verification, and report assembly as they happen.
          </p>
        </div>
      </article>
    `;
    return;
  }

  const sourceJobs = run.source_jobs || [];
  const sourceCounts = summarizeSourceJobs(sourceJobs);
  const specialists = summarizeSpecialists(run.events || []);
  const verification = summarizeVerification(run.events || []);
  const detailMessages = [];

  detailMessages.push(
    messageMarkup({
      role: "user",
      label: "You",
      title: run.company_name,
      body: "Build a source-backed financial diligence report from primary financial evidence.",
      meta: formatTime(run.created_at),
    })
  );

  detailMessages.push(
    messageMarkup({
      role: "assistant",
      label: "Orchestrator",
      title: `${run.company_name} is ${run.status}.`,
      body: run.error
        ? "The run hit an execution issue. The current thread still shows what was planned and what completed before the failure."
        : "The orchestration layer is coordinating discovery, acquisition, extraction, verification, and final compilation.",
      meta: run.openai_model || "",
      detail: `<div class="stage-strip">${stageChips(run)}</div>`,
    })
  );

  if (sourceJobs.length) {
    detailMessages.push(
      messageMarkup({
        role: "agent",
        label: "Discovery swarm",
        title: `${sourceJobs.length} source ${pluralize(sourceJobs.length, "agent")} planned for acquisition.`,
        body: `${sourceCounts.completed} completed, ${sourceCounts.running} running, ${sourceCounts.failed} failed, ${sourceCounts.retrying} retrying.`,
        detail: `<div class="detail-grid">${buildSourceCards(sourceJobs)}</div>`,
      })
    );
  }

  if (specialists.length) {
    const completedSpecialists = specialists.filter((item) => item.status === "completed").length;
    detailMessages.push(
      messageMarkup({
        role: "agent",
        label: "Financial specialists",
        title: `${completedSpecialists}/${specialists.length} specialist ${pluralize(specialists.length, "agent")} reported.`,
        body: "These specialists divide the work across document discovery, statement metrics, capital structure, guidance, and financial risk signals.",
        detail: `<div class="detail-grid">${buildSpecialistCards(specialists)}</div>`,
      })
    );
  }

  if (verification.latest || run.stages.verification.status !== "pending") {
    detailMessages.push(
      messageMarkup({
        role: "agent",
        label: "Verifier",
        title: verification.latest
          ? `${verification.latest.current}/${verification.latest.total} extracted claims checked.`
          : "Verification is queued behind extraction.",
        body: verification.total
          ? `${verification.counts.supported || 0} supported, ${verification.counts.insufficient_evidence || 0} insufficient evidence, ${verification.counts.unsupported || 0} unsupported, ${verification.counts.contradicted || 0} contradicted so far.`
          : run.stages.verification.message || "Waiting for extracted claims.",
      })
    );
  }

  if (run.final_report) {
    detailMessages.push(
      messageMarkup({
        role: "assistant",
        label: "Report compiler",
        title: "Verified financial output",
        body: "Only verified claims are being used to assemble this report.",
        detail: buildReportMarkup(run.final_report),
      })
    );
  }

  if (run.error) {
    detailMessages.push(
      messageMarkup({
        role: "error",
        label: "Runtime",
        title: "Run blocked",
        body: run.error,
      })
    );
  }

  if ((run.events || []).length) {
    detailMessages.push(
      messageMarkup({
        role: "trace",
        label: "Live trace",
        title: "Latest thread activity",
        body: "",
        detail: `<div class="trace-list">${buildTraceList(run.events || [])}</div>`,
      })
    );
  }

  target.innerHTML = detailMessages.join("");
}

function renderSnapshot(run) {
  const target = document.getElementById("run-snapshot");
  const signature = signatureFor(
    run
      ? {
          company_name: run.company_name,
          status: run.status,
          current_stage: run.current_stage,
          openai_model: run.openai_model,
          updated_at: run.updated_at,
          run_dir: run.run_dir,
        }
      : { empty: true }
  );
  if (state.renderSignatures.snapshot === signature) {
    return;
  }
  state.renderSignatures.snapshot = signature;

  if (!run) {
    target.innerHTML = "Select or launch a run to inspect the latest status.";
    return;
  }

  target.innerHTML = `
    <div class="snapshot-list">
      <div class="snapshot-row"><span>Company</span><strong>${escapeHtml(run.company_name)}</strong></div>
      <div class="snapshot-row"><span>Status</span>${statusPill(run.status)}</div>
      <div class="snapshot-row"><span>Current stage</span><strong>${escapeHtml(stageLabels[run.current_stage] || run.current_stage || "queued")}</strong></div>
      <div class="snapshot-row"><span>Model</span><strong>${escapeHtml(run.openai_model || "pending")}</strong></div>
      <div class="snapshot-row"><span>Updated</span><strong>${escapeHtml(formatTime(run.updated_at))}</strong></div>
      ${run.run_dir ? `<div class="snapshot-row"><span>Run dir</span><strong>${escapeHtml(run.run_dir)}</strong></div>` : ""}
    </div>
  `;
}

function renderArtifacts(run) {
  const artifactList = document.getElementById("artifact-list");
  const viewer = document.getElementById("artifact-viewer");
  const signature = signatureFor({
    run_id: run?.run_id || "",
    artifacts: run?.artifacts || [],
    selectedArtifactPath: state.selectedArtifactPath || "",
  });
  if (state.renderSignatures.artifacts === signature) {
    return;
  }
  state.renderSignatures.artifacts = signature;

  if (!run || !run.artifacts.length) {
    artifactList.innerHTML = `<p class="empty-state">Artifact files will appear after the run starts.</p>`;
    viewer.textContent = "Select an artifact to inspect it.";
    return;
  }

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

async function loadArtifact(runId, relativePath) {
  state.selectedArtifactPath = relativePath;
  const viewer = document.getElementById("artifact-viewer");
  viewer.textContent = "Loading artifact…";
  try {
    const payload = await api(`/api/runs/${runId}/artifact?path=${encodeURIComponent(relativePath)}`);
    viewer.textContent = JSON.stringify(payload.content, null, 2);
    const run = await api(`/api/runs/${runId}`);
    renderArtifacts(run);
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
    renderThread(null);
    renderSnapshot(null);
    renderArtifacts(null);
    return;
  }

  const run = await api(`/api/runs/${state.selectedRunId}`);
  renderThread(run);
  renderSnapshot(run);
  renderArtifacts(run);
}

function autosizeComposer() {
  const input = document.getElementById("company_name");
  input.style.height = "auto";
  input.style.height = `${Math.min(input.scrollHeight, 220)}px`;
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
    document.getElementById("thread").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) {
    alert(error.message);
  } finally {
    try {
      const health = await api("/api/health");
      syncComposerAvailability(health);
    } catch (_error) {
      button.disabled = false;
      button.textContent = "Run";
    }
  }
}

async function bootstrap() {
  const form = document.getElementById("run-form");
  const input = document.getElementById("company_name");
  form.addEventListener("submit", submitRun);
  input.addEventListener("input", autosizeComposer);
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      form.requestSubmit();
    }
  });
  autosizeComposer();

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
