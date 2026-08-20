import { useEffect, useState } from "react";
import { api } from "../api";
import DatasetPicker from "../components/DatasetPicker";

const TIER_META = {
  RESTRICTED: { cls: "bg-danger-soft text-danger" },
  CONFIDENTIAL: { cls: "bg-[#FFF8E8] text-[#B8952E]" },
  INTERNAL: { cls: "bg-[#F0F4FF] text-[#3355CC]" },
  PUBLIC: { cls: "bg-[#F4F4F5] text-ink-faint" },
};

function TierBadge({ tier }) {
  const meta = TIER_META[tier] ?? TIER_META.INTERNAL;
  return <span className={`text-[10px] font-bold px-2 py-0.5 rounded-full ${meta.cls}`}>{tier}</span>;
}

function ColumnRow({ col }) {
  return (
    <div className="flex items-center justify-between py-2.5 border-b border-line last:border-0">
      <div className="min-w-0">
        <div className="text-[12.5px] font-medium text-ink font-mono">{col.column}</div>
        {col.matched_keyword && (
          <div className="text-[10.5px] text-ink-faint mt-0.5">matched on "{col.matched_keyword}"</div>
        )}
      </div>
      <div className="flex items-center gap-3 shrink-0">
        <span className="text-[11.5px] text-ink-faint font-mono">
          {col.completeness_pct != null ? `${col.completeness_pct}% complete` : "—"}
        </span>
        <TierBadge tier={col.tier} />
      </div>
    </div>
  );
}

function formatBytes(n) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

function formatWhen(ts) {
  if (!ts) return "—";
  return String(ts).replace("T", " ").slice(0, 16) + " UTC";
}

// Policy Document Upload -- closes the "no 'uploaded policy documents'
// feature anywhere on the page" gap. Global (org-level), not scoped to
// whichever dataset happens to be selected above -- see
// app/adapters/policy_documents_adapter.py's module docstring.
function PolicyDocuments() {
  const [docs, setDocs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [file, setFile] = useState(null);
  const [uploadedBy, setUploadedBy] = useState("");
  const [note, setNote] = useState("");
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState(null);

  async function load() {
    setLoading(true);
    try {
      const res = await api.getPolicyDocuments();
      setDocs(res.documents ?? []);
      setError(null);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  async function handleUpload() {
    if (!file || !uploadedBy.trim()) return;
    setUploading(true);
    setUploadError(null);
    try {
      await api.uploadPolicyDocument(file, uploadedBy.trim(), note.trim() || null);
      setFile(null);
      setNote("");
      await load();
    } catch (e) {
      setUploadError(e.message);
    } finally {
      setUploading(false);
    }
  }

  async function handleDelete(id) {
    try {
      await api.deletePolicyDocument(id);
      await load();
    } catch (e) {
      setUploadError(e.message);
    }
  }

  return (
    <div className="bg-white border border-line rounded-card px-6 py-4 mb-5">
      <div className="text-[12px] font-bold text-ink-faint uppercase tracking-wide mb-1">
        Policy Documents
      </div>
      <p className="text-[11.5px] text-ink-faint mb-3">
        Organization-wide classification/PDPL policy documents — not scoped to any one dataset above.
      </p>

      <div className="flex flex-wrap items-center gap-2.5 mb-3">
        <input
          type="file"
          accept=".pdf,.doc,.docx"
          onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          className="text-[12px]"
        />
        <input
          value={uploadedBy}
          onChange={(e) => setUploadedBy(e.target.value)}
          placeholder="your name"
          className="text-[12.5px] border border-line rounded-lg px-3 py-1.5 w-36"
        />
        <input
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="note (optional)"
          className="text-[12.5px] border border-line rounded-lg px-3 py-1.5 w-48"
        />
        <button
          onClick={handleUpload}
          disabled={!file || !uploadedBy.trim() || uploading}
          className="text-[12px] font-semibold text-white bg-teal px-3.5 py-1.5 rounded-lg hover:opacity-90 disabled:opacity-50"
        >
          {uploading ? "Uploading…" : "Upload"}
        </button>
      </div>
      {uploadError && <div className="mb-3 text-[12px] text-danger">{uploadError}</div>}

      {loading && <div className="text-[12px] text-ink-faint">Loading…</div>}
      {error && <div className="text-[12px] text-danger">{error}</div>}

      {!loading && docs.length === 0 && (
        <div className="text-[12px] text-ink-faint">No policy documents uploaded yet.</div>
      )}

      {docs.length > 0 && (
        <div className="divide-y divide-line">
          {docs.map((d) => (
            <div key={d.id} className="flex items-center justify-between py-2.5">
              <div className="min-w-0">
                <a
                  href={api.policyDocumentDownloadUrl(d.id)}
                  className="text-[12.5px] font-medium text-teal hover:underline"
                >
                  {d.filename}
                </a>
                <div className="text-[10.5px] text-ink-faint mt-0.5">
                  {formatBytes(d.size_bytes)} · uploaded {formatWhen(d.uploaded_at)} by {d.uploaded_by}
                  {d.note ? ` — ${d.note}` : ""}
                </div>
              </div>
              <button
                onClick={() => handleDelete(d.id)}
                className="text-[11px] font-semibold text-danger hover:underline shrink-0 ml-3"
              >
                Remove
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default function Classification() {
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
        const res = await api.getClassification(selected);
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
          <h1 className="text-xl font-semibold text-ink tracking-tight">Classification & PDPL</h1>
          <p className="text-[13px] text-ink-faint mt-1">
            Every column's sensitivity tier, and PDPL completeness for personal-data columns.
          </p>
        </div>
        <DatasetPicker datasets={datasets} value={selected} onChange={setSelected} />
      </div>

      <PolicyDocuments />

      {!selected && datasetsLoaded && datasets.length > 1 && (
        <div className="text-[12.5px] text-ink-faint bg-[#FAFAFB] border border-line rounded-xl px-4 py-3">
          Select a dataset above to view its column classification.
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
      {selected && state.loading && <div className="text-[12.5px] text-ink-faint">Loading…</div>}

      {selected && state.data && (
        <>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-5">
            {Object.entries(state.data.tier_counts).map(([tier, count]) => (
              <div key={tier} className="bg-white border border-line rounded-2xl px-4 py-3.5 flex flex-col gap-1">
                <span className="text-[11px] font-bold text-ink-faint tracking-wide">{tier}</span>
                <span className="text-2xl font-semibold font-mono text-ink">{count}</span>
              </div>
            ))}
          </div>

          <div className="bg-white border border-line rounded-card px-6 py-4 mb-5">
            <div className="text-[12px] font-bold text-ink-faint uppercase tracking-wide mb-2">
              PDPL completeness
            </div>
            {state.data.pdpl.average_completeness_pct != null ? (
              <div className="text-3xl font-semibold font-mono text-ink mb-1">
                {state.data.pdpl.average_completeness_pct}%
              </div>
            ) : (
              <div className="text-[13px] text-ink-soft mb-1">—</div>
            )}
            <div className="text-[11.5px] text-ink-faint leading-relaxed">{state.data.pdpl.note}</div>
          </div>

          <div className="bg-white border border-line rounded-card px-6 py-4 mb-5">
            <div className="text-[12px] font-bold text-ink-faint uppercase tracking-wide mb-2">
              Columns ({state.data.total_columns})
            </div>
            {state.data.columns.map((col) => (
              <ColumnRow key={col.column} col={col} />
            ))}
          </div>

          <div className="text-[11px] text-[#B8952E] bg-[#FFF8E8] border border-[#F0DFAE] rounded-xl px-4 py-3 leading-relaxed">
            {state.data.enforcement_note}
          </div>
        </>
      )}
    </div>
  );
}
