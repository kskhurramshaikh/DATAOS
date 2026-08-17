import { useEffect, useMemo, useState } from "react";
import { api } from "../api";

// Turns a raw merged_record key (CUST_ID, FULL_NAME, DOB, ...) into a
// human label (Cust ID, Full Name, DOB, ...) without a hardcoded field
// list -- short segments (<=3 chars, e.g. ID/DOB/DQ) stay uppercase as
// acronyms, longer ones get title-cased. Works for whatever columns a
// given dataset actually has, not just the Banking_Demo schema.
function humanizeField(key) {
  return key
    .split("_")
    .map((part) => (part.length <= 3 ? part.toUpperCase() : part.charAt(0) + part.slice(1).toLowerCase()))
    .join(" ");
}

function matchedFieldsFor(record, search) {
  if (!search.trim()) return [];
  const s = search.toLowerCase();
  const matches = [];
  if (record.dataset_safe_name?.toLowerCase().includes(s)) matches.push("Dataset");
  for (const [key, val] of Object.entries(record.merged_record || {})) {
    if (String(val ?? "").toLowerCase().includes(s)) matches.push(humanizeField(key));
  }
  return matches;
}

function GoldenRecordCard({ record, expanded, onToggle, matchedFields }) {
  const fields = Object.entries(record.merged_record || {});
  return (
    <div className="bg-white border border-line rounded-xl overflow-hidden">
      <button
        onClick={onToggle}
        className="w-full flex items-center justify-between px-4 py-3 text-left"
      >
        <div className="flex items-center gap-3 flex-wrap">
          <span className="w-2 h-2 rounded-full bg-gold inline-block" />
          <span className="text-[12.5px] font-semibold text-ink">Golden Record #{record.id}</span>
          <span className="text-[11px] text-ink-faint">{record.source_row_ids.length} source records merged</span>
          {matchedFields?.length > 0 && (
            <span className="text-[10px] font-semibold text-teal bg-teal-soft px-1.5 py-0.5 rounded-full">
              matched on {matchedFields.slice(0, 2).join(", ")}
              {matchedFields.length > 2 ? ` +${matchedFields.length - 2}` : ""}
            </span>
          )}
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

// Guided search widget -- rather than a bare input the person has to
// guess at, this surfaces the actual searchable fields as clickable
// pills, each seeded with a REAL example value pulled from the loaded
// records (not a placeholder guess). Clicking one both teaches "you can
// search by Phone" and demonstrates it live in one motion. Field list
// is derived from whatever's actually in the data, so it stays correct
// if a dataset's columns differ from Banking_Demo's.
function SmartSearchBox({ records, search, onSearch, activeField, onActiveFieldChange }) {
  const fields = useMemo(() => {
    const seen = new Map(); // key -> first non-empty example value found
    for (const r of records) {
      for (const [key, val] of Object.entries(r.merged_record || {})) {
        if (!seen.has(key) && val !== null && val !== undefined && String(val).trim() !== "") {
          seen.set(key, String(val));
        }
      }
    }
    return Array.from(seen.entries())
      .slice(0, 9)
      .map(([key, example]) => ({ key, label: humanizeField(key), example }));
  }, [records]);

  function handleTyping(e) {
    onActiveFieldChange(null);
    onSearch(e.target.value);
  }

  function handlePillClick(f) {
    onActiveFieldChange(f.key);
    onSearch(f.example);
  }

  return (
    <div className="w-full md:w-auto">
      <div className="relative">
        <input
          value={search}
          onChange={handleTyping}
          placeholder="Search by name, phone, email, ID, city…"
          className="text-[12.5px] border border-line rounded-lg pl-3 pr-8 py-2 w-full md:w-72"
        />
        {search && (
          <button
            onClick={() => {
              onSearch("");
              onActiveFieldChange(null);
            }}
            aria-label="Clear search"
            className="absolute right-2 top-1/2 -translate-y-1/2 text-ink-faint hover:text-ink text-[13px] leading-none"
          >
            ×
          </button>
        )}
      </div>
      {fields.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5 mt-2 md:justify-end">
          <span className="text-[10.5px] text-ink-faint mr-0.5">Search checks:</span>
          {fields.map((f) => (
            <button
              key={f.key}
              onClick={() => handlePillClick(f)}
              title={`e.g. "${f.example}"`}
              className={`text-[10.5px] font-medium px-2 py-0.5 rounded-full border transition-colors ${
                activeField === f.key
                  ? "bg-teal text-white border-teal"
                  : "bg-white text-ink-soft border-line hover:border-teal/50 hover:text-teal"
              }`}
            >
              {f.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export default function GoldenRecordRegistry() {
  const [state, setState] = useState({ loading: true, records: [], error: null });
  const [expandedId, setExpandedId] = useState(null);
  const [search, setSearch] = useState("");
  const [activeField, setActiveField] = useState(null);

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

  const filtered = state.records
    .map((r) => ({ record: r, matches: matchedFieldsFor(r, search) }))
    .filter(({ matches }) => !search.trim() || matches.length > 0);

  return (
    <div className="p-7 md:px-8">
      <div className="mb-5 flex items-start justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-xl font-semibold text-ink tracking-tight">Golden Record Registry</h1>
          <p className="text-[13px] text-ink-faint mt-1">
            Real executed merges — every field traceable to the source record it came from.
          </p>
        </div>
        <SmartSearchBox
          records={state.records}
          search={search}
          onSearch={setSearch}
          activeField={activeField}
          onActiveFieldChange={setActiveField}
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
            : "No golden records match that search — it checks every merged field's value (name, phone, email, ID, city, and more), not just the ones shown collapsed."}
        </div>
      )}

      <div className="flex flex-col gap-2.5">
        {filtered.map(({ record, matches }) => (
          <GoldenRecordCard
            key={record.id}
            record={record}
            expanded={expandedId === record.id}
            onToggle={() => setExpandedId(expandedId === record.id ? null : record.id)}
            matchedFields={matches}
          />
        ))}
      </div>
    </div>
  );
}
