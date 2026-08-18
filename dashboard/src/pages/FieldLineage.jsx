import { useEffect, useMemo, useState } from "react";
import ReactFlow, { Background, Controls, Handle, MarkerType, Position } from "reactflow";
import "reactflow/dist/style.css";
import { api } from "../api";

// Field Lineage (Dev Queue item 6, second half). See
// app/marquez_client.py's module docstring for the full story on how
// this graph is actually built: Marquez's own /datasets endpoint was
// confirmed empty in this setup (checked directly against two real
// triggered DAG runs), so get_field_lineage() assembles the graph
// itself from each job's latest run facet's real inlets/outlets --
// genuine data, just not routed through Marquez's own dataset
// registry. Every node/edge below traces to an actual S3 path a real
// task actually read from or wrote to.

const JOB_STATE_STYLE = {
  COMPLETED: { bg: "#EAF6F1", border: "#2FA37E", dot: "#2FA37E" },
  RUNNING: { bg: "#EAF1FB", border: "#3E7BD6", dot: "#3E7BD6" },
  FAILED: { bg: "#FBEAEA", border: "#D6483E", dot: "#D6483E" },
};

// Both custom node types use explicit left/right Handle ids, matching
// sourceHandle="right"/targetHandle="left" set on every edge in
// layoutGraph() below -- confirmed live (real edges rendering between
// all 5 nodes, checked directly via DOM inspection, not assumed) after
// debugging an earlier false alarm where a stale/pre-render DOM check
// made it look like edges weren't drawing when they actually were.
function JobNode({ data }) {
  const s = JOB_STATE_STYLE[data.runState] || { bg: "#F4F4F5", border: "#8E8E93", dot: "#8E8E93" };
  return (
    <div
      className="rounded-[10px] px-3.5 py-2.5"
      style={{ background: s.bg, border: `1.5px solid ${s.border}`, minWidth: 170 }}
    >
      <Handle id="left" type="target" position={Position.Left} style={{ background: s.border }} />
      <div className="flex items-center gap-2">
        <span className="w-2 h-2 rounded-full inline-block" style={{ background: s.dot }} />
        <span className="text-[11.5px] font-semibold text-ink font-mono">{data.label}</span>
      </div>
      {data.datasetName && (
        <div className="text-[10px] text-ink-faint mt-1 font-mono">dataset: {data.datasetName}</div>
      )}
      <Handle id="right" type="source" position={Position.Right} style={{ background: s.border }} />
    </div>
  );
}

function DatasetNode({ data }) {
  return (
    <div className="rounded-[10px] px-3.5 py-2.5 bg-white border-[1.5px] border-line" style={{ minWidth: 170 }}>
      <Handle id="left" type="target" position={Position.Left} style={{ background: "#D4D4D8" }} />
      <div className="text-[10px] font-bold text-ink-faint uppercase tracking-wide mb-0.5">Dataset</div>
      <div className="text-[11.5px] font-semibold text-ink font-mono break-all">{data.label}</div>
      <Handle id="right" type="source" position={Position.Right} style={{ background: "#D4D4D8" }} />
    </div>
  );
}

const nodeTypes = { job: JobNode, dataset: DatasetNode };

// Simple topological layering: nodes with no incoming edges start at
// level 0; every other node sits one level past the deepest of its
// inputs. Enough for this graph's shape (a short dataset -> job ->
// dataset -> job -> dataset chain per pipeline) without pulling in a
// full layout library for what's still a handful of nodes.
function layoutGraph(rawNodes, rawEdges) {
  const incoming = new Map(rawNodes.map((n) => [n.id, []]));
  for (const e of rawEdges) {
    if (incoming.has(e.to)) incoming.get(e.to).push(e.from);
  }

  const level = new Map();
  function levelOf(id, seen = new Set()) {
    if (level.has(id)) return level.get(id);
    if (seen.has(id)) return 0; // cycle guard -- shouldn't happen with real DAG lineage, but never hang the page
    seen.add(id);
    const ins = incoming.get(id) || [];
    const l = ins.length === 0 ? 0 : Math.max(...ins.map((p) => levelOf(p, seen))) + 1;
    level.set(id, l);
    return l;
  }
  rawNodes.forEach((n) => levelOf(n.id));

  const byLevel = new Map();
  rawNodes.forEach((n) => {
    const l = level.get(n.id);
    if (!byLevel.has(l)) byLevel.set(l, []);
    byLevel.get(l).push(n);
  });

  const nodes = [];
  const colWidth = 260;
  const rowHeight = 90;
  for (const [l, group] of byLevel.entries()) {
    group.forEach((n, i) => {
      nodes.push({
        id: n.id,
        type: n.type,
        position: { x: l * colWidth, y: i * rowHeight },
        data: { label: n.label, datasetName: n.dataset_name, runState: n.run_state },
      });
    });
  }

  const edges = rawEdges.map((e, i) => ({
    id: `e${i}`,
    source: e.from,
    target: e.to,
    sourceHandle: "right",
    targetHandle: "left",
    markerEnd: { type: MarkerType.ArrowClosed, color: "#B7B7BC" },
    style: { stroke: "#B7B7BC", strokeWidth: 1.5 },
  }));

  return { nodes, edges };
}

export default function FieldLineage() {
  const [state, setState] = useState({ loading: true, data: null, error: null });

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await api.getFieldLineage();
        if (!cancelled) setState({ loading: false, data: res, error: res.error || null });
      } catch (e) {
        if (!cancelled) setState({ loading: false, data: null, error: e.message });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const graph = useMemo(() => {
    if (!state.data?.nodes?.length) return { nodes: [], edges: [] };
    return layoutGraph(state.data.nodes, state.data.edges);
  }, [state.data]);

  const hasGraph = graph.nodes.length > 0;

  return (
    <div className="p-7 md:px-8">
      <div className="mb-5">
        <h1 className="text-xl font-semibold text-ink tracking-tight">Field Lineage</h1>
        <p className="text-[13px] text-ink-faint mt-1">
          Real dataset → job → dataset lineage, traced from actual S3 storage paths each task read from and wrote to
        </p>
      </div>

      {state.error && (
        <div className="mb-4 text-[12.5px] text-danger bg-danger-soft border border-danger/20 rounded-xl px-4 py-3">
          Couldn't reach the catalog: {state.error}
        </div>
      )}

      {state.data && !state.data.configured && (
        <div className="mb-4 text-[12.5px] text-ink-soft bg-[#FFF8E8] border border-[#F0DFAE] rounded-xl px-4 py-3">
          Not connected yet — set <code className="font-mono">MARQUEZ_URL</code> on this service.
        </div>
      )}

      {state.data?.configured && !hasGraph && !state.loading && !state.error && (
        <div className="bg-white border border-line rounded-card px-6 py-8 text-center">
          <div className="text-[14px] font-semibold text-ink mb-1">No lineage recorded yet</div>
          <p className="text-[12.5px] text-ink-soft max-w-lg mx-auto leading-relaxed">
            Nothing appears here until a real pipeline run completes — run the Lakehouse pipeline from the Zones tab
            first.
          </p>
        </div>
      )}

      {hasGraph && (
        <div
          className="bg-white border border-line rounded-card shadow-[0_1px_2px_rgba(0,0,0,0.02),0_8px_24px_-12px_rgba(0,0,0,0.06)]"
          style={{ height: 420 }}
        >
          <ReactFlow
            nodes={graph.nodes}
            edges={graph.edges}
            nodeTypes={nodeTypes}
            fitView
            proOptions={{ hideAttribution: true }}
            nodesDraggable={false}
          >
            <Background color="#EEEEF0" gap={16} />
            <Controls showInteractive={false} />
          </ReactFlow>
        </div>
      )}

      {hasGraph && (
        <div className="mt-4 text-[11.5px] text-ink-faint leading-relaxed max-w-2xl">
          Built from each task's real inlets/outlets, not Marquez's own dataset registry (which stays empty for the
          Rust-based Marquez fork this stack runs) — see the Data Catalog tab's job list for the underlying runs.
        </div>
      )}

      <div className="mt-2 text-[11.5px] text-ink-faint">{state.loading ? "Loading lineage…" : ""}</div>
    </div>
  );
}
