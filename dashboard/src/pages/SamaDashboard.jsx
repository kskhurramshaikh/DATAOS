import { useEffect, useState } from "react";
import { api } from "../api";
import DatasetPicker from "../components/DatasetPicker";

const STATUS_META = {
  ok: { label: "OK", cls: "bg-success-soft text-success" },
  warn: { label: "Attention", cls: "bg-[#FFF8E8] text-[#B8952E]" },
  not_measured: { label: "Not measured", cls: "bg-[#F4F4F5] text-ink-faint" },
};

function CheckRow({ check }) {
  const meta = STATUS_META[check.status] ?? STATUS_META.not_measured;
  return (
    <div className="flex items-center justify-between py-2.5 border-b border-line last:border-0">
      <div className="text-[12.5px] font-medium text-ink">{check.label}</div>
      <div className="flex items-center gap-3">
        <span className="text-[11.5px] text-ink-faint">{check.value}</span>
        <span className={`text-[10px] font-bold px-2 py-0.5 rounded-full ${meta.cls}`}>{meta.label}</span>
      </div>
    </div>
  );
}

function DomainCard({ domain }) {
  const measured = domain.status === "measured";
  const score = domain.score;
  const scoreColor = !measured ? "text-ink-faint" : score >= 80 ? "text-success" : score >= 50 ? "text-[#B8952E]" : "text-danger";
  return (
    <div className="bg-white border border-line rounded-2xl px-4 py-3.5 flex flex-col gap-1">
      <div className="flex items-center justify-between">
        <span className="text-[11px] font-bold text-ink-faint tracking-wide">{domain.code}</span>
        {!measured && <span className="text-[9.5px] font-semibold px-1.5 py-0.5 rounded-full bg-[#F4F4F5] text-ink-faint">N/A</span>}
      </div>
      <div className={`text-2xl font-semibold font-mono ${scoreColor}`}>{measured ? `${score}%` : "—"}</div>
      <div className="text-[11px] text-ink-soft leading-tight">{domain.name}</div>
    </div>
  );
}

export default function SamaDashboard() {
  const [datasets, setDatasets] = useState([]);
  const [datasetsLoaded, setDatasetsLoaded] = useState(false);
  const [selected, setSelected] = useState("");
  const [state, setState] = useState({ loading: true, data: null, error: null });

  // Loads the dataset list once, up front -- the picker is always
  // visible (not something that only appears after an error), and
  // when there's exactly one dataset there's nothing to actually
  // choose, so it's auto-selected rather than making the person click
  // through a picker with one option in it.
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
          setState((s) => ({ ...s, loading: false, error: e.message }));
        }
      }
    }
    loadDatasets();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!datasetsLoaded) return;
    // Fetch when a dataset is selected, or when there are genuinely no
    // datasets at all (renders the honest not_measured empty state).
    // Never fetch while several datasets exist and none is picked --
    // that's exactly the ambiguous case that used to surface as a raw
    // 400; the picker prevents it from ever being requested at all.
    if (!selected && datasets.length !== 0) return;
    let cancelled = false;
    async function load() {
      setState((s) => ({ ...s, loading: true }));
      try {
        const res = await api.getSama(selected || undefined);
        if (!cancelled) setState({ loading: false, data: res, error: null });
      } catch (e) {
        if (!cancelled) setState({ loading: false, data: null, error: e.message });
      }
    }
    load();
    const interval = setInterval(load, 15000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [selected, datasets.length, datasetsLoaded]);

  return (
    <div className="p-7 md:px-8">
      <div className="mb-5 flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-xl font-semibold text-ink tracking-tight">SAMA Compliance</h1>
          <p className="text-[13px] text-ink-faint mt-1">8-domain SAMA data-governance view, computed from real signals</p>
        </div>
        <DatasetPicker datasets={datasets} value={selected} onChange={setSelected} />
      </div>

      {!selected && datasets.length > 1 && (
        <div className="mb-4 text-[12.5px] text-ink-soft bg-[#FAFAFB] border border-line rounded-xl px-4 py-3">
          Select a dataset above to view its SAMA compliance domains.
        </div>
      )}

      {state.error && (
        <div className="mb-4 text-[12.5px] text-danger bg-danger-soft border border-danger/20 rounded-xl px-4 py-3">
          Couldn't reach the API: {state.error}
        </div>
      )}

      {state.data?.no_dataset && (
        <div className="mb-4 text-[12.5px] text-ink-soft bg-[#FFF8E8] border border-[#F0DFAE] rounded-xl px-4 py-3">
          No dataset uploaded yet — upload one from the MDM tab, then run duplicate detection to populate the governance domains.
        </div>
      )}

      {state.data && !state.data.no_dataset && (
        <>
          <div className="bg-white border border-line rounded-card px-6 py-4 mb-5">
            <div className="text-[12px] font-bold text-ink-faint uppercase tracking-wide mb-1">Priority alert</div>
            <div className="text-[13px] text-ink">{state.data.priority_alert}</div>
          </div>

          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-6">
            {state.data.domain_scores.map((d) => (
              <DomainCard key={d.code} domain={d} />
            ))}
          </div>

          <div className="bg-white border border-line rounded-card px-6 py-4">
            <div className="text-[12px] font-bold text-ink-faint uppercase tracking-wide mb-1">Checks</div>
            {state.data.checks.map((c, i) => (
              <CheckRow key={i} check={c} />
            ))}
          </div>

          <div className="mt-4 text-[11px] text-ink-faint leading-relaxed max-w-2xl">{state.data.methodology_note}</div>
        </>
      )}

      <div className="mt-4 text-[11.5px] text-ink-faint">
        {state.loading ? "Loading live data…" : "Live data — refreshes every 15s."}
      </div>
    </div>
  );
}
