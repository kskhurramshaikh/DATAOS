import { Link } from "react-router-dom";
import { useEffect, useState } from "react";
import { api } from "../api";
import { useAuth } from "../auth";
import { LineageGraph } from "../components/LineageGraph";

// Data Catalog (Dev Queue item 6, first half). Reads the real job/run
// registry from Marquez -- see app/marquez_client.py's module
// docstring for exactly what's queried and why (including the
// resource-wall reasoning for building this instead of OpenMetadata,
// sent to #one-tech-ai 2026-08-18 before any of this was built).
//
// Same honesty principle as every other dashboard page here: this is
// NOT a full data catalog (no PII tagging, no search across types) --
// it's a real, live view of what Airflow's OpenLineage integration
// actually captured, plus (2026-08-19) a real browsable dataset list
// + per-column schema view built directly on top of data that already
// existed (dataset_adapter.list_datasets()), no new backend
// computation.
//
// GAPS CLOSED 2026-08-19, per Dr. Saber's direct review: this page
// previously only showed the job/run table -- the Directive also
// calls for a browsable dataset list and a schema view, both missing.
// Added below as a new "Datasets" section.
//
// GAP CLOSED 2026-08-20: the lineage graph itself is now embedded
// directly on this page too (compact, via the shared
// components/LineageGraph.jsx), not just linked to the adjacent
// /catalog/lineage tab -- that link is kept alongside it for opening
// the full-size interactive version.
//
// GAP CLOSED 2026-08-20 (later): Business Glossary -- the last of the
// Directive's four named Data Catalog requirements ("Browsable
// dataset list, schema, business glossary, lineage graph"). See
// app/adapters/glossary_adapter.py's module docstring for the full
// "why not OpenMetadata" / design reasoning. PII tagging remains out
// of scope here -- never actually cited as a Directive requirement
// (confirmed directly against the source PDF), and its real substance
// already exists via Classification & PDPL's sensitivity tiering.

const STATE_STYLE = {
  COMPLETED: { bg: "#EAF6F1", text: "#0F7A6B", dot: "#2FA37E", label: "Completed" },
  RUNNING: { bg: "#EAF1FB", text: "#3E7BD6", dot: "#3E7BD6", label: "Running" },
  FAILED: { bg: "#FBEAEA", text: "#D6483E", dot: "#D6483E", label: "Failed" },
};

function stateMeta(state) {
  return STATE_STYLE[state] || { bg: "#F4F4F5", text: "#8E8E93", dot: "#8E8E93", label: state || "Unknown" };
}

function StatusChip({ state }) {
  const s = stateMeta(state);
  return (
    <span
      className="inline-flex items-center gap-1.5 text-[11px] font-semibold px-2.5 py-1 rounded-full"
      style={{ background: s.bg, color: s.text }}
    >
      <span className="w-1.5 h-1.5 rounded-full inline-block" style={{ background: s.dot }} />
      {s.label}
    </span>
  );
}

const STAGE_STYLE = {
  gold: { bg: "#FFF8E8", text: "#946E1B", label: "Gold" },
  silver_held: { bg: "#F4F4F5", text: "#6B6B70", label: "Silver (held)" },
  silver: { bg: "#F2F2F4", text: "#6B6B70", label: "Silver" },
};

function StageChip({ stage }) {
  const s = STAGE_STYLE[stage] || { bg: "#F4F4F5", text: "#8E8E93", label: stage || "Unknown" };
  return (
    <span className="inline-flex text-[10.5px] font-semibold px-2 py-0.5 rounded-full" style={{ background: s.bg, color: s.text }}>
      {s.label}
    </span>
  );
}

function formatWhen(ts) {
  if (!ts) return "—";
  return String(ts).replace("T", " ").slice(0, 19) + " UTC";
}

function formatDuration(ms) {
  if (ms == null) return "—";
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

// Real per-column detail computed client-side from data
// dataset_adapter.list_datasets() already returns -- no new backend
// endpoint. Deliberately lighter than Classification & PDPL's own
// per-column view (that one requires an OPA-gated role, since it also
// shows sensitivity tier): this is the plain schema -- column name,
// completeness, whether it was dropped during Silver cleaning -- kept
// unauthenticated like the rest of this page. A link to the full
// classification detail is offered per-dataset instead of duplicating
// that RBAC-gated logic here.
function SchemaView({ dataset }) {
  const rows = dataset.rows || 0;
  return (
    <div className="px-5 py-4 bg-[#FAFAFB] border-t border-line">
      <div className="flex items-center justify-between mb-2.5">
        <div className="text-[11px] font-bold text-ink-faint uppercase tracking-wide">
          Schema — {dataset.columns.length} columns
        </div>
        <Link
          to="/governance/classification"
          className="text-[11.5px] font-semibold text-teal hover:underline"
        >
          View sensitivity classification →
        </Link>
      </div>
      <div className="grid grid-cols-2 lg:grid-cols-3 gap-2">
        {dataset.columns.map((col) => {
          const nullCount = dataset.null_counts?.[col] ?? 0;
          const completeness = rows ? Math.round(100 * (rows - nullCount) / rows * 10) / 10 : null;
          const dropped = dataset.dropped_columns?.includes(col);
          return (
            <div key={col} className="bg-white border border-line rounded-lg px-3 py-2">
              <div className="flex items-center justify-between gap-2">
                <span className="text-[11.5px] font-mono font-semibold text-ink truncate">{col}</span>
                {dropped && (
                  <span className="text-[9px] font-bold px-1.5 py-0.5 rounded-full bg-danger-soft text-danger shrink-0">
                    dropped
                  </span>
                )}
              </div>
              <div className="text-[10.5px] text-ink-faint mt-0.5">
                {completeness != null ? `${completeness}% complete` : "—"}
                {nullCount > 0 && ` · ${nullCount} null`}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

const GLOSSARY_DOMAINS = ["MDM", "Catalog", "Governance", "Security", "Banking", "Other"];

const DOMAIN_BADGE_STYLE = {
  MDM: { bg: "#EAF6F1", text: "#0F7A6B" },
  Catalog: { bg: "#EAF1FB", text: "#3E7BD6" },
  Governance: { bg: "#EAF6F1", text: "#0F7A6B" },
  Security: { bg: "#FBEAEA", text: "#D6483E" },
  Banking: { bg: "#FFF8E8", text: "#946E1B" },
  Other: { bg: "#F2F2F4", text: "#6B6B70" },
};

function DomainBadge({ domain }) {
  const s = DOMAIN_BADGE_STYLE[domain] || DOMAIN_BADGE_STYLE.Other;
  return (
    <span className="text-[10px] font-bold px-2 py-0.5 rounded-full" style={{ background: s.bg, color: s.text }}>
      {domain}
    </span>
  );
}

// Business Glossary (2026-08-20) -- closes the last of the Directive's
// four named Data Catalog requirements ("Browsable dataset list,
// schema, business glossary, lineage graph"). See
// app/adapters/glossary_adapter.py's module docstring for the exact
// citation and the full "why not OpenMetadata" / design reasoning --
// this UI mirrors a real reference platform's own glossary UX
// (search across term/definition/tags, a domain-colored badge, a
// letter avatar) while using this codebase's own real Postgres-backed
// CRUD and RBAC/OPA gate, rather than that reference's localStorage-
// only demo persistence.
function GlossarySection({ canEdit }) {
  const [state, setState] = useState({ loading: true, data: null, error: null });
  const [query, setQuery] = useState("");
  const [formOpen, setFormOpen] = useState(false);
  const [editingId, setEditingId] = useState(null);
  const [form, setForm] = useState({ term: "", definition: "", domain: "Other", tagsText: "" });
  const [formError, setFormError] = useState(null);
  const [busy, setBusy] = useState(false);

  async function load() {
    setState((s) => ({ ...s, loading: true }));
    try {
      const res = await api.getGlossaryTerms();
      setState({ loading: false, data: res, error: null });
    } catch (e) {
      setState({ loading: false, data: null, error: e.message });
    }
  }

  useEffect(() => {
    load();
  }, []);

  function openCreateForm() {
    setEditingId(null);
    setForm({ term: "", definition: "", domain: "Other", tagsText: "" });
    setFormError(null);
    setFormOpen(true);
  }

  function openEditForm(t) {
    setEditingId(t.id);
    setForm({ term: t.term, definition: t.definition, domain: t.domain, tagsText: t.tags.join(", ") });
    setFormError(null);
    setFormOpen(true);
  }

  function parseTags(text) {
    return text
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
  }

  async function handleSave() {
    if (!editingId && !form.term.trim()) {
      setFormError("Term is required.");
      return;
    }
    if (!form.definition.trim()) {
      setFormError("Definition is required.");
      return;
    }
    setBusy(true);
    setFormError(null);
    try {
      const tags = parseTags(form.tagsText);
      let res;
      if (editingId) {
        res = await api.updateGlossaryTerm(editingId, { definition: form.definition.trim(), domain: form.domain, tags });
      } else {
        res = await api.createGlossaryTerm({ term: form.term.trim(), definition: form.definition.trim(), domain: form.domain, tags });
      }
      setState({ loading: false, data: res, error: null });
      setFormOpen(false);
    } catch (e) {
      setFormError(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function handleDelete(t) {
    setBusy(true);
    try {
      const res = await api.deleteGlossaryTerm(t.id);
      setState({ loading: false, data: res, error: null });
    } catch (e) {
      setState((s) => ({ ...s, error: e.message }));
    } finally {
      setBusy(false);
    }
  }

  const terms = state.data?.terms ?? [];
  const q = query.trim().toLowerCase();
  const visibleTerms = !q
    ? terms
    : terms.filter(
        (t) =>
          t.term.toLowerCase().includes(q) ||
          t.definition.toLowerCase().includes(q) ||
          t.tags.some((g) => g.toLowerCase().includes(q))
      );

  return (
    <div className="mb-5">
      <div className="flex items-center justify-between mb-2 flex-wrap gap-2">
        <div className="text-[12px] font-bold text-ink-faint uppercase tracking-wide">Business Glossary</div>
        {canEdit && !formOpen && (
          <button
            onClick={openCreateForm}
            className="text-[11px] font-semibold text-teal bg-teal-soft px-2.5 py-1 rounded-full hover:opacity-80"
          >
            Add term
          </button>
        )}
      </div>

      {state.error && (
        <div className="mb-3 text-[12.5px] text-danger bg-danger-soft border border-danger/20 rounded-xl px-4 py-3">
          {state.error}
        </div>
      )}

      {formOpen && (
        <div className="bg-white border border-line rounded-card px-5 py-4 mb-3 flex flex-col gap-2.5 max-w-md">
          {!editingId && (
            <input
              value={form.term}
              onChange={(e) => setForm((f) => ({ ...f, term: e.target.value }))}
              placeholder="Term *"
              className="text-[12.5px] border border-line rounded-lg px-3 py-1.5"
            />
          )}
          <textarea
            value={form.definition}
            onChange={(e) => setForm((f) => ({ ...f, definition: e.target.value }))}
            placeholder="Definition *"
            rows={3}
            className="text-[12.5px] border border-line rounded-lg px-3 py-1.5"
          />
          <label className="text-[11.5px] text-ink-soft">
            Domain
            <select
              value={form.domain}
              onChange={(e) => setForm((f) => ({ ...f, domain: e.target.value }))}
              className="mt-1 w-full text-[12.5px] border border-line rounded-lg px-3 py-1.5 bg-white"
            >
              {GLOSSARY_DOMAINS.map((d) => (
                <option key={d} value={d}>
                  {d}
                </option>
              ))}
            </select>
          </label>
          <label className="text-[11.5px] text-ink-soft">
            Tags (comma-separated — a dataset or column name is a soft, searchable reference, not an enforced link)
            <input
              value={form.tagsText}
              onChange={(e) => setForm((f) => ({ ...f, tagsText: e.target.value }))}
              placeholder="e.g. NATIONAL_ID, MDM"
              className="mt-1 w-full text-[12.5px] border border-line rounded-lg px-3 py-1.5"
            />
          </label>
          {formError && <div className="text-[11px] text-danger">{formError}</div>}
          <div className="flex items-center gap-2 mt-0.5">
            <button
              onClick={handleSave}
              disabled={busy}
              className="text-[11.5px] font-semibold text-white bg-teal px-3 py-1.5 rounded-lg hover:opacity-90 disabled:opacity-50"
            >
              {editingId ? "Save changes" : "Add term"}
            </button>
            <button
              onClick={() => setFormOpen(false)}
              className="text-[11.5px] font-medium text-ink-faint hover:text-ink px-1"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {!state.loading && terms.length === 0 && !state.error && (
        <div className="bg-white border border-line rounded-card px-6 py-6 text-center text-[12.5px] text-ink-soft">
          No glossary terms yet.
        </div>
      )}

      {terms.length > 0 && (
        <div className="bg-white border border-line rounded-card overflow-hidden">
          <div className="px-5 py-3 border-b border-line flex items-center justify-between gap-3 flex-wrap">
            <div className="text-[11.5px] text-ink-faint">
              {state.data.terms_total} term{state.data.terms_total === 1 ? "" : "s"} · {state.data.domains_used.length} domain
              {state.data.domains_used.length === 1 ? "" : "s"}
            </div>
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search terms…"
              className="text-[12px] border border-line rounded-lg px-3 py-1.5 w-56"
            />
          </div>
          <div>
            {visibleTerms.length === 0 && (
              <div className="px-5 py-6 text-center text-[12px] text-ink-faint">No matching terms.</div>
            )}
            {visibleTerms.map((t) => (
              <div key={t.id} className="px-5 py-3.5 border-b border-line last:border-0 flex items-start gap-3.5">
                <div className="w-8 h-8 rounded-lg bg-teal-soft text-teal flex items-center justify-center text-[13px] font-bold shrink-0">
                  {t.term.charAt(0).toUpperCase()}
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="text-[13px] font-semibold text-ink">{t.term}</span>
                    {canEdit && (
                      <>
                        <button
                          onClick={() => openEditForm(t)}
                          className="text-[10.5px] font-medium text-ink-faint hover:text-teal"
                        >
                          Edit
                        </button>
                        <button
                          onClick={() => handleDelete(t)}
                          disabled={busy}
                          className="text-[10.5px] font-medium text-ink-faint hover:text-danger"
                        >
                          Delete
                        </button>
                      </>
                    )}
                  </div>
                  <div className="text-[12px] text-ink-soft mt-0.5 leading-relaxed">{t.definition}</div>
                  <div className="flex items-center gap-1.5 mt-1.5 flex-wrap">
                    <DomainBadge domain={t.domain} />
                    {t.tags
                      .filter((g) => g !== t.domain)
                      .map((g) => (
                        <span key={g} className="text-[10px] text-ink-faint bg-[#F2F2F4] px-2 py-0.5 rounded-full font-mono">
                          {g}
                        </span>
                      ))}
                    <span className="text-[10px] text-ink-faint ml-1">
                      · {t.created_by} on {t.updated_at?.slice(0, 10)}
                    </span>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function DatasetRow({ dataset, expanded, onToggle }) {
  return (
    <>
      <tr className="border-b border-line last:border-0 hover:bg-[#FAFAFB]">
        <td className="py-3 px-5">
          <span className="text-[12.5px] font-mono font-semibold text-ink">{dataset.dataset_name}</span>
        </td>
        <td className="py-3 px-3">
          <StageChip stage={dataset.stage} />
        </td>
        <td className="py-3 px-3 text-[12px] font-mono text-ink-soft text-right">{dataset.rows ?? 0}</td>
        <td className="py-3 px-3 text-[12px] font-mono text-ink-soft text-right">{dataset.columns?.length ?? 0}</td>
        <td className="py-3 px-3 text-[12px] text-ink-faint font-mono">{formatWhen(dataset.updated_at)}</td>
        <td className="py-3 px-5 text-right">
          <button onClick={onToggle} className="text-[11.5px] font-semibold text-teal hover:underline">
            {expanded ? "Hide schema" : "View schema"}
          </button>
        </td>
      </tr>
      {expanded && (
        <tr>
          <td colSpan={6} className="p-0">
            <SchemaView dataset={dataset} />
          </td>
        </tr>
      )}
    </>
  );
}

function RunHistory({ jobName, onClose }) {
  const [state, setState] = useState({ loading: true, data: null, error: null });

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await api.getCatalogJobRuns(jobName, 10);
        if (!cancelled) setState({ loading: false, data: res, error: res.error || null });
      } catch (e) {
        if (!cancelled) setState({ loading: false, data: null, error: e.message });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [jobName]);

  return (
    <tr>
      <td colSpan={5} className="p-0">
        <div className="px-5 py-4 bg-[#FAFAFB] border-t border-line">
          <div className="flex items-center justify-between mb-2.5">
            <div className="text-[11px] font-bold text-ink-faint uppercase tracking-wide">
              Recent runs — {jobName}
            </div>
            <button onClick={onClose} className="text-[11.5px] font-semibold text-teal hover:underline">
              Close
            </button>
          </div>
          {state.loading && <div className="text-[12px] text-ink-faint">Loading…</div>}
          {state.error && <div className="text-[12px] text-danger">{state.error}</div>}
          {state.data?.runs?.length === 0 && !state.loading && (
            <div className="text-[12px] text-ink-faint">No runs found.</div>
          )}
          {state.data?.runs?.length > 0 && (
            <div className="space-y-1.5">
              {state.data.runs.map((r) => (
                <div
                  key={r.id}
                  className="flex items-center justify-between bg-white border border-line rounded-lg px-3.5 py-2"
                >
                  <div className="flex items-center gap-3">
                    <StatusChip state={r.state} />
                    <span className="text-[12px] font-mono text-ink-soft">{r.run_id || r.id}</span>
                    {r.dataset_name && (
                      <span className="text-[11px] text-ink-faint bg-[#F2F2F4] px-2 py-0.5 rounded-full font-mono">
                        {r.dataset_name}
                      </span>
                    )}
                  </div>
                  <div className="flex items-center gap-3 text-[11.5px] text-ink-faint font-mono">
                    {r.error_message && <span className="text-danger">{r.error_message}</span>}
                    <span>{formatDuration(r.duration_ms)}</span>
                    <span>{formatWhen(r.started_at)}</span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </td>
    </tr>
  );
}

function JobRow({ job, expanded, onToggle }) {
  const isDag = job.job_type === "DAG";
  const run = job.latest_run;
  return (
    <>
      <tr className="border-b border-line last:border-0 hover:bg-[#FAFAFB]">
        <td className="py-3 px-5">
          <div className="flex items-center gap-2">
            {!isDag && <span className="w-3 border-t border-line ml-1" />}
            <span className={`text-[12.5px] font-mono ${isDag ? "font-semibold text-ink" : "text-ink-soft"}`}>
              {job.simple_name}
            </span>
            {isDag && (
              <span className="text-[9px] font-bold px-1.5 py-0.5 rounded-full bg-teal-soft text-teal">DAG</span>
            )}
          </div>
          {job.description && isDag && (
            <div className="text-[11px] text-ink-faint mt-0.5 max-w-md">{job.description}</div>
          )}
        </td>
        <td className="py-3 px-3">{run ? <StatusChip state={run.state} /> : <span className="text-[11px] text-ink-faint">never run</span>}</td>
        <td className="py-3 px-3 text-[12px] text-ink-soft font-mono">{run?.dataset_name ?? "—"}</td>
        <td className="py-3 px-3 text-[12px] text-ink-soft font-mono text-right">{formatDuration(run?.duration_ms)}</td>
        <td className="py-3 px-5 text-right">
          {run && (
            <button onClick={onToggle} className="text-[11.5px] font-semibold text-teal hover:underline">
              {expanded ? "Hide runs" : "View runs"}
            </button>
          )}
        </td>
      </tr>
      {expanded && <RunHistory jobName={job.name} onClose={onToggle} />}
    </>
  );
}

export default function DataCatalog() {
  const { isAuthenticated } = useAuth();
  const [state, setState] = useState({ loading: true, data: null, error: null });
  const [expandedJob, setExpandedJob] = useState(null);
  const [datasetState, setDatasetState] = useState({ loading: true, datasets: [], error: null });
  const [expandedDataset, setExpandedDataset] = useState(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await api.getCatalogJobs();
        if (!cancelled) setState({ loading: false, data: res, error: res.error || null });
      } catch (e) {
        if (!cancelled) setState({ loading: false, data: null, error: e.message });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await api.getDatasets();
        if (!cancelled) setDatasetState({ loading: false, datasets: res.datasets ?? [], error: null });
      } catch (e) {
        if (!cancelled) setDatasetState({ loading: false, datasets: [], error: e.message });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const jobs = state.data?.jobs ?? [];

  return (
    <div className="p-7 md:px-8">
      <div className="mb-5">
        <h1 className="text-xl font-semibold text-ink tracking-tight">Data Catalog</h1>
        <p className="text-[13px] text-ink-faint mt-1">
          Every dataset and DAG/task run this platform actually knows about — live, not a static registry
        </p>
      </div>

      <div className="mb-5">
        <div className="text-[12px] font-bold text-ink-faint uppercase tracking-wide mb-2">Datasets</div>

        {datasetState.error && (
          <div className="mb-3 text-[12.5px] text-danger bg-danger-soft border border-danger/20 rounded-xl px-4 py-3">
            Couldn't reach the dataset list: {datasetState.error}
          </div>
        )}

        {!datasetState.loading && datasetState.datasets.length === 0 && !datasetState.error && (
          <div className="bg-white border border-line rounded-card px-6 py-6 text-center text-[12.5px] text-ink-soft">
            No datasets yet — upload one from the MDM tab first.
          </div>
        )}

        {datasetState.datasets.length > 0 && (
          <div className="bg-white border border-line rounded-card overflow-hidden">
            <table className="w-full text-left">
              <thead>
                <tr className="border-b border-line">
                  <th className="py-2.5 px-5 text-[10.5px] font-bold text-ink-faint uppercase tracking-wide">Dataset</th>
                  <th className="py-2.5 px-3 text-[10.5px] font-bold text-ink-faint uppercase tracking-wide">Stage</th>
                  <th className="py-2.5 px-3 text-[10.5px] font-bold text-ink-faint uppercase tracking-wide text-right">Rows</th>
                  <th className="py-2.5 px-3 text-[10.5px] font-bold text-ink-faint uppercase tracking-wide text-right">Columns</th>
                  <th className="py-2.5 px-3 text-[10.5px] font-bold text-ink-faint uppercase tracking-wide">Updated</th>
                  <th className="py-2.5 px-5 text-[10.5px] font-bold text-ink-faint uppercase tracking-wide text-right">Schema</th>
                </tr>
              </thead>
              <tbody>
                {datasetState.datasets.map((d) => (
                  <DatasetRow
                    key={d.dataset_name}
                    dataset={d}
                    expanded={expandedDataset === d.dataset_name}
                    onToggle={() => setExpandedDataset(expandedDataset === d.dataset_name ? null : d.dataset_name)}
                  />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <GlossarySection canEdit={isAuthenticated} />

      <div className="mb-5">
        <div className="flex items-center justify-between mb-2">
          <div className="text-[12px] font-bold text-ink-faint uppercase tracking-wide">Lineage</div>
          <Link to="/catalog/lineage" className="text-[11.5px] font-semibold text-teal hover:underline">
            Open full graph →
          </Link>
        </div>
        <LineageGraph height={260} showEmptyState={false} showFootnote={false} />
      </div>

      <div className="mb-2">
        <div className="text-[12px] font-bold text-ink-faint uppercase tracking-wide mb-2">
          Jobs &amp; Runs
        </div>
        <p className="text-[12px] text-ink-faint mb-3">
          Every DAG and task Marquez has seen a real OpenLineage event for — live from{" "}
          <code className="font-mono">dataos-marquez.onrender.com</code>
        </p>
      </div>

      {state.error && (
        <div className="mb-4 text-[12.5px] text-danger bg-danger-soft border border-danger/20 rounded-xl px-4 py-3">
          Couldn't reach the catalog: {state.error}
        </div>
      )}

      {state.data && !state.data.configured && (
        <div className="mb-4 text-[12.5px] text-ink-soft bg-[#FFF8E8] border border-[#F0DFAE] rounded-xl px-4 py-3">
          Not connected yet — set <code className="font-mono">MARQUEZ_URL</code> on this service.
        </div>
      )}

      {state.data?.configured && jobs.length === 0 && !state.loading && !state.error && (
        <div className="bg-white border border-line rounded-card px-6 py-8 text-center">
          <div className="text-[14px] font-semibold text-ink mb-1">No jobs registered yet</div>
          <p className="text-[12.5px] text-ink-soft max-w-lg mx-auto leading-relaxed">
            Nothing appears here until a real DAG run happens — run the Lakehouse pipeline from the Zones tab first.
          </p>
        </div>
      )}

      {jobs.length > 0 && (
        <div className="bg-white border border-line rounded-card overflow-hidden">
          <table className="w-full text-left">
            <thead>
              <tr className="border-b border-line">
                <th className="py-2.5 px-5 text-[10.5px] font-bold text-ink-faint uppercase tracking-wide">Job</th>
                <th className="py-2.5 px-3 text-[10.5px] font-bold text-ink-faint uppercase tracking-wide">Latest run</th>
                <th className="py-2.5 px-3 text-[10.5px] font-bold text-ink-faint uppercase tracking-wide">Dataset</th>
                <th className="py-2.5 px-3 text-[10.5px] font-bold text-ink-faint uppercase tracking-wide text-right">Duration</th>
                <th className="py-2.5 px-5 text-[10.5px] font-bold text-ink-faint uppercase tracking-wide text-right">History</th>
              </tr>
            </thead>
            <tbody>
              {jobs.map((job) => (
                <JobRow
                  key={job.name}
                  job={job}
                  expanded={expandedJob === job.name}
                  onToggle={() => setExpandedJob(expandedJob === job.name ? null : job.name)}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="mt-4 text-[11.5px] text-ink-faint">{state.loading || datasetState.loading ? "Loading catalog…" : ""}</div>
    </div>
  );
}
