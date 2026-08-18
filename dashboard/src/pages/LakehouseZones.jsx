import { useEffect, useState } from "react";
import { api } from "../api";
import FlowConnector from "../components/FlowConnector";
import DatasetPicker from "../components/DatasetPicker";
import TriggerPipelineButton from "../components/TriggerPipelineButton";
import PipelineRunStatus from "../components/PipelineRunStatus";

// Tailwind's build only includes classes it can find as complete literal
// strings during its static scan -- a template literal like
// `bg-${color}-soft` is invisible to that scan, so the styles would
// silently be missing from the built CSS. Full literal strings per zone
// avoids that trap.
const ZONE_META = {
  bronze: {
    label: "Bronze",
    note: "Raw — unmodified",
    card: "bg-bronze-soft border-bronze/20",
    dot: "bg-bronze",
    text: "text-bronze",
  },
  silver: {
    label: "Silver",
    note: "Cleaned — header-verified",
    card: "bg-silver-soft border-silver/20",
    dot: "bg-silver",
    text: "text-silver",
  },
  gold: {
    label: "Gold",
    note: "Governed — IFRS 9 / NDI",
    card: "bg-gold-soft border-gold/20",
    dot: "bg-gold",
    text: "text-gold",
  },
};

// Real browser localStorage -- this is a genuine deployed SPA served
// from FastAPI, not a sandboxed Artifact preview, so persisting across
// reloads here is normal and safe. Shared with Pipeline Monitoring so
// picking a dataset on either page carries over to the other, and a
// page refresh doesn't reset to "no dataset selected" for a dataset
// that's already gone through the pipeline.
const SELECTED_DATASET_KEY = "dataos:lakehouse:selectedDataset";

function formatBytes(n) {
  if (n == null) return "—";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

function ZoneCard({ zoneKey, data }) {
  const meta = ZONE_META[zoneKey];

  if (!data || data.error) {
    return (
      <div className={`${meta.card} border rounded-2xl px-5 py-4 min-w-[168px] flex flex-col gap-1.5`}>
        <div className={`inline-flex items-center gap-1.5 text-xs font-bold ${meta.text} w-fit`}>
          <span className={`w-1.5 h-1.5 rounded-full ${meta.dot} inline-block`} />
          {meta.label}
        </div>
        <div className="text-[11px] text-ink-faint mt-1">
          {data?.error ? "Couldn't load — check connection" : "No data yet"}
        </div>
      </div>
    );
  }

  const sizeOrRows = zoneKey === "bronze" ? formatBytes(data.size_bytes) : `${data.rows ?? 0} rows`;

  return (
    <div className={`${meta.card} border rounded-2xl px-5 py-4 min-w-[168px] flex flex-col gap-1.5`}>
      <div className={`inline-flex items-center gap-1.5 text-xs font-bold ${meta.text} w-fit`}>
        <span className={`w-1.5 h-1.5 rounded-full ${meta.dot} inline-block`} />
        {meta.label}
      </div>
      <div className="text-[11.5px] text-ink-soft">
        <span className="font-mono font-semibold text-ink">{data.tables ?? 0}</span> {zoneKey === "bronze" ? "files" : "tables"}
      </div>
      <div className="text-[11.5px] text-ink-soft">
        <span className="font-mono font-semibold text-ink">{sizeOrRows}</span>
      </div>
      <div className="text-[10.5px] text-ink-faint mt-0.5">{meta.note}</div>
      <div className={`text-[10px] ${meta.text} mt-1 font-mono`}>● updated {data.freshness}</div>
    </div>
  );
}

export default function LakehouseZones() {
  const [datasets, setDatasets] = useState([]);
  const [datasetsLoaded, setDatasetsLoaded] = useState(false);
  const [selected, setSelected] = useState(() => {
    try {
      return localStorage.getItem(SELECTED_DATASET_KEY) || "";
    } catch {
      return "";
    }
  });
  const [state, setState] = useState({ loading: false, configured: true, zones: {}, error: null });
  const [refreshTick, setRefreshTick] = useState(0);

  function selectDataset(name) {
    setSelected(name);
    try {
      if (name) localStorage.setItem(SELECTED_DATASET_KEY, name);
      else localStorage.removeItem(SELECTED_DATASET_KEY);
    } catch {
      // Non-fatal -- selection still works for this session even if storage is unavailable.
    }
  }

  // Same DatasetPicker pattern as the rest of the dashboard (SAMA,
  // Golden Records, Audit Log) -- DataOS is a multi-dataset platform
  // now, so every page scoped to a dataset gets this picker, this app.
  // The remembered selection (if any) is validated against the real
  // dataset list once it loads -- a stale name from a deleted dataset
  // is dropped rather than left pointing at nothing.
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
    if (!selected) {
      setState({ loading: false, configured: true, zones: {}, error: null });
      return;
    }
    let cancelled = false;
    async function load() {
      try {
        const res = await api.getZones(selected);
        if (!cancelled) setState({ loading: false, configured: res.configured, zones: res.zones, error: res.error || null });
      } catch (e) {
        if (!cancelled) setState({ loading: false, configured: true, zones: {}, error: e.message });
      }
    }
    load();
    const interval = setInterval(load, 15000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [selected, refreshTick]);

  return (
    <div className="p-7 md:px-8">
      <div className="mb-5 flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-xl font-semibold text-ink tracking-tight">Lakehouse Zones</h1>
          <p className="text-[13px] text-ink-faint mt-1">
            Bronze → Silver → Gold, backed by real Iceberg tables in SeaweedFS
          </p>
        </div>
        <div className="flex items-start gap-3 flex-wrap">
          <DatasetPicker datasets={datasets} value={selected} onChange={selectDataset} />
          <TriggerPipelineButton datasetName={selected} onTriggered={() => setRefreshTick((t) => t + 1)} />
        </div>
      </div>

      {!selected && datasetsLoaded && datasets.length > 1 && (
        <div className="mb-4 text-[12.5px] text-ink-soft bg-[#FAFAFB] border border-line rounded-xl px-4 py-3">
          Select a dataset above to view its Lakehouse Zones.
        </div>
      )}
      {!selected && datasetsLoaded && datasets.length === 0 && (
        <div className="mb-4 text-[12.5px] text-ink-faint bg-[#FAFAFB] border border-line rounded-xl px-4 py-3">
          No datasets yet — upload one from the MDM tab first.
        </div>
      )}

      {selected && !state.configured && (
        <div className="mb-4 text-[12.5px] text-ink-soft bg-[#FFF8E8] border border-[#F0DFAE] rounded-xl px-4 py-3">
          Not connected yet — set <code className="font-mono">LAKEHOUSE_DB_URI</code> and{" "}
          <code className="font-mono">SEAWEEDFS_INTERNAL_HOST</code> on this service to see live data.
        </div>
      )}
      {state.error && (
        <div className="mb-4 text-[12.5px] text-danger bg-danger-soft border border-danger/20 rounded-xl px-4 py-3">
          Couldn't reach the API: {state.error}
        </div>
      )}

      {selected && (
        <>
          <div className="mb-4">
            <PipelineRunStatus datasetName={selected} onRunSettled={() => setRefreshTick((t) => t + 1)} />
          </div>

          <div className="bg-white border border-line rounded-card px-7 py-6 shadow-[0_1px_2px_rgba(0,0,0,0.02),0_8px_24px_-12px_rgba(0,0,0,0.06)]">
            <div className="flex items-center overflow-x-auto pb-1">
              <div className="flex flex-col gap-1.5 min-w-[128px]">
                <span className="text-[10px] font-bold text-[#B0B0B5] tracking-wide uppercase mb-0.5">Source</span>
                <div className="bg-[#FAFAFB] border border-line rounded-lg px-2.5 py-1.5 text-[11.5px] text-ink-soft font-mono truncate max-w-[128px]">
                  {selected}
                </div>
              </div>

              <FlowConnector label="Bronze ingest" />
              <ZoneCard zoneKey="bronze" data={state.zones.bronze} />
              <FlowConnector label="Clean + validate" />
              <ZoneCard zoneKey="silver" data={state.zones.silver} />
              <FlowConnector label="Business logic" />
              <ZoneCard zoneKey="gold" data={state.zones.gold} />
              <FlowConnector label="BI / Copilot" />

              <div className="flex flex-col gap-1.5 min-w-[128px]">
                <span className="text-[10px] font-bold text-[#B0B0B5] tracking-wide uppercase mb-0.5">Consumption</span>
                <div className="bg-[#FAFAFB] border border-line rounded-lg px-2.5 py-1.5 text-[11.5px] text-ink-soft">
                  IFRS 9 / NDI views
                </div>
              </div>
            </div>
          </div>

          <div className="mt-4 text-[11.5px] text-ink-faint">
            {state.loading ? "Loading live data…" : "Live data — refreshes every 15s. \"Run pipeline\" is manual by design — nothing here runs automatically on upload."}
          </div>
        </>
      )}
    </div>
  );
}
