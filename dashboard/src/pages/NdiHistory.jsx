import { Fragment, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";

// NDI Assessment History (Dev Queue item 5, second half).
//
// This page shows recorded assessments and nothing else. It does NOT
// draw a modelled or back-filled trend: the per-domain inputs are Dr.
// Saber's fixed BAJ baseline today, so every recorded assessment scores
// the same, and the page says that outright rather than presenting a
// flat line for the reader to interpret (or, worse, a synthetic
// improvement curve). See app/adapters/ndi_history.py's module
// docstring for the full reasoning.

// Small reusable "Export: CSV · Excel · PDF" link group -- used at both
// the full-history level and per-snapshot level below. Real files for
// all three (see ndi_history.py's export_*_csv/xlsx/pdf), not a CSV
// renamed -- closes the "export ships as CSV, not the literal Excel/
// PDF named in the doc" gap.
function ExportLinks({ csvUrl, xlsxUrl, pdfUrl, className = "" }) {
  return (
    <div className={`flex items-center gap-1 text-[11px] font-semibold text-teal ${className}`}>
      <span className="text-ink-faint font-normal mr-0.5">Export:</span>
      <a href={csvUrl} className="hover:underline">CSV</a>
      <span className="text-ink-faint">·</span>
      <a href={xlsxUrl} className="hover:underline">Excel</a>
      <span className="text-ink-faint">·</span>
      <a href={pdfUrl} className="hover:underline">PDF</a>
    </div>
  );
}

function formatWhen(ts) {
  if (!ts) return "—";
  return String(ts).replace("T", " ").slice(0, 16) + " UTC";
}

// Period/quarter filter (2026-08-20) -- Item B (UI polish), part 3 of
// the remaining 4. Sourced from a reference platform's own sidebar
// quarter selector; rebuilt here as a plain client-side filter over
// the real recorded snapshots this page already has -- no new
// backend, no synthetic periods invented for datasets that don't have
// a record in them.
//
// REAL BUG FOUND AND FIXED (2026-08-20, live testing): recorded_at
// comes back from the backend as UTC but without a 'Z'/offset suffix
// (db.py's own CURRENT_TIMESTAMP formatting -- see its docstring), so
// `new Date(recordedAt)` parsed it as the VIEWER'S LOCAL time, not
// UTC, while getUTCMonth()/getUTCFullYear() then read it back as UTC
// -- a genuine mismatch. Confirmed live in a UTC+5 browser: an
// "2026-04-01T00:00:00" record landed in Q1 instead of Q2. Appending
// 'Z' when the string has no explicit zone forces UTC parsing, making
// the period bucketing correct regardless of the viewer's timezone.
function getPeriod(recordedAt) {
  const iso = /Z|[+-]\d{2}:\d{2}$/.test(recordedAt) ? recordedAt : `${recordedAt}Z`;
  const d = new Date(iso);
  const q = Math.floor(d.getUTCMonth() / 3) + 1;
  return `${d.getUTCFullYear()} Q${q}`;
}

function Delta({ value, suffix = "" }) {
  if (value === null || value === undefined) {
    return <span className="text-[11px] text-ink-faint">first record</span>;
  }
  if (value === 0) {
    return <span className="text-[11px] text-ink-faint font-mono">no change</span>;
  }
  const up = value > 0;
  return (
    <span className={`text-[11px] font-mono font-semibold ${up ? "text-success" : "text-danger"}`}>
      {up ? "+" : ""}
      {value}
      {suffix}
    </span>
  );
}

function Sparkline({ snapshots }) {
  // snapshots arrive newest-first; chart reads left-to-right oldest-first.
  const points = [...snapshots].reverse();
  const W = 620;
  const H = 110;
  const pad = 14;
  const scores = points.map((p) => p.display_score);
  const min = Math.min(...scores);
  const max = Math.max(...scores);
  // A genuinely flat series would otherwise divide by zero and/or render
  // pinned to the top edge -- centre it instead, which is also the
  // visually honest reading of "nothing moved."
  const span = max - min || 1;
  const x = (i) => pad + (i * (W - pad * 2)) / Math.max(1, points.length - 1);
  const y = (v) => (max === min ? H / 2 : H - pad - ((v - min) / span) * (H - pad * 2));
  const path = points.map((p, i) => `${i === 0 ? "M" : "L"}${x(i)},${y(p.display_score)}`).join(" ");

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full">
      <path d={path} fill="none" stroke="#0F7A6B" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      {points.map((p, i) => (
        <circle key={p.id} cx={x(i)} cy={y(p.display_score)} r="3.4" fill="#0F7A6B" />
      ))}
    </svg>
  );
}

function DomainDetail({ snapshotId }) {
  const [state, setState] = useState({ loading: true, data: null, error: null });

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await api.getNdiSnapshot(snapshotId);
        if (!cancelled) setState({ loading: false, data: res, error: null });
      } catch (e) {
        if (!cancelled) setState({ loading: false, data: null, error: e.message });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [snapshotId]);

  if (state.loading) return <div className="px-4 py-3 text-[12px] text-ink-faint">Loading recorded detail…</div>;
  if (state.error) return <div className="px-4 py-3 text-[12px] text-danger">{state.error}</div>;

  return (
    <div className="px-4 py-3 bg-[#FAFAFB] border-t border-line">
      <div className="flex items-center justify-between mb-2">
        <div className="text-[11px] font-bold text-ink-faint uppercase tracking-wide">
          14 domains as recorded
        </div>
        <ExportLinks
          csvUrl={api.ndiSnapshotExportUrl(snapshotId)}
          xlsxUrl={api.ndiSnapshotExportXlsxUrl(snapshotId)}
          pdfUrl={api.ndiSnapshotExportPdfUrl(snapshotId)}
        />
      </div>
      <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-7 gap-2.5">
        {state.data.domains.map((d) => (
          <div key={d.code} className="bg-white border border-line rounded-xl px-3 py-2">
            <div className="text-[10.5px] font-bold text-ink-faint font-mono">{d.code}</div>
            <div className="text-[15px] font-semibold font-mono text-ink leading-tight">{d.maturity_score.toFixed(1)}</div>
            <div className="text-[10.5px] text-ink-soft">{d.compliance_pct}% compliant</div>
          </div>
        ))}
      </div>
    </div>
  );
}

function CompareDelta({ value, suffix = "" }) {
  if (value === 0) {
    return <span className="text-[11px] text-ink-faint font-mono">no change</span>;
  }
  const up = value > 0;
  return (
    <span className={`text-[11px] font-mono font-semibold ${up ? "text-success" : "text-danger"}`}>
      {up ? "+" : ""}
      {value}
      {suffix}
    </span>
  );
}

function ComparePanel({ snapshots }) {
  const [idA, setIdA] = useState("");
  const [idB, setIdB] = useState("");
  const [state, setState] = useState({ loading: false, data: null, error: null });

  async function runCompare() {
    if (!idA || !idB) return;
    if (idA === idB) {
      setState({ loading: false, data: null, error: "Pick two different assessments to compare." });
      return;
    }
    setState({ loading: true, data: null, error: null });
    try {
      const res = await api.compareNdiSnapshots(idA, idB);
      setState({ loading: false, data: res, error: null });
    } catch (e) {
      setState({ loading: false, data: null, error: e.message });
    }
  }

  const options = [...snapshots].reverse(); // oldest first in the pickers, easier to reason about "from -> to"

  return (
    <div className="bg-white border border-line rounded-card px-6 py-4 mb-5">
      <div className="text-[12px] font-bold text-ink-faint uppercase tracking-wide mb-2.5">
        Compare two assessments
      </div>
      <div className="flex flex-wrap items-center gap-2.5">
        <select
          value={idA}
          onChange={(e) => setIdA(e.target.value)}
          className="text-[12.5px] border border-line rounded-lg px-3 py-1.5 bg-white"
        >
          <option value="">First assessment…</option>
          {options.map((s) => (
            <option key={s.id} value={s.id}>
              {formatWhen(s.recorded_at)} — {s.recorded_by}
            </option>
          ))}
        </select>
        <span className="text-[12px] text-ink-faint">vs</span>
        <select
          value={idB}
          onChange={(e) => setIdB(e.target.value)}
          className="text-[12.5px] border border-line rounded-lg px-3 py-1.5 bg-white"
        >
          <option value="">Second assessment…</option>
          {options.map((s) => (
            <option key={s.id} value={s.id}>
              {formatWhen(s.recorded_at)} — {s.recorded_by}
            </option>
          ))}
        </select>
        <button
          onClick={runCompare}
          disabled={!idA || !idB || state.loading}
          className="text-[12px] font-semibold text-white bg-teal px-3.5 py-1.5 rounded-lg hover:opacity-90 disabled:opacity-50"
        >
          {state.loading ? "Comparing…" : "Compare"}
        </button>
      </div>

      {state.error && <div className="mt-3 text-[12px] text-danger">{state.error}</div>}

      {state.data && (
        <div className="mt-4">
          <div className="text-[11.5px] text-ink-soft mb-2">
            {formatWhen(state.data.from.recorded_at)} ({state.data.from.recorded_by}) →{" "}
            {formatWhen(state.data.to.recorded_at)} ({state.data.to.recorded_by})
          </div>

          {state.data.identical && (
            <div className="mb-3 text-[12px] text-ink-soft bg-[#FFF8E8] border border-[#F0DFAE] rounded-xl px-3.5 py-2.5">
              These two assessments scored identically — expected while the per-domain inputs are the
              fixed BAJ baseline.
            </div>
          )}

          <div className="grid grid-cols-3 gap-3 mb-3.5">
            <div className="bg-[#FAFAFB] border border-line rounded-xl px-3.5 py-2.5">
              <div className="text-[10.5px] font-bold text-ink-faint uppercase tracking-wide">Score</div>
              <div className="text-[16px] font-mono font-semibold text-ink mt-0.5">{state.data.to.display_score}</div>
              <CompareDelta value={state.data.delta_display_score} />
            </div>
            <div className="bg-[#FAFAFB] border border-line rounded-xl px-3.5 py-2.5">
              <div className="text-[10.5px] font-bold text-ink-faint uppercase tracking-wide">Compliance</div>
              <div className="text-[16px] font-mono font-semibold text-ink mt-0.5">{state.data.to.overall_compliance_pct}%</div>
              <CompareDelta value={state.data.delta_compliance_pct} suffix="pp" />
            </div>
            <div className="bg-[#FAFAFB] border border-line rounded-xl px-3.5 py-2.5">
              <div className="text-[10.5px] font-bold text-ink-faint uppercase tracking-wide">Maturity level</div>
              <div className="text-[13.5px] font-medium text-ink mt-1.5">{state.data.to.maturity_level}</div>
            </div>
          </div>

          <div className="text-[10.5px] font-bold text-ink-faint uppercase tracking-wide mb-1.5">
            Per-domain movement
          </div>
          <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-7 gap-2">
            {state.data.domains.map((d) => (
              <div key={d.code} className="bg-[#FAFAFB] border border-line rounded-lg px-2.5 py-2">
                <div className="text-[10px] font-bold text-ink-faint font-mono">{d.code}</div>
                {d.comparable ? (
                  <>
                    <div className="text-[12.5px] font-mono font-semibold text-ink">{d.maturity_score_to.toFixed(1)}</div>
                    <CompareDelta value={d.delta_maturity_score} />
                  </>
                ) : (
                  <div className="text-[10px] text-ink-faint">not comparable</div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

export default function NdiHistory() {
  const [state, setState] = useState({ loading: true, data: null, error: null });
  const [expanded, setExpanded] = useState(null);
  const [periodFilter, setPeriodFilter] = useState("all");

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await api.getNdiHistory();
        if (!cancelled) setState({ loading: false, data: res, error: null });
      } catch (e) {
        if (!cancelled) setState({ loading: false, data: null, error: e.message });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const d = state.data;
  const snapshots = d?.snapshots ?? [];

  const periods = useMemo(() => {
    const set = new Set(snapshots.map((s) => getPeriod(s.recorded_at)));
    return Array.from(set).sort().reverse();
  }, [snapshots]);

  // Filters the trend chart, comparison panel, and table rows -- the
  // 4 top tiles deliberately stay on the overall latest record
  // (snapshots[0]) regardless of filter, matching "current state"
  // rather than "state as of the selected period."
  const filteredSnapshots = periodFilter === "all" ? snapshots : snapshots.filter((s) => getPeriod(s.recorded_at) === periodFilter);

  return (
    <div className="p-7 md:px-8">
      <div className="mb-5 flex items-start justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-xl font-semibold text-ink tracking-tight">NDI Assessment History</h1>
          <p className="text-[13px] text-ink-faint mt-1">
            Every assessment recorded — when it was taken, by whom, and exactly what it said
          </p>
        </div>
        {snapshots.length > 0 && (
          <div className="flex items-center gap-2.5 flex-wrap">
            {periods.length > 1 && (
              <select
                value={periodFilter}
                onChange={(e) => setPeriodFilter(e.target.value)}
                className="text-[12px] border border-line rounded-lg px-2.5 py-2 bg-white"
              >
                <option value="all">All periods</option>
                {periods.map((p) => (
                  <option key={p} value={p}>
                    {p}
                  </option>
                ))}
              </select>
            )}
            <ExportLinks
              csvUrl={api.ndiHistoryExportUrl()}
              xlsxUrl={api.ndiHistoryExportXlsxUrl()}
              pdfUrl={api.ndiHistoryExportPdfUrl()}
              className="bg-teal-soft px-3.5 py-2 rounded-lg"
            />
          </div>
        )}
      </div>

      {state.error && (
        <div className="mb-4 text-[12.5px] text-danger bg-danger-soft border border-danger/20 rounded-xl px-4 py-3">
          Couldn't reach the API: {state.error}
        </div>
      )}

      {d && snapshots.length === 0 && (
        <div className="bg-white border border-line rounded-card px-6 py-8 text-center">
          <div className="text-[14px] font-semibold text-ink mb-1">No assessments recorded yet</div>
          <p className="text-[12.5px] text-ink-soft max-w-lg mx-auto leading-relaxed">
            Nothing is back-filled here — history starts from the first assessment someone actually records.
            Record one from the{" "}
            <Link to="/ndi" className="text-teal underline font-semibold">
              Assessment tab
            </Link>
            .
          </p>
        </div>
      )}

      {d && snapshots.length > 0 && (
        <>
          {d.all_identical && (
            <div className="mb-4 text-[12.5px] text-ink-soft bg-[#FFF8E8] border border-[#F0DFAE] rounded-xl px-4 py-3 leading-relaxed">
              <span className="font-semibold text-ink">Every recorded assessment scores identically.</span>{" "}
              That's expected, not a bug: the per-domain maturity and compliance inputs are currently the fixed BAJ
              demo baseline, so re-running the assessment can't move the score. This becomes a real trend the moment
              those inputs become editable or data-derived — nothing synthetic has been added in the meantime.
            </div>
          )}

          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-5">
            <div className="bg-white border border-line rounded-2xl px-5 py-4">
              <div className="text-[11px] font-bold text-ink-faint uppercase tracking-wide">Records</div>
              <div className="text-[26px] font-semibold font-mono text-ink mt-1">{d.count}</div>
            </div>
            <div className="bg-white border border-line rounded-2xl px-5 py-4">
              <div className="text-[11px] font-bold text-ink-faint uppercase tracking-wide">Latest score</div>
              <div className="text-[26px] font-semibold font-mono text-teal mt-1">{snapshots[0].display_score}</div>
              <div className="text-[11.5px] text-ink-soft mt-0.5">{snapshots[0].maturity_level}</div>
            </div>
            <div className="bg-white border border-line rounded-2xl px-5 py-4">
              <div className="text-[11px] font-bold text-ink-faint uppercase tracking-wide">Latest compliance</div>
              <div className="text-[26px] font-semibold font-mono text-ink mt-1">{snapshots[0].overall_compliance_pct}%</div>
            </div>
            <div className="bg-white border border-line rounded-2xl px-5 py-4">
              <div className="text-[11px] font-bold text-ink-faint uppercase tracking-wide">Last recorded</div>
              <div className="text-[13.5px] font-mono text-ink mt-2 leading-tight">{formatWhen(snapshots[0].recorded_at)}</div>
              <div className="text-[11.5px] text-ink-soft mt-0.5 truncate">{snapshots[0].recorded_by}</div>
            </div>
          </div>

          <div className="bg-white border border-line rounded-card px-6 py-4 mb-5">
            <div className="text-[12px] font-bold text-ink-faint uppercase tracking-wide mb-2">
              Display score over recorded assessments{periodFilter !== "all" ? ` — ${periodFilter}` : ""}
            </div>
            {filteredSnapshots.length < 2 ? (
              <div className="text-[12.5px] text-ink-soft py-6 text-center">
                {periodFilter !== "all"
                  ? `Only ${filteredSnapshots.length} record(s) in ${periodFilter} — a line needs at least two points.`
                  : "One record so far — a line needs at least two points. Record another assessment to start a series."}
              </div>
            ) : (
              <Sparkline snapshots={filteredSnapshots} />
            )}
          </div>

          {filteredSnapshots.length >= 2 && <ComparePanel snapshots={filteredSnapshots} />}

          <div className="bg-white border border-line rounded-card overflow-hidden">
            <table className="w-full text-left">
              <thead>
                <tr className="border-b border-line">
                  <th className="py-2.5 px-5 text-[10.5px] font-bold text-ink-faint uppercase tracking-wide">Recorded</th>
                  <th className="py-2.5 px-3 text-[10.5px] font-bold text-ink-faint uppercase tracking-wide">By</th>
                  <th className="py-2.5 px-3 text-[10.5px] font-bold text-ink-faint uppercase tracking-wide text-right">Score</th>
                  <th className="py-2.5 px-3 text-[10.5px] font-bold text-ink-faint uppercase tracking-wide">Level</th>
                  <th className="py-2.5 px-3 text-[10.5px] font-bold text-ink-faint uppercase tracking-wide text-right">Compliance</th>
                  <th className="py-2.5 px-3 text-[10.5px] font-bold text-ink-faint uppercase tracking-wide">Note</th>
                  <th className="py-2.5 px-5 text-[10.5px] font-bold text-ink-faint uppercase tracking-wide text-right">Detail</th>
                </tr>
              </thead>
              <tbody>
                {filteredSnapshots.length === 0 && (
                  <tr>
                    <td colSpan={7} className="py-4 px-5 text-[12px] text-ink-faint text-center">
                      No assessments recorded in {periodFilter}.
                    </td>
                  </tr>
                )}
                {filteredSnapshots.map((s) => (
                  <Fragment key={s.id}>
                    <tr className="border-b border-line last:border-0">
                      <td className="py-3 px-5 text-[12px] font-mono text-ink">{formatWhen(s.recorded_at)}</td>
                      <td className="py-3 px-3 text-[12.5px] text-ink-soft">{s.recorded_by}</td>
                      <td className="py-3 px-3 text-right">
                        <div className="text-[13px] font-mono font-semibold text-ink">{s.display_score}</div>
                        <Delta value={s.delta_display_score} />
                      </td>
                      <td className="py-3 px-3 text-[12px] text-ink-soft">{s.maturity_level}</td>
                      <td className="py-3 px-3 text-right">
                        <div className="text-[13px] font-mono text-ink">{s.overall_compliance_pct}%</div>
                        <Delta value={s.delta_compliance_pct} suffix="pp" />
                      </td>
                      <td className="py-3 px-3 text-[12px] text-ink-soft">{s.note ?? "—"}</td>
                      <td className="py-3 px-5 text-right">
                        <button
                          onClick={() => setExpanded(expanded === s.id ? null : s.id)}
                          className="text-[11.5px] font-semibold text-teal hover:underline"
                        >
                          {expanded === s.id ? "Hide" : "View domains"}
                        </button>
                      </td>
                    </tr>
                    {expanded === s.id && (
                      <tr>
                        <td colSpan={7} className="p-0">
                          <DomainDetail snapshotId={s.id} />
                        </td>
                      </tr>
                    )}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </div>

          <div className="mt-4 text-[11px] text-ink-faint leading-relaxed max-w-3xl">{d.note}</div>
        </>
      )}

      <div className="mt-4 text-[11.5px] text-ink-faint">{state.loading ? "Loading history…" : ""}</div>
    </div>
  );
}
