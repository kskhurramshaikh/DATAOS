const BASE = "/api";

async function getJSON(path) {
  const res = await fetch(`${BASE}${path}`);
  if (!res.ok) throw new Error(`${path} -> ${res.status}`);
  return res.json();
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
  getZones: () => getJSON("/lakehouse/zones"),
  getPipelineRuns: (limit = 10) => getJSON(`/pipeline/runs?limit=${limit}`),
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
};
