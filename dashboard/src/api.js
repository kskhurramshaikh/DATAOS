const BASE = "/api";

async function getJSON(path, { signal } = {}) {
  const res = await fetch(`${BASE}${path}`, { signal });
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
