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

// BUG FIX ROUND 4 (2026-08-18, confirmed via direct DOM inspection
// after round 3's fix was deployed and re-checked -- not guessed):
// round 3 correctly diagnosed the cause (the pane's z-index beating
// the node's) but set the zIndex on the WRONG element. React Flow
// wraps every custom node component in its own outer
// ".react-flow__node" div, which is the element that actually
// participates in the stacking-order comparison against the pane --
// a zIndex set on a CHILD div inside it (what round 3 did) has no
// effect on that comparison at all. Confirmed directly: re-read
// getComputedStyle on ".react-flow__node" itself after round 3
// deployed, and it was still zIndex "0" -- round 3's fix genuinely
// never reached the element that mattered.
//
// REAL FIX: React Flow reads a per-node `style` property (set on the
// node OBJECT passed to the `nodes` array, in buildGraph() below) and
// applies it directly to its own ".react-flow__node" wrapper -- that's
// the supported, correct way to influence this specific element,
// not a style prop on the custom component's own rendered div.
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
// onSelect: threaded into each node's data so TaskNode's own onClick
// above can call it directly. style: { zIndex: 10 } on each node
// object (round 4's real fix -- see TaskNode's own comment) is what
// actually wins the stacking order against React Flow's pane.
function buildGraph(tasks, onSelect) {
  const order = ["verify_silver_ready", "silver_to_iceberg", "gold_compute"];
  const positioned = order
    .map((id, i) => {
      const t = tasks.find((x) => x.task_id === id);
      return {
        id,
        type: "task",
        position: { x: i * 260, y: 60 },
        style: { zIndex: 10 },
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

  // Same real fix as LakehouseZones.jsx's dataset-switch bug -- see
  // that file's comment for the full reasoning. This page had the
  // even plainer version of the bug: `load()` here never set `loading`
  // at all before its await, so switching datasets left the OLD
  // dataset's run history and task statuses on screen with no loading
  // indicator whatsoever until the new fetch resolved. A separate
  // effect keyed only on `selected` clears `runs` immediately on
  // dataset change; the periodic-refresh effect below still preserves
  // last-known data across a 10s poll/manual trigger, which is correct
  // (the dataset hasn't changed in that case).
  useEffect(() => {
    setSelectedTask(null);
    setState({ loading: !!selected, configured: true, runs: [], error: null });
  }, [selected]);

  useEffect(() => {
    if (!selected) return;
    let cancelled = false;
    async function load() {
      setState((s) => ({ ...s, loading: true }));
      try {
        const res = await api.getPipelineRuns(selected, 5);
        if (!cancelled) setState({ loading: false, configured: res.configured, runs: res.runs, error: res.error || null });
      } catch (e) {
        if (!cancelled) setState((s) => ({ ...s, loading: false, error: e.message }));
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
  // -- see that component's comment for the full history.
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
          {/* Schedule display (2026-08-20, closes the "no schedule display
              anywhere" gap): reflects this DAG's REAL config -- see
              spike/dags/banking_demo_lakehouse_spike.py's `schedule=None` and
              its own module docstring, a deliberate design decision
              (auto-triggering on upload would add load/delay most uploads
              don't need), not a missing feature. Static text is correct here
              rather than a fetched value -- the schedule isn't per-dataset or
              runtime-configurable, it's the same fixed DAG setting for every
              dataset. */}
          <div className="inline-flex items-center gap-1.5 mt-2 text-[11px] font-semibold text-ink-faint bg-[#F4F4F5] px-2.5 py-1 rounded-full">
            <span className="w-1.5 h-1.5 rounded-full inline-block bg-[#8E8E93]" />
            Schedule: Manual trigger only — no automatic runs
          </div>
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
