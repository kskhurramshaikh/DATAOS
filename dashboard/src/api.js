import { getStoredToken } from "./auth";

const BASE = "/api";

// Real Bearer auth, wired 2026-08-19 -- see auth.jsx's module
// docstring. Attached to every request unconditionally: harmless for
// every endpoint that doesn't need it (the backend simply ignores an
// Authorization header it has no Depends() for), and required for the
// two real RBAC/OPA policy points (classification detail, stewardship
// assign/unassign) -- see app/opa_client.py.
function authHeaders() {
  const token = getStoredToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function getJSON(path, { signal } = {}) {
  const res = await fetch(`${BASE}${path}`, { signal, headers: authHeaders() });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `${path} -> ${res.status}`);
  return data;
}

async function postJSON(path, body) {
  const res = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `${path} -> ${res.status}`);
  return data;
}

async function postForm(path, file, fields) {
  const form = new FormData();
  form.append("file", file);
  Object.entries(fields).forEach(([k, v]) => form.append(k, v));
  const res = await fetch(`${BASE}${path}`, { method: "POST", body: form, headers: authHeaders() });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `${path} -> ${res.status}`);
  return data;
}

async function deleteJSON(path) {
  const res = await fetch(`${BASE}${path}`, { method: "DELETE", headers: authHeaders() });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `${path} -> ${res.status}`);
  return data;
}

export const api = {
  getZones: (datasetName) => getJSON(`/lakehouse/zones${datasetName ? `?dataset_name=${encodeURIComponent(datasetName)}` : ""}`),
  getPipelineRuns: (datasetName, limit = 10) =>
    getJSON(`/pipeline/runs?limit=${limit}${datasetName ? `&dataset_name=${encodeURIComponent(datasetName)}` : ""}`),
  triggerPipeline: (datasetName) => postJSON("/lakehouse/trigger", { dataset_name: datasetName }),
  getTaskLog: (runId, taskId, tryNumber = 1) =>
    getJSON(`/pipeline/logs/${encodeURIComponent(runId)}/${encodeURIComponent(taskId)}?try_number=${tryNumber}`),

  getDatasets: () => getJSON("/mdm/datasets"),
  uploadDataset: (file, datasetName) => postForm("/mdm/upload-dataset", file, { dataset_name: datasetName }),
  detectDuplicates: (datasetName) => postJSON("/mdm/detect-duplicates", { dataset_name: datasetName }),
  getDuplicateQueue: (datasetName) =>
    getJSON(`/mdm/duplicate-queue${datasetName ? `?dataset_name=${encodeURIComponent(datasetName)}` : ""}`),
  decideCluster: (clusterId, status, decidedBy) =>
    postJSON("/mdm/duplicate-queue/decide", { cluster_id: clusterId, status, decided_by: decidedBy }),
  bulkConfirm: (tier, decidedBy, datasetName) =>
    postJSON("/mdm/duplicate-queue/bulk-confirm", { tier, decided_by: decidedBy, dataset_name: datasetName ?? null }),
  getGoldenRecords: (datasetName) =>
    getJSON(`/mdm/golden-records${datasetName ? `?dataset_name=${encodeURIComponent(datasetName)}` : ""}`),
  getGoldenRecordDetail: (id) => getJSON(`/mdm/golden-records/${id}`),
  // Field-Level Lineage (Item 3's 3rd of 4 required MDM pages,
  // reopened 2026-08-18). Dataset-scoped, unlike Item 6's catalog
  // lineage -- see app/field_lineage.py's module docstring.
  //
  // ACCEPTS { signal } (2026-08-18, real bug fix): the page-level
  // useEffect cleanup previously only set a local `cancelled` flag,
  // which suppressed a stale response's STATE UPDATE but never
  // actually cancelled the underlying fetch -- so a stale in-flight
  // request kept running server-side. Confirmed live (both by Claude
  // and independently by Khurram, same symptom): the page got stuck
  // on "Loading..." permanently, and the network panel showed several
  // real duplicate requests to this exact endpoint piling up, with a
  // mix of 503 and one eventual 200 -- consistent with redundant
  // requests queuing against this service's single free-tier worker.
  // getJSON's new signal option lets the page actually abort a
  // superseded request via AbortController, instead of just ignoring
  // its result.
  getMdmFieldLineage: (datasetName, opts) => getJSON(`/mdm/field-lineage?dataset_name=${encodeURIComponent(datasetName)}`, opts),

  // Data Stewardship (Item 3's 4th and last required MDM page).
  getStewardship: (datasetName) => getJSON(`/mdm/stewardship?dataset_name=${encodeURIComponent(datasetName)}`),
  getStewardshipCoverage: () => getJSON("/mdm/stewardship/coverage"),
  // assigned_by is no longer a caller-supplied argument, wired
  // 2026-08-19 -- the server takes it from the real logged-in user's
  // verified token (see main.py's mdm_stewardship_assign), not
  // whatever a client claims. Requires authHeaders() to actually carry
  // a token, same as the classification RBAC/OPA gate.
  assignStewardship: (datasetName, role, assigneeName, assigneeEmail, note) =>
    postJSON("/mdm/stewardship/assign", {
      dataset_name: datasetName,
      role,
      assignee_name: assigneeName,
      assignee_email: assigneeEmail,
      note,
    }),
  unassignStewardship: (datasetName, role) => postJSON("/mdm/stewardship/unassign", { dataset_name: datasetName, role }),

  // Stewardship Policy Wizard (2026-08-20) -- closes the "no policy
  // wizard anywhere on the page" gap. Separate concern from role
  // assignment above: WHAT the accountability rules are (retention,
  // review cadence, quality bar, escalation contact), not WHO holds
  // each role.
  getStewardshipPolicy: (datasetName) => getJSON(`/mdm/stewardship/policy?dataset_name=${encodeURIComponent(datasetName)}`),
  setStewardshipPolicy: (datasetName, fields) => postJSON("/mdm/stewardship/policy", { dataset_name: datasetName, ...fields }),

  // Stewardship Task Assignment (2026-08-20) -- closes the last named
  // gap on this page. Separate concern from both role assignment and
  // the Policy Wizard above -- see stewardship_adapter.py's module
  // docstring for the WHO/WHAT/WHAT-NEEDS-DOING distinction.
  getStewardshipTasks: (datasetName) => getJSON(`/mdm/stewardship/tasks?dataset_name=${encodeURIComponent(datasetName)}`),
  createStewardshipTask: (datasetName, fields) =>
    postJSON("/mdm/stewardship/tasks", { dataset_name: datasetName, ...fields }),
  updateStewardshipTaskStatus: (datasetName, taskId, status) =>
    postJSON("/mdm/stewardship/tasks/status", { dataset_name: datasetName, task_id: taskId, status }),
  deleteStewardshipTask: (datasetName, taskId) =>
    postJSON("/mdm/stewardship/tasks/delete", { dataset_name: datasetName, task_id: taskId }),

  getSama: (datasetName) => getJSON(`/governance/sama${datasetName ? `?dataset_name=${encodeURIComponent(datasetName)}` : ""}`),
  getAuditLog: (datasetName) => getJSON(`/governance/audit-log${datasetName ? `?dataset_name=${encodeURIComponent(datasetName)}` : ""}`),

  // SAMA History (2026-08-20) -- closes the "no trend-over-time
  // exists" gap. dataset_name REQUIRED on every call here, unlike NDI
  // history -- SAMA compliance is always scoped to one dataset.
  getSamaHistory: (datasetName, limit = 100) =>
    getJSON(`/governance/sama/history?dataset_name=${encodeURIComponent(datasetName)}&limit=${limit}`),
  getSamaSnapshot: (id) => getJSON(`/governance/sama/history/${id}`),
  recordSamaSnapshot: (datasetName, recordedBy, note) =>
    postJSON("/governance/sama/snapshot", { dataset_name: datasetName, recorded_by: recordedBy, note: note || null }),
  samaHistoryExportUrl: (datasetName) => `${BASE}/governance/sama/history/export?dataset_name=${encodeURIComponent(datasetName)}`,
  samaSnapshotExportUrl: (id) => `${BASE}/governance/sama/history/${id}/export`,

  // Classification & PDPL + Data Quality Rules (item 7).
  getClassification: (datasetName) => getJSON(`/governance/classification?dataset_name=${encodeURIComponent(datasetName)}`),
  getClassificationCoverage: () => getJSON("/governance/classification/coverage"),

  // Policy Document Upload (2026-08-20) -- global (org-level), not
  // dataset-scoped. See app/adapters/policy_documents_adapter.py's
  // module docstring.
  getPolicyDocuments: () => getJSON("/governance/classification/policy-documents"),
  uploadPolicyDocument: (file, uploadedBy, note) =>
    postForm("/governance/classification/policy-documents", file, { uploaded_by: uploadedBy, note: note || "" }),
  deletePolicyDocument: (id) => deleteJSON(`/governance/classification/policy-documents/${id}`),
  policyDocumentDownloadUrl: (id) => `${BASE}/governance/classification/policy-documents/${id}/download`,

  getQualityRules: (datasetName) => getJSON(`/governance/quality-rules?dataset_name=${encodeURIComponent(datasetName)}`),

  // NDI (item 5). No dataset parameter anywhere on purpose -- the
  // assessment runs on Dr. Saber's fixed BAJ baseline, not on an
  // uploaded dataset, so there is nothing to scope these to.
  getNdi: () => getJSON("/governance/ndi"),
  getNdiHistory: () => getJSON("/governance/ndi/history"),
  getNdiSnapshot: (id) => getJSON(`/governance/ndi/history/${id}`),
  recordNdiSnapshot: (recordedBy, note) =>
    postJSON("/governance/ndi/snapshot", { recorded_by: recordedBy, note: note || null }),
  // Period comparison + CSV export, wired 2026-08-19 -- the two gaps
  // flagged and deferred when NDI History first shipped. Export URLs
  // are plain paths, not fetch-based -- these are unauthenticated GETs
  // that return a real file (Content-Disposition: attachment), so a
  // direct <a href> download is simpler and more correct than
  // fetch+blob for the same result.
  compareNdiSnapshots: (idA, idB) => getJSON(`/governance/ndi/compare?a=${idA}&b=${idB}`),
  ndiHistoryExportUrl: () => `${BASE}/governance/ndi/history/export`,
  ndiSnapshotExportUrl: (id) => `${BASE}/governance/ndi/history/${id}/export`,
  // Real Excel (.xlsx) and PDF export, wired 2026-08-20 -- closes the
  // "export ships as CSV, not the literal Excel/PDF named in the doc"
  // gap. Same plain-<a>-download pattern as the CSV URLs above.
  ndiHistoryExportXlsxUrl: () => `${BASE}/governance/ndi/history/export.xlsx`,
  ndiHistoryExportPdfUrl: () => `${BASE}/governance/ndi/history/export.pdf`,
  ndiSnapshotExportXlsxUrl: (id) => `${BASE}/governance/ndi/history/${id}/export.xlsx`,
  ndiSnapshotExportPdfUrl: (id) => `${BASE}/governance/ndi/history/${id}/export.pdf`,

  // Data Catalog + Field Lineage (item 6). Real data from Marquez --
  // see app/marquez_client.py's module docstring. No dataset
  // parameter on getCatalogJobs/getFieldLineage -- both return
  // everything Marquez knows across all datasets, since a catalog is
  // meant to be browsed as a whole, not scoped to one dataset at a
  // time the way the Lakehouse/SAMA pages are.
  getCatalogJobs: () => getJSON("/catalog/jobs"),
  getCatalogJobRuns: (jobName, limit = 10) => getJSON(`/catalog/jobs/${encodeURIComponent(jobName)}/runs?limit=${limit}`),
  getFieldLineage: () => getJSON("/catalog/lineage"),

  // User & role administration, wired 2026-08-19 -- real Keycloak
  // Admin REST API calls, admin-only (see app/adapters/
  // user_admin_adapter.py's module docstring). Closes the
  // "no admin-promotion UI" gap the RBAC/OPA build left -- promoting
  // a user used to require Keycloak's own admin console directly.
  getAdminUsers: () => getJSON("/admin/users"),
  getAdminRoles: () => getJSON("/admin/roles"),
  assignUserRole: (userId, role) => postJSON(`/admin/users/${encodeURIComponent(userId)}/roles`, { role }),
  removeUserRole: (userId, role) => deleteJSON(`/admin/users/${encodeURIComponent(userId)}/roles/${encodeURIComponent(role)}`),
};
