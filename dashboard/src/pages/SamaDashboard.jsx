import { useEffect, useState } from "react";
import { api } from "../api";

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
  const [state, setState] = useState({ loading: true, data: null, error: null, needsDataset: false, datasets: [] });
  const [selected, setSelected] = useState("");

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const res = await api.getSama(selected || undefined);
        if (!cancelled) setState((s) => ({ ...s, loading: false, data: res, error: null, needsDataset: false }));
      } catch (e) {
        if (cancelled) return;
        // run_sama_compliance raises a 400 with a friendly "which
        // dataset did you mean" message when more than one dataset
        // exists and none was specified -- surface a picker instead
        // of a raw error in that case.
        const ambiguous = e.message?.includes("More than one dataset");
        if (ambiguous) {
          try {
            const ds = await api.getDatasets();
            setState({ loading: false, data: null, error: null, needsDataset: true, datasets: ds.datasets ?? [] });
            return;
          } catch {
            // fall through to plain error below
          }
        }
        setState((s) => ({ ...s, loading: false, error: e.message }));
      }
    }
    load();
    const interval = setInterval(load, 15000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [selected]);

  return (
    <div className="p-7 md:px-8">
      <div className="mb-5 flex items-start justify-between">
        <div>
          <h1 className="text-xl font-semibold text-ink tracking-tight">SAMA Compliance</h1>
          <p className="text-[13px] text-ink-faint mt-1">8-domain SAMA data-governance view, computed from real signals</p>
        </div>
        {state.data?.dataset_name && (
          <div className="text-[11.5px] text-ink-faint font-mono bg-[#FAFAFB] border border-line rounded-lg px-2.5 py-1.5">
            {state.data.dataset_name}
          </div>
        )}
      </div>

      {state.needsDataset && (
        <div className="bg-white border border-line rounded-card px-6 py-5 mb-5">
          <div className="text-[13px] text-ink-soft mb-3">More than one dataset exists — pick one to view SAMA compliance for:</div>
          <div className="flex flex-wrap gap-2">
            {state.datasets.map((d) => (
              <button
                key={d.dataset_name}
                onClick={() => setSelected(d.dataset_name)}
                className="text-[12px] font-semibold px-3 py-1.5 rounded-lg border border-line text-ink-soft hover:bg-[#FAFAFB]"
              >
                {d.display_name ?? d.dataset_name}
              </button>
            ))}
          </div>
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

      {state.data && (
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
