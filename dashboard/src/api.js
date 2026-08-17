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

export const api = {
  getZones: () => getJSON("/lakehouse/zones"),
  getPipelineRuns: (limit = 10) => getJSON(`/pipeline/runs?limit=${limit}`),
  getTaskLog: (runId, taskId, tryNumber = 1) =>
    getJSON(`/pipeline/logs/${encodeURIComponent(runId)}/${encodeURIComponent(taskId)}?try_number=${tryNumber}`),

  getDuplicateQueue: (datasetName) =>
    getJSON(`/mdm/duplicate-queue${datasetName ? `?dataset_name=${encodeURIComponent(datasetName)}` : ""}`),
  decideCluster: (clusterId, status, decidedBy) =>
    postJSON("/mdm/duplicate-queue/decide", { cluster_id: clusterId, status, decided_by: decidedBy }),
  getGoldenRecords: (datasetName) =>
    getJSON(`/mdm/golden-records${datasetName ? `?dataset_name=${encodeURIComponent(datasetName)}` : ""}`),
  getGoldenRecordDetail: (id) => getJSON(`/mdm/golden-records/${id}`),
};
