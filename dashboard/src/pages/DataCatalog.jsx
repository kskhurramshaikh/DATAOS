import { useEffect, useState } from "react";
import { api } from "../api";

// Data Catalog (Dev Queue item 6, first half). Reads the real job/run
// registry from Marquez -- see app/marquez_client.py's module
// docstring for exactly what's queried and why (including the
// resource-wall reasoning for building this instead of OpenMetadata,
// sent to #one-tech-ai 2026-08-18 before any of this was built).
//
// Same honesty principle as every other dashboard page here: this is
// NOT a full data catalog (no PII tagging, no business glossary, no
// search across types) -- it's a real, live view of what Airflow's
// OpenLineage integration actually captured. Every row below is a
// real job Marquez has seen a lineage event for, not a static list.

const STATE_STYLE = {
  COMPLETED: { bg: "#EAF6F1", text: "#0F7A6B", dot: "#2FA37E", label: "Completed" },
  RUNNING: { bg: "#EAF1FB", text: "#3E7BD6", dot: "#3E7BD6", label: "Running" },
  FAILED: { bg: "#FBEAEA", text: "#D6483E", dot: "#D6483E", label: "Failed" },
};

function stateMeta(state) {
  return STATE_STYLE[state] || { bg: "#F4F4F5", text: "#8E8E93", dot: "#8E8E93", label: state || "Unknown" };
}

function StatusChip({ state }) {
  const s = stateMeta(state);
  return (
    <span
      className="inline-flex items-center gap-1.5 text-[11px] font-semibold px-2.5 py-1 rounded-full"
      style={{ background: s.bg, color: s.text }}
    >
      <span className="w-1.5 h-1.5 rounded-full inline-block" style={{ background: s.dot }} />
      {s.label}
    </span>
  );
}

function formatWhen(ts) {
  if (!ts) return "—";
  return String(ts).replace("T", " ").slice(0, 19) + " UTC";
}

function formatDuration(ms) {
  if (ms == null) return "—";
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

function RunHistory({ jobName, onClose }) {
  const [state, setState] = useState({ loading: true, data: null, error: null });

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await api.getCatalogJobRuns(jobName, 10);
        if (!cancelled) setState({ loading: false, data: res, error: res.error || null });
      } catch (e) {
        if (!cancelled) setState({ loading: false, data: null, error: e.message });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [jobName]);

  return (
    <tr>
      <td colSpan={5} className="p-0">
        <div className="px-5 py-4 bg-[#FAFAFB] border-t border-line">
          <div className="flex items-center justify-between mb-2.5">
            <div className="text-[11px] font-bold text-ink-faint uppercase tracking-wide">
              Recent runs — {jobName}
            </div>
            <button onClick={onClose} className="text-[11.5px] font-semibold text-teal hover:underline">
              Close
            </button>
          </div>
          {state.loading && <div className="text-[12px] text-ink-faint">Loading…</div>}
          {state.error && <div className="text-[12px] text-danger">{state.error}</div>}
          {state.data?.runs?.length === 0 && !state.loading && (
            <div className="text-[12px] text-ink-faint">No runs found.</div>
          )}
          {state.data?.runs?.length > 0 && (
            <div className="space-y-1.5">
              {state.data.runs.map((r) => (
                <div
                  key={r.id}
                  className="flex items-center justify-between bg-white border border-line rounded-lg px-3.5 py-2"
                >
                  <div className="flex items-center gap-3">
                    <StatusChip state={r.state} />
                    <span className="text-[12px] font-mono text-ink-soft">{r.run_id || r.id}</span>
                    {r.dataset_name && (
                      <span className="text-[11px] text-ink-faint bg-[#F2F2F4] px-2 py-0.5 rounded-full font-mono">
                        {r.dataset_name}
                      </span>
                    )}
                  </div>
                  <div className="flex items-center gap-3 text-[11.5px] text-ink-faint font-mono">
                    {r.error_message && <span className="text-danger">{r.error_message}</span>}
                    <span>{formatDuration(r.duration_ms)}</span>
                    <span>{formatWhen(r.started_at)}</span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </td>
    </tr>
  );
}

function JobRow({ job, expanded, onToggle }) {
  const isDag = job.job_type === "DAG";
  const run = job.latest_run;
  return (
    <>
      <tr className="border-b border-line last:border-0 hover:bg-[#FAFAFB]">
        <td className="py-3 px-5">
          <div className="flex items-center gap-2">
            {!isDag && <span className="w-3 border-t border-line ml-1" />}
            <span className={`text-[12.5px] font-mono ${isDag ? "font-semibold text-ink" : "text-ink-soft"}`}>
              {job.simple_name}
            </span>
            {isDag && (
              <span className="text-[9px] font-bold px-1.5 py-0.5 rounded-full bg-teal-soft text-teal">DAG</span>
            )}
          </div>
          {job.description && isDag && (
            <div className="text-[11px] text-ink-faint mt-0.5 max-w-md">{job.description}</div>
          )}
        </td>
        <td className="py-3 px-3">{run ? <StatusChip state={run.state} /> : <span className="text-[11px] text-ink-faint">never run</span>}</td>
        <td className="py-3 px-3 text-[12px] text-ink-soft font-mono">{run?.dataset_name ?? "—"}</td>
        <td className="py-3 px-3 text-[12px] text-ink-soft font-mono text-right">{formatDuration(run?.duration_ms)}</td>
        <td className="py-3 px-5 text-right">
          {run && (
            <button onClick={onToggle} className="text-[11.5px] font-semibold text-teal hover:underline">
              {expanded ? "Hide runs" : "View runs"}
            </button>
          )}
        </td>
      </tr>
      {expanded && <RunHistory jobName={job.name} onClose={onToggle} />}
    </>
  );
}

export default function DataCatalog() {
  const [state, setState] = useState({ loading: true, data: null, error: null });
  const [expandedJob, setExpandedJob] = useState(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await api.getCatalogJobs();
        if (!cancelled) setState({ loading: false, data: res, error: res.error || null });
      } catch (e) {
        if (!cancelled) setState({ loading: false, data: null, error: e.message });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const jobs = state.data?.jobs ?? [];

  return (
    <div className="p-7 md:px-8">
      <div className="mb-5">
        <h1 className="text-xl font-semibold text-ink tracking-tight">Data Catalog</h1>
        <p className="text-[13px] text-ink-faint mt-1">
          Every DAG and task Marquez has seen a real OpenLineage event for — live from{" "}
          <code className="font-mono">dataos-marquez.onrender.com</code>
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

      {state.data?.configured && jobs.length === 0 && !state.loading && !state.error && (
        <div className="bg-white border border-line rounded-card px-6 py-8 text-center">
          <div className="text-[14px] font-semibold text-ink mb-1">No jobs registered yet</div>
          <p className="text-[12.5px] text-ink-soft max-w-lg mx-auto leading-relaxed">
            Nothing appears here until a real DAG run happens — run the Lakehouse pipeline from the Zones tab first.
          </p>
        </div>
      )}

      {jobs.length > 0 && (
        <div className="bg-white border border-line rounded-card overflow-hidden">
          <table className="w-full text-left">
            <thead>
              <tr className="border-b border-line">
                <th className="py-2.5 px-5 text-[10.5px] font-bold text-ink-faint uppercase tracking-wide">Job</th>
                <th className="py-2.5 px-3 text-[10.5px] font-bold text-ink-faint uppercase tracking-wide">Latest run</th>
                <th className="py-2.5 px-3 text-[10.5px] font-bold text-ink-faint uppercase tracking-wide">Dataset</th>
                <th className="py-2.5 px-3 text-[10.5px] font-bold text-ink-faint uppercase tracking-wide text-right">Duration</th>
                <th className="py-2.5 px-5 text-[10.5px] font-bold text-ink-faint uppercase tracking-wide text-right">History</th>
              </tr>
            </thead>
            <tbody>
              {jobs.map((job) => (
                <JobRow
                  key={job.name}
                  job={job}
                  expanded={expandedJob === job.name}
                  onToggle={() => setExpandedJob(expandedJob === job.name ? null : job.name)}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="mt-4 text-[11.5px] text-ink-faint">{state.loading ? "Loading catalog…" : ""}</div>
    </div>
  );
}
