import { useCallback, useEffect, useState } from "react";
import ReactFlow, { Background, Controls, MarkerType } from "reactflow";
import "reactflow/dist/style.css";
import { api } from "../api";
import DatasetPicker from "../components/DatasetPicker";
import TriggerPipelineButton from "../components/TriggerPipelineButton";

const STATUS_STYLE = {
  success: { bg: "#EAF6F1", border: "#2FA37E", dot: "#2FA37E", label: "Success" },
  running: { bg: "#EAF1FB", border: "#3E7BD6", dot: "#3E7BD6", label: "Running" },
  failed: { bg: "#FBEAEA", border: "#D6483E", dot: "#D6483E", label: "Failed" },
  queued: { bg: "#F4F4F5", border: "#8E8E93", dot: "#8E8E93", label: "Queued" },
  up_for_retry: { bg: "#FFF8E8", border: "#C99A2E", dot: "#C99A2E", label: "Retrying" },
};

// Same key as LakehouseZones.jsx -- one selection shared across both
// Lakehouse pages, and persisted across reloads (real browser
// localStorage; this is a genuine deployed SPA, not an Artifact
// preview). Picking a dataset here also sticks if you switch to Zones.
const SELECTED_DATASET_KEY = "dataos:lakehouse:selectedDataset";

// BUG FIX ROUND 2 (2026-08-18, confirmed live via real testing, not
// guessed): the earlier fix (React Flow's own onNodeClick prop,
// instead of a plain onClick on this node's div, with the reasoning
// that React Flow's drag-handling was swallowing plain clicks) did NOT
// actually work -- confirmed directly: a genuine mouse click (via
// Claude-in-Chrome's computer tool, not a synthetic dispatchEvent)
// left React's own selectedTask state unset, and zero network request
// to /api/pipeline/logs/... ever fired. onNodeClick itself simply
// never fired in this deployed build, for reasons not worth chasing
# further blind (same lesson as Field Lineage's edge-rendering saga:
# stop guessing at this library's internals, use the mechanism that's
# actually confirmed to work).
#
# Real fix: a plain onClick directly on this node's own div, PLUS the
# onNodeClick prop kept as a harmless redundant path. This is safe now
# in a way it wasn't before nodesDraggable={false} existed: the
# original drag-vs-click conflict this was trying to avoid depended on
# React Flow's drag-gesture detection actually running on this node,
# which nodesDraggable={false} (already present on the <ReactFlow>
# element below) disables entirely -- so a plain onClick has nothing
# left to race against. data.onSelect is threaded in from buildGraph()
# below (a closure over the current onSelectTask), not a prop this
# component receives directly from React Flow.
function TaskNode({ id, data }) {
  const s = STATUS_STYLE[data.status] || STATUS_STYLE.queued;
  return (
    <div
      className="rounded-[10px] px-3.5 py-2.5 cursor-pointer"
      style={{ background: s.bg, border: `1.5px solid ${s.border}`, minWidth: 150 }}
      onClick={() => data.onSelect?.(id)}
    >
      <div className="flex items-center gap-2">
        <span className="w-2 h-2 rounded-full inline-block" style={{ background: s.dot }} />
        <span className="text-[11.5px] font-semibold text-ink font-mono">{data.label}</span>
      </div>
      <div className="text-[10px] text-ink-faint mt-1">
        {s.label}
        {data.duration != null && ` · ${data.duration}s`}
      </div>
    </div>
  );
}

const nodeTypes = { task: TaskNode };

// Task names match the generalized DAG (2026-08-18): verify_silver_ready
// (was bronze_ingest) -> silver_to_iceberg (was silver_transform) ->
// gold_compute (unchanged).
//
// onSelect (2026-08-18, bug fix round 2): threaded into each node's
// data so TaskNode's own onClick above can call it directly -- see
// that component's comment for why this replaced relying solely on
# React Flow's onNodeClick, which was confirmed live not to fire.
function buildGraph(tasks, onSelect) {
  const order = ["verify_silver_ready", "silver_to_iceberg", "gold_compute"];
  const positioned = order
    .map((id, i) => {
      const t = tasks.find((x) => x.task_id === id);
      return {
        id,
        type: "task",
        position: { x: i * 260, y: 60 },
        data: {
          label: id,
          status: t?.state || "queued",
          duration: t?.duration_s,
          onSelect,
        },
      };
    })
    .filter(Boolean);

  const edges = [
    { id: "e1", source: "verify_silver_ready", target: "silver_to_iceberg", markerEnd: { type: MarkerType.ArrowClosed, color: "#D4D4D8" }, style: { stroke: "#D4D4D8" } },
    { id: "e2", source: "silver_to_iceberg", target: "gold_compute", markerEnd: { type: MarkerType.ArrowClosed, color: "#D4D4D8" }, style: { stroke: "#D4D4D8" } },
  ];

  return { nodes: positioned, edges };
}

export default function PipelineMonitoring() {
  const [datasets, setDatasets] = useState([]);
  const [datasetsLoaded, setDatasetsLoaded] = useState(false);
  const [selected, setSelected] = useState(() => {
    try {
      return localStorage.getItem(SELECTED_DATASET_KEY) || "";
    } catch {
      return "";
    }
  });
  const [state, setState] = useState({ loading: false, configured: true, runs: [], error: null });
  const [refreshTick, setRefreshTick] = useState(0);
  const [selectedTask, setSelectedTask] = useState(null);
  const [logState, setLogState] = useState({ loading: false, log: null, error: null });

  function selectDataset(name) {
    setSelected(name);
    try {
      if (name) localStorage.setItem(SELECTED_DATASET_KEY, name);
      else localStorage.removeItem(SELECTED_DATASET_KEY);
    } catch {
      // Non-fatal.
    }
  }

  // Same picker + persistence pattern as LakehouseZones.jsx.
  useEffect(() => {
    let cancelled = false;
    async function loadDatasets() {
      try {
        const res = await api.getDatasets();
        if (cancelled) return;
        const list = res.datasets ?? [];
        setDatasets(list);
        setSelected((current) => {
          const stillValid = current && list.some((d) => d.dataset_name === current);
          if (stillValid) return current;
          const next = list.length === 1 ? list[0].dataset_name : "";
          try {
            if (next) localStorage.setItem(SELECTED_DATASET_KEY, next);
            else localStorage.removeItem(SELECTED_DATASET_KEY);
          } catch {
            // Non-fatal.
          }
          return next;
        });
        setDatasetsLoaded(true);
      } catch {
        if (!cancelled) setDatasetsLoaded(true);
      }
    }
    loadDatasets();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    setSelectedTask(null);
    if (!selected) {
      setState({ loading: false, configured: true, runs: [], error: null });
      return;
    }
    let cancelled = false;
    async function load() {
      try {
        const res = await api.getPipelineRuns(selected, 5);
        if (!cancelled) setState({ loading: false, configured: res.configured, runs: res.runs, error: res.error || null });
      } catch (e) {
        if (!cancelled) setState({ loading: false, configured: true, runs: [], error: e.message });
      }
    }
    load();
    const interval = setInterval(load, 10000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [selected, refreshTick]);

  const latestRun = state.runs[0];

  const onSelectTask = useCallback(
    async (taskId) => {
      if (!latestRun) return;
      setSelectedTask(taskId);
      setLogState({ loading: true, log: null, error: null });
      try {
        const res = await api.getTaskLog(latestRun.run_id, taskId);
        setLogState({ loading: false, log: res.log, error: res.error || null });
      } catch (e) {
        setLogState({ loading: false, log: null, error: e.message });
      }
    },
    [latestRun]
  );

  // Kept as a harmless redundant path alongside TaskNode's own onClick
  // -- see that component's comment for why the plain onClick is now
  // the mechanism actually relied on.
  const handleNodeClick = useCallback(
    (_event, node) => {
      onSelectTask(node.id);
    },
    [onSelectTask]
  );

  const { nodes, edges } = latestRun ? buildGraph(latestRun.tasks, onSelectTask) : { nodes: [], edges: [] };

  const allSucceeded = latestRun?.tasks?.length > 0 && latestRun.tasks.every((t) => t.state === "success");

  return (
    <div className="p-7 md:px-8">
      <div className="mb-5 flex items-start justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-xl font-semibold text-ink tracking-tight">Pipeline Monitoring</h1>
          <p className="text-[13px] text-ink-faint mt-1">
            {latestRun ? `run ${latestRun.run_id}` : selected ? "Waiting for a run…" : "Select a dataset to see its pipeline history"}
          </p>
        </div>
        <div className="flex items-start gap-3 flex-wrap">
          <DatasetPicker datasets={datasets} value={selected} onChange={selectDataset} />
          <TriggerPipelineButton datasetName={selected} onTriggered={() => setRefreshTick((t) => t + 1)} />
        </div>
      </div>

      {latestRun && (
        <div className="mb-4">
          <div
            className={`inline-flex text-[11.5px] font-semibold px-3 py-1.5 rounded-full items-center gap-1.5 ${
              allSucceeded ? "text-success bg-success-soft" : "text-running bg-running-soft"
            }`}
          >
            <span className={`w-1.5 h-1.5 rounded-full inline-block ${allSucceeded ? "bg-success" : "bg-running"}`} />
            {allSucceeded ? "All tasks succeeded" : latestRun.state}
          </div>
        </div>
      )}

      {!selected && datasetsLoaded && datasets.length > 1 && (
        <div className="mb-4 text-[12.5px] text-ink-soft bg-[#FAFAFB] border border-line rounded-xl px-4 py-3">
          Select a dataset above to view its pipeline runs.
        </div>
      )}
      {!selected && datasetsLoaded && datasets.length === 0 && (
        <div className="mb-4 text-[12.5px] text-ink-faint bg-[#FAFAFB] border border-line rounded-xl px-4 py-3">
          No datasets yet — upload one from the MDM tab first.
        </div>
      )}

      {selected && !state.configured && (
        <div className="mb-4 text-[12.5px] text-ink-soft bg-[#FFF8E8] border border-[#F0DFAE] rounded-xl px-4 py-3">
          Not connected yet — set <code className="font-mono">LAKEHOUSE_DB_URI</code> on this service to see live runs.
        </div>
      )}
      {state.error && (
        <div className="mb-4 text-[12.5px] text-danger bg-danger-soft border border-danger/20 rounded-xl px-4 py-3">
          {state.error}
        </div>
      )}
      {selected && !state.loading && state.configured && !latestRun && !state.error && (
        <div className="mb-4 text-[12.5px] text-ink-faint bg-[#FAFAFB] border border-line rounded-xl px-4 py-3">
          No runs yet for this dataset — click "Run pipeline" above to start one.
        </div>
      )}

      {latestRun && (
        <div className="bg-white border border-line rounded-card shadow-[0_1px_2px_rgba(0,0,0,0.02),0_8px_24px_-12px_rgba(0,0,0,0.06)]" style={{ height: 260 }}>
          <ReactFlow
            nodes={nodes}
            edges={edges}
            nodeTypes={nodeTypes}
            fitView
            proOptions={{ hideAttribution: true }}
            onNodeClick={handleNodeClick}
            nodesDraggable={false}
          >
            <Background color="#EEEEF0" gap={16} />
            <Controls showInteractive={false} />
          </ReactFlow>
        </div>
      )}

      {selectedTask && (
        <div className="mt-4 bg-[#FAFAFB] border border-line rounded-xl px-4.5 py-3.5 text-[12px] text-ink-soft font-mono">
          <div className="mb-1.5 text-ink font-semibold">{selectedTask} — log</div>
          {logState.loading && <div className="text-ink-faint">Loading…</div>}
          {logState.error && <div className="text-danger">{logState.error}</div>}
          {logState.log && (
            <pre className="whitespace-pre-wrap max-h-64 overflow-auto text-[11px] leading-relaxed">{logState.log}</pre>
          )}
          {!logState.loading && !logState.error && !logState.log && (
            <div className="text-ink-faint">No log content found for this task/run.</div>
          )}
        </div>
      )}

      {selected && state.runs.length > 0 && (
        <div className="mt-4 text-[11.5px] text-ink-faint">
          Live data — refreshes every 10s. "Run pipeline" is manual by design — nothing here runs automatically on upload.
        </div>
      )}
    </div>
  );
}
