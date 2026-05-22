const currency = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });
const number = new Intl.NumberFormat("en-US");

let payload = null;
let selectedDomain = "All";
let selectedGate = "All";

const el = (tag, className, html) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (html !== undefined) node.innerHTML = html;
  return node;
};

const riskClass = (value) => value.toLowerCase().replaceAll(" ", "-");

const rowsForFilters = () => payload.priorityQueue.filter((row) => {
  const domainMatch = selectedDomain === "All" || row.business_domain === selectedDomain;
  const gateMatch = selectedGate === "All" || row.gate_status === selectedGate;
  return domainMatch && gateMatch;
});

const average = (items, field) => {
  if (!items.length) return 0;
  return Math.round(items.reduce((sum, item) => sum + Number(item[field]), 0) / items.length);
};

const countWhere = (items, test) => items.filter(test).length;

const renderMetrics = () => {
  const rows = rowsForFilters();
  const ready = countWhere(rows, (row) => row.gate_status === "Sign-off ready");
  const blocked = countWhere(rows, (row) => row.gate_status === "Needs source fix");
  const decisions = countWhere(rows, (row) => row.gate_status === "Needs mapping decision");
  const findings = rows.reduce((sum, row) => sum + Number(row.open_findings), 0);

  document.querySelector("#heroScore").textContent = `${payload.summary.avg_readiness}%`;
  document.querySelector("#heroSub").textContent = `${payload.summary.signoff_ready} ready, ${payload.summary.blocked} source fixes`;

  document.querySelector("#metrics").replaceChildren(
    el("article", "metric", `<span>Filtered mappings</span><strong>${rows.length}</strong><small>${selectedDomain} scope</small>`),
    el("article", "metric", `<span>Sign-off ready</span><strong>${ready}</strong><small>${average(rows, "readiness_score")}% avg readiness</small>`),
    el("article", "metric", `<span>Blocked or decision</span><strong>${blocked + decisions}</strong><small>${blocked} source fixes</small>`),
    el("article", "metric", `<span>Open findings</span><strong>${findings}</strong><small>${payload.summary.open_incidents} active incidents</small>`)
  );
};

const renderFilters = () => {
  const domains = ["All", ...new Set(payload.priorityQueue.map((row) => row.business_domain))];
  const gates = ["All", "Sign-off ready", "Watch", "Needs mapping decision", "Needs source fix"];

  const domainButtons = domains.map((domain) => {
    const button = el("button", domain === selectedDomain ? "chip active" : "chip", domain);
    button.type = "button";
    button.addEventListener("click", () => {
      selectedDomain = domain;
      render();
    });
    return button;
  });

  const gateButtons = gates.map((gate) => {
    const button = el("button", gate === selectedGate ? "chip active" : "chip", gate);
    button.type = "button";
    button.addEventListener("click", () => {
      selectedGate = gate;
      render();
    });
    return button;
  });

  document.querySelector("#domainFilters").replaceChildren(...domainButtons);
  document.querySelector("#gateFilters").replaceChildren(...gateButtons);
};

const renderDomainPulse = () => {
  document.querySelector("#domainPulse").replaceChildren(...payload.domains.map((domain) => el(
    "div",
    "domain-row",
    `<div>
      <strong>${domain.business_domain}</strong>
      <span>${domain.ready} ready, ${domain.blocked} blocked, ${domain.open_findings} findings</span>
    </div>
    <div class="bar"><i style="width:${domain.avg_readiness}%"></i></div>
    <b>${domain.avg_readiness}%</b>`
  )));
};

const renderQueue = () => {
  const rows = rowsForFilters().slice(0, 12);
  document.querySelector("#priorityQueue").innerHTML = `
    <table>
      <thead>
        <tr>
          <th>Mapping</th>
          <th>Source to target</th>
          <th>Gate</th>
          <th>Recon</th>
          <th>Next step</th>
        </tr>
      </thead>
      <tbody>
        ${rows.map((row) => `
          <tr>
            <td><strong>${row.mapping_id}</strong><small>${row.business_domain} | ${row.criticality}</small></td>
            <td>${row.source_field} <span class="arrow">to</span> ${row.target_field}<small>${row.mapping_complexity}</small></td>
            <td><b class="badge ${riskClass(row.gate_status)}">${row.gate_status}</b></td>
            <td>${row.recon_reconciliation_score}%<small>${row.open_findings} findings</small></td>
            <td>${row.recommended_next_step}</td>
          </tr>
        `).join("")}
      </tbody>
    </table>
  `;
};

const renderSttm = () => {
  const candidates = rowsForFilters()
    .slice()
    .sort((a, b) => Number(a.readiness_score) - Number(b.readiness_score))
    .slice(0, 6);
  document.querySelector("#sttmCards").replaceChildren(...candidates.map((row) => el(
    "article",
    "sttm-card",
    `<div class="card-top">
      <b>${row.google_sheet_tab}</b>
      <span>${row.signoff_status}</span>
    </div>
    <h3>${row.source_table}.${row.source_field}</h3>
    <p>${row.target_object}.${row.target_field}</p>
    <dl>
      <div><dt>Rule</dt><dd>${row.transformation_rule}</dd></div>
      <div><dt>Lineage</dt><dd>${row.lineage_path}</dd></div>
      <div><dt>Owner</dt><dd>${row.owner_group}</dd></div>
    </dl>`
  )));
};

const renderReconciliation = () => {
  const rows = rowsForFilters()
    .slice()
    .sort((a, b) => Number(a.recon_reconciliation_score) - Number(b.recon_reconciliation_score))
    .slice(0, 8);

  document.querySelector("#reconGrid").replaceChildren(...rows.map((row) => {
    const amount = Number(row.recon_amount_variance_usd);
    return el(
      "article",
      "recon-card",
      `<div class="card-top">
        <b>${row.mapping_id}</b>
        <span class="${riskClass(row.recon_recon_status)}">${row.recon_recon_status}</span>
      </div>
      <h3>${row.source_field} to ${row.target_field}</h3>
      <div class="split">
        <span>Load-ready rows<strong>${number.format(row.recon_load_ready_rows)}</strong></span>
        <span>Target rows<strong>${number.format(row.recon_target_rows)}</strong></span>
      </div>
      <div class="check-list">
        <span>Row delta <b>${number.format(row.recon_row_count_delta)}</b></span>
        <span>Matched keys <b>${row.recon_matched_keys_pct}%</b></span>
        <span>Value match <b>${row.recon_value_match_pct}%</b></span>
        <span>Amount variance <b>${currency.format(amount)}</b></span>
      </div>
      <p>${row.recon_blocker_reason}</p>`
    );
  }));
};

const renderIncidents = () => {
  const mappingIds = new Set(rowsForFilters().map((row) => row.mapping_id));
  let incidents = payload.incidents
    .filter((incident) => mappingIds.has(incident.mapping_id))
    .sort((a, b) => Number(b.aging_days) - Number(a.aging_days))
    .slice(0, 8);
  if (incidents.length < 4) {
    incidents = payload.incidents
      .slice()
      .sort((a, b) => Number(b.aging_days) - Number(a.aging_days))
      .slice(0, 8);
  }

  document.querySelector("#incidentList").replaceChildren(...incidents.map((incident) => el(
    "article",
    "incident",
    `<div>
      <b class="badge ${riskClass(incident.severity)}">${incident.severity}</b>
      <strong>${incident.incident_id} | ${incident.mapping_id}</strong>
      <span>${incident.verification_phase} | ${incident.status} | ${incident.aging_days} days</span>
    </div>
    <p>${incident.business_impact}</p>
    <small>${incident.next_update}</small>`
  )));
};

const renderSqlEvidence = () => {
  document.querySelector("#sqlEvidence").replaceChildren(...payload.sqlEvidence.map((item) => el(
    "article",
    "sql-tile",
    `<b>${item.technique}</b><h3>${item.name}</h3><p>${item.purpose}</p>`
  )));
};

const render = () => {
  renderMetrics();
  renderFilters();
  renderDomainPulse();
  renderQueue();
  renderSttm();
  renderReconciliation();
  renderIncidents();
  renderSqlEvidence();
};

const init = async () => {
  const response = await fetch("analysis/outputs/app_payload.json");
  payload = await response.json();
  render();
};

init().catch((error) => {
  document.querySelector("#appStatus").textContent = `Unable to load generated analysis payload: ${error.message}`;
});
