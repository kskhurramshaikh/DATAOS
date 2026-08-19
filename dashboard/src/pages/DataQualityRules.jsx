import { useEffect, useState } from "react";
import { api } from "../api";
import DatasetPicker from "../components/DatasetPicker";

const STATUS_META = {
  pass: { label: "Pass", cls: "bg-success-soft text-success" },
  fail: { label: "Fail", cls: "bg-danger-soft text-danger" },
  warn: { label: "Attention", cls: "bg-[#FFF8E8] text-[#B8952E]" },
};

function RuleRow({ rule }) {
  const meta = STATUS_META[rule.status] ?? STATUS_META.warn;
  return (
    <div className="flex items-center justify-between py-2.5 border-b border-line last:border-0 gap-3">
      <div className="min-w-0">
        <div className="text-[12.5px] font-medium text-ink">{rule.description}</div>
        <div className="text-[10.5px] text-ink-faint font-mono mt-0.5">
          {rule.rule_type}
          {rule.detail ? ` · ${rule.detail}` : ""}
          {rule.unexpected_count != null && rule.element_count
            ? ` · ${rule.unexpected_count}/${rule.element_count} unexpected`
            : ""}
        </div>
      </div>
      <span className={`text-[10px] font-bold px-2 py-0.5 rounded-full shrink-0 ${meta.cls}`}>{meta.label}</span>
    </div>
  );
}

export default function DataQualityRules() {
  const [datasets, setDatasets] = useState([]);
  const [datasetsLoaded, setDatasetsLoaded] = useState(false);
  const [selected, setSelected] = useState("");
  const [state, setState] = useState({ loading: false, data: null, error: null });

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
      setState({ loading: false, data: null, error: null });
      return;
    }
    let cancelled = false;
    async function load() {
      setState((s) => ({ ...s, loading: true }));
      try {
        const res = await api.getQualityRules(selected);
        if (!cancelled) setState({ loading: false, data: res, error: null });
      } catch (e) {
        if (!cancelled) setState({ loading: false, data: null, error: e.message });
      }
    }
    load();
    return () => {
      cancelled = true;
    };
  }, [selected]);

  return (
    <div className="p-7 md:px-8">
      <div className="mb-5 flex items-start justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-xl font-semibold text-ink tracking-tight">Data Quality Rules</h1>
          <p className="text-[13px] text-ink-faint mt-1">
            Real Great Expectations rules, run live against this dataset's Silver data.
          </p>
        </div>
        <DatasetPicker datasets={datasets} value={selected} onChange={setSelected} />
      </div>

      {!selected && datasetsLoaded && datasets.length > 1 && (
        <div className="text-[12.5px] text-ink-faint bg-[#FAFAFB] border border-line rounded-xl px-4 py-3">
          Select a dataset above to run its quality rules.
        </div>
      )}
      {!selected && datasetsLoaded && datasets.length === 0 && (
        <div className="text-[12.5px] text-ink-faint bg-[#FAFAFB] border border-line rounded-xl px-4 py-3">
          No datasets yet — upload one first.
        </div>
      )}

      {state.error && (
        <div className="mb-4 text-[12.5px] text-danger bg-danger-soft border border-danger/20 rounded-xl px-4 py-3">
          {state.error}
        </div>
      )}
      {selected && state.loading && <div className="text-[12.5px] text-ink-faint">Running rules…</div>}

      {selected && state.data && (
        <>
          <div className="bg-white border border-line rounded-card px-6 py-4 mb-5 flex items-center justify-between">
            <div>
              <div className="text-[12px] font-bold text-ink-faint uppercase tracking-wide mb-1">Result</div>
              <div className="text-2xl font-semibold font-mono text-ink">
                {state.data.rules_passed} / {state.data.rules_total} passed
              </div>
            </div>
            <div className="text-[11px] text-ink-faint text-right max-w-xs">{state.data.engine}</div>
          </div>

          <div className="bg-white border border-line rounded-card px-6 py-4 mb-5">
            <div className="text-[12px] font-bold text-ink-faint uppercase tracking-wide mb-2">Rules</div>
            {state.data.rules.map((rule, i) => (
              <RuleRow key={i} rule={rule} />
            ))}
          </div>

          <div className="text-[11px] text-ink-faint leading-relaxed max-w-2xl">{state.data.methodology_note}</div>
        </>
      )}
    </div>
  );
}
