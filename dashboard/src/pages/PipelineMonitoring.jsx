import { useCallback, useEffect, useState } from "react";
import ReactFlow, { Background, Controls, MarkerType } from "reactflow";
import "reactflow/dist/style.css";
import { api } from "../api";

const STATUS_STYLE = {
  success: { bg: "#EAF6F1", border: "#2FA37E", dot: "#2FA37E", label: "Success" },
  running: { bg: "#EAF1FB", border: "#3E7BD6", dot: "#3E7BD6", label: "Running" },
  failed: { bg: "#FBEAEA", border: "#D6483E", dot: "#D6483E", label: "Failed" },
  queued: { bg: "#F4F4F5", border: "#8E8E93", dot: "#8E8E93", label: "Queued" },
  up_for_retry: { bg: "#FFF8E8", border: "#C99A2E", dot: "#C99A2E", label: "Retrying" },
};

function TaskNode({ data }) {
  const s = STATUS_STYLE[data.status] || STATUS_STYLE.queued;
  return (
    <div
      className="rounded-[10px] px-3.5 py-2.5 cursor-pointer"
      style={{ background: s.bg, border: `1.5px solid ${s.border}`, minWidth: 150 }}
      onClick={data.onClick}
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

function buildGraph(tasks, onSelect) {
  const order = ["bronze_ingest", "silver_transform", "gold_compute"];
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
          onClick: () => onSelect(id),
        },
      };
    })
    .filter(Boolean);

  const edges = [
    { id: "e1", source: "bronze_ingest", target: "silver_transform", markerEnd: { type: MarkerType.ArrowClosed, color: "#D4D4D8" }, style: { stroke: "#D4D4D8" } },
    { id: "e2", source: "silver_transform", target: "gold_compute", markerEnd: { type: MarkerType.ArrowClosed, color: "#D4D4D8" }, style: { stroke: "#D4D4D8" } },
  ];

  return { nodes: positioned, edges };
}

export default function PipelineMonitoring() {
  const [state, setState] = useState({ loading: true, configured: true, runs: [], error: null });
  const [selectedTask, setSelectedTask] = useState(null);
  const [logState, setLogState] = useState({ loading: false, log: null, error: null });

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const res = await api.getPipelineRuns(5);
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
  }, []);

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

  const { nodes, edges } = latestRun ? buildGraph(latestRun.tasks, onSelectTask) : { nodes: [], edges: [] };

  const allSucceeded = latestRun?.tasks?.length > 0 && latestRun.tasks.every((t) => t.state === "success");

  return (
    <div className="p-7 md:px-8">
      <div className="mb-5 flex items-baseline justify-between flex-wrap gap-2">
        <div>
          <h1 className="text-xl font-semibold text-ink tracking-tight">Pipeline Monitoring</h1>
          <p className="text-[13px] text-ink-faint mt-1">
            {latestRun ? `banking_demo_lakehouse_spike · ${latestRun.run_id}` : "Waiting for a run…"}
          </p>
        </div>
        {latestRun && (
          <div
            className={`text-[11.5px] font-semibold px-3 py-1.5 rounded-full flex items-center gap-1.5 ${
              allSucceeded ? "text-success bg-success-soft" : "text-running bg-running-soft"
            }`}
          >
            <span className={`w-1.5 h-1.5 rounded-full inline-block ${allSucceeded ? "bg-success" : "bg-running"}`} />
            {allSucceeded ? "All tasks succeeded" : latestRun.state}
          </div>
        )}
      </div>

      {!state.configured && (
        <div className="mb-4 text-[12.5px] text-ink-soft bg-[#FFF8E8] border border-[#F0DFAE] rounded-xl px-4 py-3">
          Not connected yet — set <code className="font-mono">LAKEHOUSE_DB_URI</code> on this service to see live runs.
        </div>
      )}
      {state.error && (
        <div className="mb-4 text-[12.5px] text-danger bg-danger-soft border border-danger/20 rounded-xl px-4 py-3">
          {state.error}
        </div>
      )}
      {!state.loading && state.configured && !latestRun && !state.error && (
        <div className="mb-4 text-[12.5px] text-ink-faint bg-[#FAFAFB] border border-line rounded-xl px-4 py-3">
          No runs yet — trigger the DAG in Airflow to see it here.
        </div>
      )}

      {latestRun && (
        <div className="bg-white border border-line rounded-card shadow-[0_1px_2px_rgba(0,0,0,0.02),0_8px_24px_-12px_rgba(0,0,0,0.06)]" style={{ height: 260 }}>
          <ReactFlow nodes={nodes} edges={edges} nodeTypes={nodeTypes} fitView proOptions={{ hideAttribution: true }}>
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
    </div>
  );
}
