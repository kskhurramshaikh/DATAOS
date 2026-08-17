const BASE = "/api";

async function getJSON(path) {
  const res = await fetch(`${BASE}${path}`);
  if (!res.ok) throw new Error(`${path} -> ${res.status}`);
  return res.json();
}

export const api = {
  getZones: () => getJSON("/lakehouse/zones"),
  getPipelineRuns: (limit = 10) => getJSON(`/pipeline/runs?limit=${limit}`),
  getTaskLog: (runId, taskId, tryNumber = 1) =>
    getJSON(`/pipeline/logs/${encodeURIComponent(runId)}/${encodeURIComponent(taskId)}?try_number=${tryNumber}`),
};
