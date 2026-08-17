import { useEffect, useState } from "react";
import { api } from "../api";

function GoldenRecordCard({ record, expanded, onToggle }) {
  const fields = Object.entries(record.merged_record || {});
  return (
    <div className="bg-white border border-line rounded-xl overflow-hidden">
      <button
        onClick={onToggle}
        className="w-full flex items-center justify-between px-4 py-3 text-left"
      >
        <div className="flex items-center gap-3">
          <span className="w-2 h-2 rounded-full bg-gold inline-block" />
          <span className="text-[12.5px] font-semibold text-ink">Golden Record #{record.id}</span>
          <span className="text-[11px] text-ink-faint">{record.source_row_ids.length} source records merged</span>
        </div>
        <span className="text-[11px] text-ink-faint font-mono">{record.dataset_safe_name}</span>
      </button>

      {expanded && (
        <div className="border-t border-line px-4 py-3.5">
          <div className="grid grid-cols-2 gap-x-6 gap-y-2 mb-4">
            {fields.map(([key, val]) => (
              <div key={key} className="text-[12px]">
                <span className="text-ink-faint">{key}: </span>
                <span className="text-ink font-medium">{val ?? "—"}</span>
                {record.field_sources?.[key] && (
                  <span className="text-[10px] text-ink-faint ml-1">(from row {record.field_sources[key]})</span>
                )}
              </div>
            ))}
          </div>
          <div className="bg-[#FAFAFB] border border-line rounded-lg px-3 py-2.5">
            <div className="text-[10.5px] font-bold text-ink-faint uppercase tracking-wide mb-1.5">
              Merge history
            </div>
            <div className="text-[11.5px] text-ink-soft">
              Base record: <span className="font-mono">{record.base_row_id}</span> (most complete of the group) ·
              merged from {record.source_row_ids.length} source rows: <span className="font-mono">{record.source_row_ids.join(", ")}</span>
            </div>
            <div className="text-[11.5px] text-ink-soft mt-1">
              Merged by <span className="font-medium text-ink">{record.merged_by}</span> on{" "}
              {record.created_at?.slice(0, 16).replace("T", " ")} — cluster #{record.cluster_id}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default function GoldenRecordRegistry() {
  const [state, setState] = useState({ loading: true, records: [], error: null });
  const [expandedId, setExpandedId] = useState(null);
  const [search, setSearch] = useState("");

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const res = await api.getGoldenRecords();
        if (!cancelled) setState({ loading: false, records: res.golden_records, error: null });
      } catch (e) {
        if (!cancelled) setState({ loading: false, records: [], error: e.message });
      }
    }
    load();
    return () => {
      cancelled = true;
    };
  }, []);

  const filtered = state.records.filter((r) => {
    if (!search.trim()) return true;
    const s = search.toLowerCase();
    return (
      r.dataset_safe_name?.toLowerCase().includes(s) ||
      Object.values(r.merged_record || {}).some((v) => String(v ?? "").toLowerCase().includes(s))
    );
  });

  return (
    <div className="p-7 md:px-8">
      <div className="mb-5 flex items-end justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-xl font-semibold text-ink tracking-tight">Golden Record Registry</h1>
          <p className="text-[13px] text-ink-faint mt-1">
            Real executed merges — every field traceable to the source record it came from.
          </p>
        </div>
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search golden records…"
          className="text-[12.5px] border border-line rounded-lg px-3 py-2 w-64"
        />
      </div>

      {state.error && (
        <div className="mb-4 text-[12.5px] text-danger bg-danger-soft border border-danger/20 rounded-xl px-4 py-3">
          {state.error}
        </div>
      )}
      {state.loading && <div className="text-[12.5px] text-ink-faint">Loading…</div>}
      {!state.loading && filtered.length === 0 && (
        <div className="text-[12.5px] text-ink-faint bg-[#FAFAFB] border border-line rounded-xl px-4 py-3">
          {state.records.length === 0
            ? "No golden records yet — confirm a duplicate cluster in the Duplicate Queue to create one."
            : "No golden records match that search."}
        </div>
      )}

      <div className="flex flex-col gap-2.5">
        {filtered.map((r) => (
          <GoldenRecordCard
            key={r.id}
            record={r}
            expanded={expandedId === r.id}
            onToggle={() => setExpandedId(expandedId === r.id ? null : r.id)}
          />
        ))}
      </div>
    </div>
  );
}
