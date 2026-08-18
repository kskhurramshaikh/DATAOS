import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import DatasetPicker from "../components/DatasetPicker";

const ALL_DATASETS = { value: "__all__", label: "All datasets", sublabel: "Full compliance history, every dataset" };

function StatusPill({ status }) {
  const confirmed = status === "confirmed_duplicate";
  return (
    <span
      className={`text-[10.5px] font-semibold px-2 py-0.5 rounded-full ${
        confirmed ? "bg-success-soft text-success" : "bg-[#F4F4F5] text-ink-faint"
      }`}
    >
      {confirmed ? "Merged" : "Rejected"}
    </span>
  );
}

// Small stat tile -- same visual language as SAMA's domain cards, so
// the two pages under Governance feel like one family. Uses the wide
// horizontal space at the top of the page instead of leaving it as a
// bare title bar before a narrow table.
function StatTile({ label, value, tone = "ink" }) {
  const toneCls = { ink: "text-ink", success: "text-success", danger: "text-danger", gold: "text-gold" }[tone];
  return (
    <div className="bg-white border border-line rounded-2xl px-4 py-3.5 flex flex-col gap-1">
      <div className={`text-2xl font-semibold font-mono ${toneCls}`}>{value}</div>
      <div className="text-[11px] text-ink-soft leading-tight">{label}</div>
    </div>
  );
}

function AuditRow({ entry, showDataset }) {
  return (
    <tr className="border-b border-line last:border-0">
      <td className="py-2.5 pr-4 text-[12px] text-ink font-medium">{entry.members?.[0]?.name ?? "—"}</td>
      <td className="py-2.5 pr-4 text-[11.5px] text-ink-faint">{entry.members?.length ?? 0} records</td>
      {showDataset && <td className="py-2.5 pr-4 text-[11.5px] text-ink-soft font-mono">{entry.dataset_safe_name}</td>}
      <td className="py-2.5 pr-4">
        <span
          className={`text-[10px] font-bold px-2 py-0.5 rounded-full ${
            entry.confidence_tier === "high_confidence" ? "bg-success-soft text-success" : "bg-[#FFF8E8] text-[#B8952E]"
          }`}
        >
          {entry.confidence_tier === "high_confidence" ? "High confidence" : "Needs review"}
        </span>
      </td>
      <td className="py-2.5 pr-4">
        <StatusPill status={entry.status} />
      </td>
      <td className="py-2.5 pr-4 text-[11.5px] text-ink-faint">{entry.decided_by}</td>
      <td className="py-2.5 text-[11.5px] text-ink-faint font-mono">{entry.decided_at?.slice(0, 16).replace("T", " ")}</td>
    </tr>
  );
}

export default function AuditLog() {
  const [datasets, setDatasets] = useState([]);
  const [datasetsLoaded, setDatasetsLoaded] = useState(false);
  const [selected, setSelected] = useState("");
  const [state, setState] = useState({ loading: false, entries: [], error: null });

  // Same picker pattern as SamaDashboard / GoldenRecordRegistry:
  // always visible, auto-select the one real choice, never fetch
  // while several datasets exist and none is picked. The one
  // difference here is ALL_DATASETS -- a real, explicitly-chosen
  // option (not a default), because a compliance log genuinely
  // benefits from a full cross-dataset view sometimes.
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

  useEffect(() => {
    if (!selected) {
      setState({ loading: false, entries: [], error: null });
      return;
    }
    let cancelled = false;
    async function load() {
      setState((s) => ({ ...s, loading: true }));
      try {
        const datasetName = selected === ALL_DATASETS.value ? undefined : selected;
        const res = await api.getAuditLog(datasetName);
        if (!cancelled) setState({ loading: false, entries: res.entries ?? [], error: null });
      } catch (e) {
        if (!cancelled) setState({ loading: false, entries: [], error: e.message });
      }
    }
    load();
    const interval = setInterval(load, 15000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [selected]);

  const stats = useMemo(() => {
    const merged = state.entries.filter((e) => e.status === "confirmed_duplicate").length;
    const rejected = state.entries.length - merged;
    const highConf = state.entries.filter((e) => e.confidence_tier === "high_confidence").length;
    return { total: state.entries.length, merged, rejected, highConf };
  }, [state.entries]);

  const showDatasetColumn = selected === ALL_DATASETS.value;

  return (
    <div className="p-7 md:px-8">
      <div className="mb-5 flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-xl font-semibold text-ink tracking-tight">Audit Log</h1>
          <p className="text-[13px] text-ink-faint mt-1">
            Every duplicate-review decision — who decided what, and when. The durable compliance record, independent of any one chat session.
          </p>
        </div>
        <DatasetPicker datasets={datasets} value={selected} onChange={setSelected} allOption={ALL_DATASETS} />
      </div>

      {!selected && datasetsLoaded && datasets.length > 1 && (
        <div className="mb-4 text-[12.5px] text-ink-soft bg-[#FAFAFB] border border-line rounded-xl px-4 py-3">
          Select a dataset above — or choose "All datasets" for the full compliance history.
        </div>
      )}
      {!selected && datasetsLoaded && datasets.length === 0 && (
        <div className="mb-4 text-[12.5px] text-ink-faint bg-[#FAFAFB] border border-line rounded-xl px-4 py-3">
          No datasets yet — upload one and run duplicate detection to start building an audit history.
        </div>
      )}

      {state.error && (
        <div className="mb-4 text-[12.5px] text-danger bg-danger-soft border border-danger/20 rounded-xl px-4 py-3">
          Couldn't reach the API: {state.error}
        </div>
      )}

      {selected && (
        <>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-6">
            <StatTile label="Total decisions" value={stats.total} />
            <StatTile label="Merged" value={stats.merged} tone="success" />
            <StatTile label="Rejected" value={stats.rejected} />
            <StatTile label="High confidence" value={stats.highConf} tone="gold" />
          </div>

          <div className="bg-white border border-line rounded-card px-6 py-5">
            {state.entries.length === 0 && !state.loading ? (
              <div className="text-[12.5px] text-ink-faint py-4 text-center">
                No decisions recorded yet — Confirm or Reject a cluster in the Duplicate Queue to see it here.
              </div>
            ) : (
              <table className="w-full">
                <thead>
                  <tr className="border-b border-line text-left">
                    <th className="pb-2 pr-4 text-[10.5px] font-bold text-ink-faint uppercase tracking-wide">Person</th>
                    <th className="pb-2 pr-4 text-[10.5px] font-bold text-ink-faint uppercase tracking-wide">Cluster</th>
                    {showDatasetColumn && <th className="pb-2 pr-4 text-[10.5px] font-bold text-ink-faint uppercase tracking-wide">Dataset</th>}
                    <th className="pb-2 pr-4 text-[10.5px] font-bold text-ink-faint uppercase tracking-wide">Confidence</th>
                    <th className="pb-2 pr-4 text-[10.5px] font-bold text-ink-faint uppercase tracking-wide">Decision</th>
                    <th className="pb-2 pr-4 text-[10.5px] font-bold text-ink-faint uppercase tracking-wide">Decided by</th>
                    <th className="pb-2 text-[10.5px] font-bold text-ink-faint uppercase tracking-wide">When</th>
                  </tr>
                </thead>
                <tbody>
                  {state.entries.map((e) => (
                    <AuditRow key={e.id} entry={e} showDataset={showDatasetColumn} />
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </>
      )}

      <div className="mt-4 text-[11.5px] text-ink-faint">
        {state.loading ? "Loading live data…" : "Live data — refreshes every 15s."}
      </div>
    </div>
  );
}
