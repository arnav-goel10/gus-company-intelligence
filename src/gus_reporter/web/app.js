const state = {
  runs: [],
  selectedRunId: null,
  selectedArtifactPath: null,
  pollHandle: null,
  previewSelections: {},
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

const domainLabels = {
  financials: "Financial",
  legal: "Legal",
  news: "News",
  company_overview: "Overview",
  industry: "Industry",
  leadership: "Leadership",
  employees: "Employees",
  risk_signals: "Risk signals",
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

/* Note: All innerHTML assignments in this file use only values that have been
   processed through escapeHtml() which replaces &, <, >, ", and ' with their
   HTML entity equivalents. No raw user input is ever inserted into the DOM
   without escaping. Status values, labels, URLs, and all other dynamic content
   pass through escapeHtml before being placed into markup strings. */

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
    `<span class="health-pill muted">Financial · Legal · News</span>`,
    `<span class="health-pill muted">${health.company_name_only_supported ? "Discovery on" : "Discovery off"}</span>`,
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
    ? "Financial · Legal · News"
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
    target.innerHTML = `<p class="empty-state">Runs you start will appear here.</p>`;
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
  const subtitle = document.getElementById("thread-subtitle");
  const meta = document.getElementById("thread-meta");
  if (!run) {
    title.textContent = "Intelligence thread";
    subtitle.textContent = "Launch a run to begin multi-domain source acquisition.";
    meta.innerHTML = "";
    return;
  }

  title.textContent = `${run.company_name}`;
  subtitle.textContent = "Due diligence thread — financial, legal, and news acquisition running in parallel.";
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

function summarizeSourceJobsByCategory(sourceJobs) {
  const categories = {};
  sourceJobs.forEach((job) => {
    const cat = job.category || "other";
    if (!categories[cat]) categories[cat] = [];
    categories[cat].push(job);
  });
  return categories;
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

function groupSpecialistsBySection(specialists) {
  const sections = {};
  specialists.forEach((event) => {
    const section = event.section || "other";
    if (!sections[section]) sections[section] = [];
    sections[section].push(event);
  });
  return sections;
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

function sourceJobKey(job) {
  return [job.label || "", job.url || "", job.run_id || ""].join("::");
}

function previewableSourceJobs(sourceJobs) {
  const seen = new Set();
  return sourceJobs.filter((job) => {
    const streamingUrl = String(job.streaming_url || "").trim();
    if (!streamingUrl || seen.has(streamingUrl)) {
      return false;
    }
    seen.add(streamingUrl);
    return true;
  });
}

function isTerminalSourceStatus(status) {
  return ["completed", "failed", "cancelled"].includes(String(status || "").toLowerCase());
}

function categoryBadge(category) {
  const cat = (category || "other").toLowerCase();
  const label = domainLabels[cat] || cat;
  return `<span class="category-badge ${escapeHtml(cat)}">${escapeHtml(label)}</span>`;
}

function buildSourceCards(sourceJobs) {
  return sourceJobs.map((job) => `
    <div class="detail-card">
      <div class="detail-row">
        <strong>${escapeHtml(job.label)}</strong>
        ${statusPill(job.status)}
      </div>
      <p class="quiet">${categoryBadge(job.category)} ${escapeHtml(job.source_type || "")}</p>
      ${job.message ? `<p class="quiet">${escapeHtml(job.message)}</p>` : ""}
      <p class="url-text">${escapeHtml(job.url)}</p>
      ${job.streaming_url ? `<a class="source-link" href="${escapeHtml(job.streaming_url)}" target="_blank" rel="noreferrer">Open Tinyfish preview</a>` : ""}
    </div>
  `).join("");
}

function buildGroupedSourceCards(sourceJobs) {
  const byCat = summarizeSourceJobsByCategory(sourceJobs);
  const order = ["financials", "legal", "news"];
  const allCats = [...order.filter((c) => byCat[c]), ...Object.keys(byCat).filter((c) => !order.includes(c))];
  return allCats.map((cat) => {
    const jobs = byCat[cat];
    const dotClass = cat === "financials" ? "financial" : cat === "legal" ? "legal" : cat === "news" ? "news" : "other";
    return `
      <div class="domain-group-header"><span class="domain-dot ${dotClass}"></span>${escapeHtml(domainLabels[cat] || cat)} sources (${escapeHtml(String(jobs.length))})</div>
      <div class="detail-grid">${buildSourceCards(jobs)}</div>
    `;
  }).join("");
}

function buildSpecialistCards(specialists) {
  return specialists.map((event) => `
    <div class="detail-card">
      <div class="detail-row">
        <strong>${escapeHtml(event.specialist)}</strong>
        ${statusPill(event.status)}
      </div>
      <p class="quiet">${escapeHtml(event.message || "")}</p>
      ${typeof event.claims_extracted === "number" ? `<p class="quiet">${escapeHtml(String(event.claims_extracted))} ${pluralize(event.claims_extracted, "claim")} extracted</p>` : ""}
    </div>
  `).join("");
}

function buildGroupedSpecialistCards(specialists) {
  const bySection = groupSpecialistsBySection(specialists);
  const order = ["financials", "legal", "news"];
  const allSections = [...order.filter((s) => bySection[s]), ...Object.keys(bySection).filter((s) => !order.includes(s))];
  return allSections.map((section) => {
    const items = bySection[section];
    const dotClass = section === "financials" ? "financial" : section === "legal" ? "legal" : section === "news" ? "news" : "other";
    return `
      <div class="domain-group-header"><span class="domain-dot ${dotClass}"></span>${escapeHtml(domainLabels[section] || section)} specialists (${escapeHtml(String(items.length))})</div>
      <div class="detail-grid">${buildSpecialistCards(items)}</div>
    `;
  }).join("");
}

function buildTraceList(events) {
  const compacted = [];
  events
    .filter((event) => event.event !== "artifact_written")
    .forEach((event) => {
      const last = compacted[compacted.length - 1];
      const eventKey = [
        event.event || "",
        event.message || "",
        event.label || "",
        event.stage || "",
      ].join("::");
      if (last && last._eventKey === eventKey) {
        last.repeat_count += 1;
        last.timestamp = event.timestamp;
        return;
      }
      compacted.push({
        ...event,
        _eventKey: eventKey,
        repeat_count: 1,
      });
    });

  const recent = compacted.slice(-8).reverse();
  return recent.map((event) => `
    <div class="trace-row">
      <time>${escapeHtml(formatTime(event.timestamp))}</time>
      <p>
        ${escapeHtml(event.message || event.event)}
        ${event.repeat_count > 1 ? `<span class="quiet">(${escapeHtml(String(event.repeat_count))}x)</span>` : ""}
      </p>
    </div>
  `).join("");
}

function buildTraceDisclosure(events) {
  const visibleEvents = (events || []).filter((event) => event.event !== "artifact_written");
  if (!visibleEvents.length) {
    return "";
  }
  const lastEvent = visibleEvents[visibleEvents.length - 1];
  return `
    <details class="trace-disclosure">
      <summary>
        <div class="trace-summary-copy">
          <strong>Thread activity</strong>
          <span>${escapeHtml(String(visibleEvents.length))} ${pluralize(visibleEvents.length, "update")}</span>
        </div>
        <div class="trace-summary-meta">
          <span>${escapeHtml(formatTime(lastEvent.timestamp))}</span>
          <span class="trace-summary-indicator" aria-hidden="true">+</span>
        </div>
      </summary>
      <div class="trace-list">${buildTraceList(visibleEvents)}</div>
    </details>
  `;
}

function buildPreviewDeck(runId, sourceJobs) {
  const previews = previewableSourceJobs(sourceJobs);
  if (!previews.length) {
    return "";
  }

  const prioritized = [
    ...previews.filter((job) => ["running", "retrying", "queued"].includes(String(job.status || "").toLowerCase())),
    ...previews.filter((job) => !["running", "retrying", "queued"].includes(String(job.status || "").toLowerCase())),
  ];
  const deduped = [];
  const seenKeys = new Set();
  prioritized.forEach((job) => {
    const key = sourceJobKey(job);
    if (!seenKeys.has(key)) {
      seenKeys.add(key);
      deduped.push(job);
    }
  });

  const selectedKey = state.previewSelections[runId];
  const selectedJob = deduped.find((job) => sourceJobKey(job) === selectedKey) || deduped[0];
  state.previewSelections[runId] = sourceJobKey(selectedJob);
  const selectedStatus = String(selectedJob.status || "").toLowerCase();
  const previewFinished = isTerminalSourceStatus(selectedStatus);
  const previewBody = selectedJob.message
    ? selectedJob.message
    : previewFinished
      ? "This Tinyfish run has ended. The last visible browser state is preserved here."
      : "Live browser capture is active for this source agent.";
  const previewTitle = previewFinished ? "Tinyfish Preview Ended" : "Tinyfish Live Preview";
  const previewLinkLabel = previewFinished ? "Open final preview" : "Open live preview";

  return `
    <section class="preview-deck">
      <div class="preview-head">
        <div>
          <p class="message-label">${escapeHtml(previewTitle)}</p>
          <div class="preview-selected-meta">
            ${statusPill(selectedJob.status)}
            ${categoryBadge(selectedJob.category)}
            ${selectedJob.source_type ? `<span class="preview-source-type">${escapeHtml(selectedJob.source_type)}</span>` : ""}
          </div>
          <p class="quiet">${escapeHtml(previewBody)}</p>
        </div>
        <a class="source-link" href="${escapeHtml(selectedJob.streaming_url)}" target="_blank" rel="noreferrer">${escapeHtml(previewLinkLabel)}</a>
      </div>
      <div class="preview-tabs">
        ${deduped.map((job) => `
          <button
            class="preview-tab ${sourceJobKey(job) === sourceJobKey(selectedJob) ? "active" : ""}"
            type="button"
            data-run-id="${escapeHtml(runId)}"
            data-preview-key="${escapeHtml(sourceJobKey(job))}"
          >
            <span>${escapeHtml(job.label)}</span>
            ${statusPill(job.status)}
          </button>
        `).join("")}
      </div>
      <div class="preview-frame-shell ${previewFinished ? "ended" : "live"}">
        <iframe
          class="preview-frame"
          src="${escapeHtml(selectedJob.streaming_url)}"
          title="${escapeHtml(selectedJob.label)} live preview"
          loading="lazy"
          referrerpolicy="no-referrer"
        ></iframe>
      </div>
      <div class="preview-footnote">
        <span>${escapeHtml(selectedJob.label)}</span>
        <span>${escapeHtml(previewFinished ? "Preview session finished." : "Preview session active.")}</span>
      </div>
      <p class="quiet">If the preview host blocks embedding, use the open link above.</p>
    </section>
  `;
}

function buildLegalStructuredMarkup(data) {
  if (!data) return "";
  let markup = "";
  const matters = data.active_matters || [];
  if (matters.length) {
    markup += `<div class="structured-block"><h5>Active matters</h5><table class="matters-table"><thead><tr><th>Type</th><th>Description</th><th>Jurisdiction</th><th>Status</th><th>Severity</th><th>Date</th></tr></thead><tbody>`;
    for (const m of matters) {
      markup += `<tr><td>${escapeHtml(m.matter_type)}</td><td>${escapeHtml(m.description)}</td><td>${escapeHtml(m.jurisdiction)}</td><td>${escapeHtml(m.status)}</td><td><span class="pill pill-${escapeHtml(m.severity)}">${escapeHtml(m.severity)}</span></td><td>${escapeHtml(m.date_filed_or_announced)}</td></tr>`;
    }
    markup += `</tbody></table></div>`;
  }
  const sanctions = data.sanctions_flags || [];
  if (sanctions.length) {
    markup += `<div class="structured-block"><h5>Sanctions flags</h5><ul>${sanctions.map((s) => `<li>${escapeHtml(s)}</li>`).join("")}</ul></div>`;
  }
  const regulatory = data.regulatory_exposure || [];
  if (regulatory.length) {
    markup += `<div class="structured-block"><h5>Regulatory exposure</h5><ul>${regulatory.map((r) => `<li>${escapeHtml(r)}</li>`).join("")}</ul></div>`;
  }
  const redFlags = data.red_flags || [];
  if (redFlags.length) {
    markup += `<div class="structured-block"><h5>Red flags</h5><ul>${redFlags.map((f) => `<li>${escapeHtml(f)}</li>`).join("")}</ul></div>`;
  }
  const gaps = data.data_gaps || [];
  if (gaps.length) {
    markup += `<div class="structured-block"><h5>Data gaps</h5><ul>${gaps.map((g) => `<li>${escapeHtml(g)}</li>`).join("")}</ul></div>`;
  }
  return markup;
}

function buildNewsStructuredMarkup(data) {
  if (!data) return "";
  let markup = "";
  const events = data.material_events || [];
  if (events.length) {
    markup += `<div class="structured-block"><h5>Material events</h5><div class="event-timeline">`;
    for (const e of events) {
      const sentimentClass = e.sentiment === "positive" ? "pill-positive" : e.sentiment === "negative" ? "pill-negative" : "pill-neutral";
      markup += `<div class="event-item"><span class="event-date">${escapeHtml(e.date)}</span><span class="event-headline">${escapeHtml(e.headline)}</span><span class="event-meta">${escapeHtml(e.source_publisher)} <span class="pill ${sentimentClass}">${escapeHtml(e.sentiment)}</span></span></div>`;
    }
    markup += `</div></div>`;
  }
  const sentiment = data.coverage_sentiment || "";
  const density = data.coverage_density || "";
  if (sentiment || density) {
    markup += `<div class="structured-block"><h5>Coverage overview</h5><div class="coverage-pills">`;
    if (sentiment) {
      const sClass = sentiment === "net_positive" ? "pill-positive" : sentiment === "net_negative" ? "pill-negative" : "pill-neutral";
      markup += `<span>Sentiment: <span class="pill ${sClass}">${escapeHtml(sentiment.replace("_", " "))}</span></span>`;
    }
    if (density) {
      markup += `<span>Density: <span class="pill pill-density-${escapeHtml(density)}">${escapeHtml(density)}</span></span>`;
    }
    markup += `</div></div>`;
  }
  const negative = data.negative_signals || [];
  if (negative.length) {
    markup += `<div class="structured-block"><h5>Negative signals</h5><ul>${negative.map((n) => `<li>${escapeHtml(n)}</li>`).join("")}</ul></div>`;
  }
  const gaps = data.data_gaps || [];
  if (gaps.length) {
    markup += `<div class="structured-block"><h5>Data gaps</h5><ul>${gaps.map((g) => `<li>${escapeHtml(g)}</li>`).join("")}</ul></div>`;
  }
  return markup;
}

function buildSectionStructuredMarkup(section) {
  const sd = section.structured_data;
  if (!sd) return "";
  if (section.name === "legal") return buildLegalStructuredMarkup(sd);
  if (section.name === "news") return buildNewsStructuredMarkup(sd);
  if (section.name === "financials") {
    let markup = "";
    const metrics = sd.key_metrics || [];
    if (metrics.length) {
      markup += `<div class="structured-block"><h5>Key metrics</h5><table class="matters-table"><thead><tr><th>Metric</th><th>Value</th><th>Period</th></tr></thead><tbody>`;
      for (const m of metrics) {
        markup += `<tr><td>${escapeHtml(m.metric)}</td><td>${escapeHtml(m.value)}</td><td>${escapeHtml(m.period)}</td></tr>`;
      }
      markup += `</tbody></table></div>`;
    }
    const redFlags = sd.red_flags || [];
    if (redFlags.length) {
      markup += `<div class="structured-block"><h5>Red flags</h5><ul>${redFlags.map((f) => `<li>${escapeHtml(f)}</li>`).join("")}</ul></div>`;
    }
    const gaps = sd.data_gaps || [];
    if (gaps.length) {
      markup += `<div class="structured-block"><h5>Data gaps</h5><ul>${gaps.map((g) => `<li>${escapeHtml(g)}</li>`).join("")}</ul></div>`;
    }
    return markup;
  }
  return "";
}

function buildReportMarkup(report) {
  const findings = (report.key_findings || []).map((item) => `<li>${escapeHtml(item)}</li>`).join("");
  const sections = (report.sections || []).map((section) => `
    <section class="report-block">
      <div class="detail-row">
        <strong>${categoryBadge(section.name)} ${escapeHtml(domainLabels[section.name] || section.name)}</strong>
        ${statusPill(section.status)}
      </div>
      <p>${escapeHtml(section.summary)}</p>
      ${(section.highlights || []).length ? `<ul>${section.highlights.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>` : ""}
      ${buildSectionStructuredMarkup(section)}
    </section>
  `).join("");
  const risks = (report.risk_matrix || []).map((item) => `<li><span class="pill pill-${escapeHtml(item.severity || "medium")}">${escapeHtml(item.severity || "medium")}</span> ${escapeHtml(item.section)}: ${escapeHtml(item.issue)}</li>`).join("");

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
  const signature = signatureFor(
    run
      ? {
          run,
          selectedPreview: state.previewSelections[run.run_id] || "",
        }
      : { empty: true }
  );
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
          <h3>Enter a company to start the diligence swarm.</h3>
          <p>
            GUS runs financial, legal, and news source discovery in parallel,
            then extracts and cross-verifies claims before assembling an
            analyst-grade report.
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
      body: "Build a multi-domain due diligence report from primary financial, legal, and news evidence.",
      meta: formatTime(run.created_at),
    })
  );

  detailMessages.push(
    messageMarkup({
      role: "assistant",
      label: "Orchestrator",
      title: `${run.company_name} — ${run.status}`,
      body: run.error
        ? "The run hit an execution issue. The thread shows what was planned and completed before failure."
        : "Coordinating parallel discovery across financial, legal, and news domains.",
      meta: run.openai_model || "",
      detail: `<div class="stage-strip">${stageChips(run)}</div>`,
    })
  );

  if (sourceJobs.length) {
    const catSummary = summarizeSourceJobsByCategory(sourceJobs);
    const catCounts = Object.entries(catSummary).map(([cat, jobs]) => `${jobs.length} ${domainLabels[cat] || cat}`).join(", ");
    detailMessages.push(
      messageMarkup({
        role: "agent",
        label: "Discovery swarm",
        title: `${sourceJobs.length} source ${pluralize(sourceJobs.length, "agent")} across domains`,
        body: `${catCounts}. ${sourceCounts.completed} completed, ${sourceCounts.running} running, ${sourceCounts.failed} failed.`,
        detail: `
          ${buildPreviewDeck(run.run_id, sourceJobs)}
          ${buildGroupedSourceCards(sourceJobs)}
        `,
      })
    );
  }

  if (specialists.length) {
    const completedSpecialists = specialists.filter((item) => item.status === "completed").length;
    detailMessages.push(
      messageMarkup({
        role: "agent",
        label: "Extraction specialists",
        title: `${completedSpecialists}/${specialists.length} specialist ${pluralize(specialists.length, "agent")} reported`,
        body: "Domain-specific specialists extract structured claims from acquired evidence.",
        detail: buildGroupedSpecialistCards(specialists),
      })
    );
  }

  if (verification.latest || run.stages.verification.status !== "pending") {
    detailMessages.push(
      messageMarkup({
        role: "agent",
        label: "Verifier",
        title: verification.latest
          ? `${verification.latest.current}/${verification.latest.total} claims checked`
          : "Verification queued behind extraction",
        body: verification.total
          ? `${verification.counts.supported || 0} supported, ${verification.counts.insufficient_evidence || 0} insufficient, ${verification.counts.unsupported || 0} unsupported, ${verification.counts.contradicted || 0} contradicted.`
          : run.stages.verification.message || "Waiting for extracted claims.",
      })
    );
  }

  if (run.final_report) {
    detailMessages.push(
      messageMarkup({
        role: "assistant",
        label: "Report compiler",
        title: "Verified diligence output",
        body: "Only cross-verified claims are used in this report.",
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
        title: "Live trace",
        body: "Background runtime events are tucked away unless you inspect them.",
        detail: buildTraceDisclosure(run.events || []),
      })
    );
  }

  target.innerHTML = detailMessages.join("");
  target.querySelectorAll(".preview-tab").forEach((node) => {
    node.addEventListener("click", async () => {
      state.previewSelections[node.dataset.runId] = node.dataset.previewKey;
      await refreshSelectedRun();
    });
  });
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
    target.innerHTML = "Select or launch a run to inspect status.";
    return;
  }

  target.innerHTML = `
    <div class="snapshot-list">
      <div class="snapshot-row"><span>Company</span><strong>${escapeHtml(run.company_name)}</strong></div>
      <div class="snapshot-row"><span>Status</span>${statusPill(run.status)}</div>
      <div class="snapshot-row"><span>Stage</span><strong>${escapeHtml(stageLabels[run.current_stage] || run.current_stage || "queued")}</strong></div>
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
    artifactList.innerHTML = `<p class="empty-state">Artifacts appear after the run starts.</p>`;
    viewer.textContent = "Select an artifact to inspect.";
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
  input.style.height = `${Math.min(input.scrollHeight, 200)}px`;
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
