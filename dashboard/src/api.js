const BASE = "/api";

async function getJSON(path) {
  const res = await fetch(`${BASE}${path}`);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `${path} -> ${res.status}`);
  return data;
}

async function postJSON(path, body) {
  const res = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
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
  const res = await fetch(`${BASE}${path}`, { method: "POST", body: form });
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

  getSama: (datasetName) => getJSON(`/governance/sama${datasetName ? `?dataset_name=${encodeURIComponent(datasetName)}` : ""}`),
  getAuditLog: (datasetName) => getJSON(`/governance/audit-log${datasetName ? `?dataset_name=${encodeURIComponent(datasetName)}` : ""}`),

  // NDI (item 5). No dataset parameter anywhere on purpose -- the
  // assessment runs on Dr. Saber's fixed BAJ baseline, not on an
  // uploaded dataset, so there is nothing to scope these to.
  getNdi: () => getJSON("/governance/ndi"),
  getNdiHistory: () => getJSON("/governance/ndi/history"),
  getNdiSnapshot: (id) => getJSON(`/governance/ndi/history/${id}`),
  recordNdiSnapshot: (recordedBy, note) =>
    postJSON("/governance/ndi/snapshot", { recorded_by: recordedBy, note: note || null }),

  // Data Catalog + Field Lineage (item 6). Real data from Marquez --
  // see app/marquez_client.py's module docstring. No dataset
  // parameter on getCatalogJobs/getFieldLineage -- both return
  // everything Marquez knows across all datasets, since a catalog is
  // meant to be browsed as a whole, not scoped to one dataset at a
  // time the way the Lakehouse/SAMA pages are.
  getCatalogJobs: () => getJSON("/catalog/jobs"),
  getCatalogJobRuns: (jobName, limit = 10) => getJSON(`/catalog/jobs/${encodeURIComponent(jobName)}/runs?limit=${limit}`),
  getFieldLineage: () => getJSON("/catalog/lineage"),
};
