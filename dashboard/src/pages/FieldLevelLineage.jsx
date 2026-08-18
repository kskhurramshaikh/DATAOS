import { useEffect, useState } from "react";
import { api } from "../api";
import DatasetPicker from "../components/DatasetPicker";

// Field-Level Lineage (Item 3's 3rd of 4 required MDM pages, reopened
// 2026-08-18 -- Section 04 page 3 names 4 required MDM pages: Golden
// Record Registry, Duplicate Resolution Queue, Field-Level Lineage
// (tracing ECL_SAR, NATIONAL_ID, DUPLICATE_FLAG, NDI_SCORE), and Data
// Stewardship. Only the first two were built when Item 3 was marked
// done; this closes the gap for Field-Level Lineage specifically.
//
// FLAGGED HONESTLY, same as the page's own backend module
// (app/field_lineage.py): the doc's wording implies all four fields
// are "sourced from OpenLineage/Marquez." In this system, only two
// genuinely are. NATIONAL_ID and DUPLICATE_FLAG live entirely in the
// MDM/Postgres world; ECL_SAR and NDI_SCORE are Marquez-traceable only
// when computed via the Airflow DAG, not via the separate on-demand
// chat/dashboard actions. Each field below shows exactly which real
// system produced its trace, and states plainly when a source isn't
// available for this dataset rather than inventing one.

const FIELD_ORDER = ["NATIONAL_ID", "DUPLICATE_FLAG", "ECL_SAR", "NDI_SCORE"];

function FieldCard({ data }) {
  return (
    <div className="bg-white border border-line rounded-card px-5 py-4">
      <div className="flex items-start justify-between gap-3 mb-1">
        <div>
          <div className="text-[13.5px] font-semibold text-ink font-mono">{data.field}</div>
          <div className="text-[11px] text-ink-faint mt-0.5">{data.source_system}</div>
        </div>
        <span
          className={`text-[10.5px] font-semibold px-2.5 py-1 rounded-full shrink-0 ${
            data.available ? "text-success bg-success-soft" : "text-ink-faint bg-[#F4F4F5]"
          }`}
        >
          {data.available ? "Traced" : "Not available"}
        </span>
      </div>

      {!data.available && data.reason && (
        <div className="text-[12px] text-ink-soft mt-3">{data.reason}</div>
      )}

      {data.steps && data.steps.length > 0 && (
        <div className="mt-3.5 flex flex-col gap-2">
          {data.steps.map((s, i) => (
            <div key={i} className="flex items-start gap-2.5">
              <div className="flex flex-col items-center pt-0.5">
                <span className="w-1.5 h-1.5 rounded-full bg-teal shrink-0" />
                {i < data.steps.length - 1 && <span className="w-px flex-1 bg-line mt-1" style={{ minHeight: 14 }} />}
              </div>
              <div className="min-w-0 pb-1">
                <div className="text-[11.5px] font-semibold text-ink font-mono">{s.stage}</div>
                <div className="text-[11.5px] text-ink-soft mt-0.5">{s.description}</div>
              </div>
            </div>
          ))}
        </div>
      )}

      {data.status_counts && (
        <div className="mt-3.5 flex gap-4 text-[11px] text-ink-faint font-mono">
          <span>pending: {data.status_counts.pending}</span>
          <span>confirmed: {data.status_counts.confirmed_duplicate}</span>
          <span>rejected: {data.status_counts.not_duplicate}</span>
        </div>
      )}

      {data.latest_recorded_snapshot && (
        <div className="mt-3.5 text-[11px] text-ink-faint bg-[#FAFAFB] border border-line rounded-lg px-3 py-2">
          Latest NDI History snapshot: score {data.latest_recorded_snapshot.display_score}, recorded by{" "}
          {data.latest_recorded_snapshot.recorded_by} at {data.latest_recorded_snapshot.recorded_at}.
        </div>
      )}

      {data.note && <div className="mt-3.5 text-[11px] text-ink-faint leading-relaxed">{data.note}</div>}
    </div>
  );
}

export default function FieldLevelLineage() {
  const [datasets, setDatasets] = useState([]);
  const [selected, setSelected] = useState("");
  const [state, setState] = useState({ loading: false, data: null, error: null });

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await api.getDatasets();
        if (cancelled) return;
        const list = res.datasets ?? [];
        setDatasets(list);
        if (list.length === 1) setSelected(list[0].dataset_name);
      } catch {
        // Non-fatal -- picker just shows empty.
      }
    })();
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
    (async () => {
      setState({ loading: true, data: null, error: null });
      try {
        const res = await api.getMdmFieldLineage(selected);
        if (!cancelled) setState({ loading: false, data: res, error: null });
      } catch (e) {
        if (!cancelled) setState({ loading: false, data: null, error: e.message });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [selected]);

  return (
    <div className="p-7 md:px-8">
      <div className="mb-5 flex items-start justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-xl font-semibold text-ink tracking-tight">Field-Level Lineage</h1>
          <p className="text-[13px] text-ink-faint mt-1 max-w-2xl">
            Traces ECL_SAR, NATIONAL_ID, DUPLICATE_FLAG, and NDI_SCORE through whichever real system actually
            produces each one for the selected dataset.
          </p>
        </div>
        <DatasetPicker datasets={datasets} value={selected} onChange={setSelected} />
      </div>

      {!selected && (
        <div className="mb-4 text-[12.5px] text-ink-soft bg-[#FAFAFB] border border-line rounded-xl px-4 py-3">
          Select a dataset above to trace its fields.
        </div>
      )}

      {state.error && (
        <div className="mb-4 text-[12.5px] text-danger bg-danger-soft border border-danger/20 rounded-xl px-4 py-3">
          {state.error}
        </div>
      )}

      {state.loading && <div className="text-[12.5px] text-ink-faint">Loading…</div>}

      {state.data && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {FIELD_ORDER.map((f) => (
            <FieldCard key={f} data={state.data.fields[f]} />
          ))}
        </div>
      )}
    </div>
  );
}
