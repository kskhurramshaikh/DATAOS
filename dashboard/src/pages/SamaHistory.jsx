import { Fragment, useEffect, useState } from "react";
import { api } from "../api";
import DatasetPicker from "../components/DatasetPicker";

// SAMA Compliance History (closes the "no trend-over-time exists" gap
// flagged 2026-08-19: SAMA has 8-domain detail + priority alerts +
// action items, but unlike NDI has no History page at all).
//
// Dataset-scoped throughout, unlike NDI History -- SAMA compliance is
// always computed for one dataset, same as the live SAMA Compliance
// page's own picker. See app/adapters/sama_history.py's module
// docstring for how this differs from NDI's history: SAMA's domain
// scores are real, data-derived signals, so genuine movement between
// two recorded snapshots is possible, not just a fixed-baseline series
// that reads identically until someone edits a config.

function formatWhen(ts) {
  if (!ts) return "—";
  return String(ts).replace("T", " ").slice(0, 16) + " UTC";
}

function Delta({ value }) {
  if (value === null || value === undefined) {
    return <span className="text-[11px] text-ink-faint">—</span>;
  }
  if (value === 0) {
    return <span className="text-[11px] text-ink-faint font-mono">no change</span>;
  }
  const up = value > 0;
  return (
    <span className={`text-[11px] font-mono font-semibold ${up ? "text-success" : "text-danger"}`}>
      {up ? "+" : ""}
      {value}
    </span>
  );
}

function Sparkline({ snapshots }) {
  // snapshots arrive newest-first; chart reads left-to-right oldest-first.
  // Only points with a real average_measured_score are plotted -- a
  // snapshot recorded before duplicate detection ever ran has no
  // measured domains yet, and a broken line through a missing point
  // would be worse than just skipping it.
  const points = [...snapshots].reverse().filter((p) => p.average_measured_score !== null);
  const W = 620;
  const H = 110;
  const pad = 14;
  if (points.length < 2) return null;
  const scores = points.map((p) => p.average_measured_score);
  const min = Math.min(...scores);
  const max = Math.max(...scores);
  const span = max - min || 1;
  const x = (i) => pad + (i * (W - pad * 2)) / Math.max(1, points.length - 1);
  const y = (v) => (max === min ? H / 2 : H - pad - ((v - min) / span) * (H - pad * 2));
  const path = points.map((p, i) => `${i === 0 ? "M" : "L"}${x(i)},${y(p.average_measured_score)}`).join(" ");

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full">
      <path d={path} fill="none" stroke="#0F7A6B" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      {points.map((p, i) => (
        <circle key={p.id} cx={x(i)} cy={y(p.average_measured_score)} r="3.4" fill="#0F7A6B" />
      ))}
    </svg>
  );
}

function DomainDetail({ snapshotId }) {
  const [state, setState] = useState({ loading: true, data: null, error: null });

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await api.getSamaSnapshot(snapshotId);
        if (!cancelled) setState({ loading: false, data: res, error: null });
      } catch (e) {
        if (!cancelled) setState({ loading: false, data: null, error: e.message });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [snapshotId]);

  if (state.loading) return <div className="px-4 py-3 text-[12px] text-ink-faint">Loading recorded detail…</div>;
  if (state.error) return <div className="px-4 py-3 text-[12px] text-danger">{state.error}</div>;

  return (
    <div className="px-4 py-3 bg-[#FAFAFB] border-t border-line">
      <div className="flex items-center justify-between mb-2">
        <div className="text-[11px] font-bold text-ink-faint uppercase tracking-wide">
          8 domains as recorded
        </div>
        <a
          href={api.samaSnapshotExportUrl(snapshotId)}
          className="text-[11px] font-semibold text-teal hover:underline"
        >
          Export CSV
        </a>
      </div>
      <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-8 gap-2.5">
        {state.data.domain_scores.map((d) => (
          <div key={d.code} className="bg-white border border-line rounded-xl px-3 py-2">
            <div className="text-[10.5px] font-bold text-ink-faint font-mono">{d.code}</div>
            <div className="text-[15px] font-semibold font-mono text-ink leading-tight">
              {d.score !== null ? `${d.score}%` : "—"}
            </div>
            <div className="text-[10.5px] text-ink-soft">{d.status === "measured" ? "measured" : "not measured"}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

export default function SamaHistory() {
  const [datasets, setDatasets] = useState([]);
  const [datasetsLoaded, setDatasetsLoaded] = useState(false);
  const [selected, setSelected] = useState("");
  const [state, setState] = useState({ loading: false, data: null, error: null });
  const [expanded, setExpanded] = useState(null);
  const [recorderName, setRecorderName] = useState("");
  const [recording, setRecording] = useState(false);
  const [recordError, setRecordError] = useState(null);

  useEffect(() => {
    let cancelled = false;
    async function loadDatasets() {
      try {
        const res = await api.getDatasets();
        if (cancelled) return;
        const list = res.datasets ?? [];
        setDatasets(list);
        if (list.length === 1) setSelected(list[0].dataset_name);
        setDatasetsLoaded(true);
      } catch (e) {
        if (!cancelled) {
          setDatasetsLoaded(true);
          setState((s) => ({ ...s, error: e.message }));
        }
      }
    }
    loadDatasets();
    return () => {
      cancelled = true;
    };
  }, []);

  async function loadHistory(datasetName) {
    if (!datasetName) {
      setState({ loading: false, data: null, error: null });
      return;
    }
    setState((s) => ({ ...s, loading: true }));
    try {
      const res = await api.getSamaHistory(datasetName);
      setState({ loading: false, data: res, error: null });
    } catch (e) {
      setState({ loading: false, data: null, error: e.message });
    }
  }

  useEffect(() => {
    setExpanded(null);
    loadHistory(selected);
  }, [selected]);

  async function handleRecord() {
    const name = recorderName.trim() || "dashboard reviewer";
    setRecording(true);
    setRecordError(null);
    try {
      await api.recordSamaSnapshot(selected, name);
      await loadHistory(selected);
    } catch (e) {
      setRecordError(e.message);
    } finally {
      setRecording(false);
    }
  }

  const d = state.data;
  const snapshots = d?.snapshots ?? [];

  return (
    <div className="p-7 md:px-8">
      <div className="mb-5 flex items-start justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-xl font-semibold text-ink tracking-tight">SAMA Compliance History</h1>
          <p className="text-[13px] text-ink-faint mt-1">
            Recorded SAMA snapshots for one dataset — when, by whom, and the real measured average at that moment
          </p>
        </div>
        <DatasetPicker datasets={datasets} value={selected} onChange={setSelected} />
      </div>

      {!selected && datasetsLoaded && datasets.length > 1 && (
        <div className="mb-4 text-[12.5px] text-ink-soft bg-[#FAFAFB] border border-line rounded-xl px-4 py-3">
          Select a dataset above to view or record its SAMA compliance history.
        </div>
      )}
      {!selected && datasetsLoaded && datasets.length === 0 && (
        <div className="mb-4 text-[12.5px] text-ink-faint bg-[#FAFAFB] border border-line rounded-xl px-4 py-3">
          No datasets yet — upload one from the MDM tab first.
        </div>
      )}

      {state.error && (
        <div className="mb-4 text-[12.5px] text-danger bg-danger-soft border border-danger/20 rounded-xl px-4 py-3">
          Couldn't reach the API: {state.error}
        </div>
      )}

      {selected && (
        <div className="bg-white border border-line rounded-card px-6 py-4 mb-5">
          <div className="text-[12px] font-bold text-ink-faint uppercase tracking-wide mb-2.5">
            Record a snapshot
          </div>
          <div className="flex flex-wrap items-center gap-2.5">
            <input
              value={recorderName}
              onChange={(e) => setRecorderName(e.target.value)}
              placeholder="your name"
              className="text-[12.5px] border border-line rounded-lg px-3 py-1.5 w-44"
            />
            <button
              onClick={handleRecord}
              disabled={recording}
              className="text-[12px] font-semibold text-white bg-teal px-3.5 py-1.5 rounded-lg hover:opacity-90 disabled:opacity-50"
            >
              {recording ? "Recording…" : "Record current SAMA view"}
            </button>
          </div>
          {recordError && <div className="mt-2.5 text-[12px] text-danger">{recordError}</div>}
        </div>
      )}

      {selected && d && snapshots.length === 0 && (
        <div className="bg-white border border-line rounded-card px-6 py-8 text-center">
          <div className="text-[14px] font-semibold text-ink mb-1">No snapshots recorded yet</div>
          <p className="text-[12.5px] text-ink-soft max-w-lg mx-auto leading-relaxed">
            Record one above to start this dataset's SAMA compliance history.
          </p>
        </div>
      )}

      {selected && d && snapshots.length > 0 && (
        <>
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-5">
            <div className="bg-white border border-line rounded-2xl px-5 py-4">
              <div className="text-[11px] font-bold text-ink-faint uppercase tracking-wide">Records</div>
              <div className="text-[26px] font-semibold font-mono text-ink mt-1">{d.count}</div>
            </div>
            <div className="bg-white border border-line rounded-2xl px-5 py-4">
              <div className="text-[11px] font-bold text-ink-faint uppercase tracking-wide">Latest average</div>
              <div className="text-[26px] font-semibold font-mono text-teal mt-1">
                {snapshots[0].average_measured_score !== null ? `${snapshots[0].average_measured_score}%` : "—"}
              </div>
            </div>
            <div className="bg-white border border-line rounded-2xl px-5 py-4 col-span-2">
              <div className="text-[11px] font-bold text-ink-faint uppercase tracking-wide">Last recorded</div>
              <div className="text-[13.5px] font-mono text-ink mt-2 leading-tight">{formatWhen(snapshots[0].recorded_at)}</div>
              <div className="text-[11.5px] text-ink-soft mt-0.5 truncate">{snapshots[0].recorded_by}</div>
            </div>
          </div>

          <div className="bg-white border border-line rounded-card px-6 py-4 mb-5">
            <div className="text-[12px] font-bold text-ink-faint uppercase tracking-wide mb-2">
              Average measured domain score over recorded snapshots
            </div>
            {snapshots.filter((s) => s.average_measured_score !== null).length < 2 ? (
              <div className="text-[12.5px] text-ink-soft py-6 text-center">
                Not enough measured records yet for a line — a trend needs at least two snapshots with a measured score.
              </div>
            ) : (
              <Sparkline snapshots={snapshots} />
            )}
          </div>

          <div className="bg-white border border-line rounded-card overflow-hidden">
            <table className="w-full text-left">
              <thead>
                <tr className="border-b border-line">
                  <th className="py-2.5 px-5 text-[10.5px] font-bold text-ink-faint uppercase tracking-wide">Recorded</th>
                  <th className="py-2.5 px-3 text-[10.5px] font-bold text-ink-faint uppercase tracking-wide">By</th>
                  <th className="py-2.5 px-3 text-[10.5px] font-bold text-ink-faint uppercase tracking-wide text-right">Average</th>
                  <th className="py-2.5 px-3 text-[10.5px] font-bold text-ink-faint uppercase tracking-wide">Priority alert</th>
                  <th className="py-2.5 px-5 text-[10.5px] font-bold text-ink-faint uppercase tracking-wide text-right">Detail</th>
                </tr>
              </thead>
              <tbody>
                {snapshots.map((s) => (
                  <Fragment key={s.id}>
                    <tr className="border-b border-line last:border-0">
                      <td className="py-3 px-5 text-[12px] font-mono text-ink">{formatWhen(s.recorded_at)}</td>
                      <td className="py-3 px-3 text-[12.5px] text-ink-soft">{s.recorded_by}</td>
                      <td className="py-3 px-3 text-right">
                        <div className="text-[13px] font-mono font-semibold text-ink">
                          {s.average_measured_score !== null ? `${s.average_measured_score}%` : "—"}
                        </div>
                        <Delta value={s.delta_average_score} />
                      </td>
                      <td className="py-3 px-3 text-[11.5px] text-ink-soft max-w-sm truncate" title={s.priority_alert}>
                        {s.priority_alert}
                      </td>
                      <td className="py-3 px-5 text-right">
                        <button
                          onClick={() => setExpanded(expanded === s.id ? null : s.id)}
                          className="text-[11.5px] font-semibold text-teal hover:underline"
                        >
                          {expanded === s.id ? "Hide" : "View domains"}
                        </button>
                      </td>
                    </tr>
                    {expanded === s.id && (
                      <tr>
                        <td colSpan={5} className="p-0">
                          <DomainDetail snapshotId={s.id} />
                        </td>
                      </tr>
                    )}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </div>

          <div className="mt-4 flex items-center justify-between flex-wrap gap-2">
            <div className="text-[11px] text-ink-faint leading-relaxed max-w-3xl">{d.note}</div>
            <a
              href={api.samaHistoryExportUrl(selected)}
              className="text-[12px] font-semibold text-teal bg-teal-soft px-3.5 py-2 rounded-lg hover:opacity-80 shrink-0"
            >
              Export CSV
            </a>
          </div>
        </>
      )}

      <div className="mt-4 text-[11.5px] text-ink-faint">{state.loading ? "Loading history…" : ""}</div>
    </div>
  );
}
