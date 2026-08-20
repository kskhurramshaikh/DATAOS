import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";

// NDI Assessment (Dev Queue item 5). Domain-level only, per Dr. Saber's
// 2026-08-11 scoping answer -- 14 domains, official weights, 6-level
// maturity scale; the full 191-spec drill-down is deferred.
//
// NO DatasetPicker here, deliberately (see the matching note in
// main.py's NDI section): compute_ndi_assessment() takes no dataset --
// it applies the real SDAIA NDI v1.1 methodology to Dr. Saber's fixed
// BAJ baseline. A picker would switch between identical outputs. The
// 2026-08-18 standing rule covers pages whose data is dataset-scoped;
// this one's genuinely isn't.
//
// The radar is hand-rolled SVG rather than a charting library -- same
// no-new-npm-dependency approach DatasetPicker took. 14 axes of a
// 0-5 scale is trigonometry, not a reason to add a build dependency to
// the Docker frontend stage.
//
// LAYOUT ADDITIONS (2026-08-20), sourced from reviewing a reference
// platform's own NDI page layout -- Khurram's explicit instruction:
// layout only, never touch the maths/metrics (those are Dr. Saber's
// signed-off methodology). All three below are pure presentation over
// data this page (or the History endpoint) already computes -- no new
// backend, no new numbers:
// - TrendMini: a small sparkline over real recorded snapshots (same
//   ndi_history.py data the History tab uses), surfaced on the main
//   page instead of requiring a navigation. Honors the exact same
//   honesty rule ndi_history.py's module docstring states -- with the
//   fixed BAJ baseline in place, snapshots read identically, and this
//   says so in plain text rather than drawing a flat line the reader
//   has to interpret as "no real movement."
// - PriorityDomains: the same 14 domains already on this page,
//   re-sorted lowest-compliance-first -- a reader shouldn't have to
//   scan a 14-row table in code order to find what needs attention.
// - OEComparison: splits the same domains array on the is_oe_domain
//   flag this page already receives and averages each group -- no
//   value here wasn't already being rendered elsewhere on this page.
//
// Deliberately NOT built: a target/benchmark overlay line on the radar
// (the reference platform's own NDI page has one) -- holding that one
// until Khurram confirms whether SDAIA NDI v1.1 has a real documented
// enablement-target level to cite, since drawing an unverified number
// would functionally be introducing a new metric, not a layout choice.

const COMPLIANCE_COLORS = {
  high: { bar: "#2FA37E", text: "text-success", chip: "bg-success-soft text-success" },
  medium: { bar: "#B8952E", text: "text-[#B8952E]", chip: "bg-[#FFF8E8] text-[#B8952E]" },
  low: { bar: "#D6483E", text: "text-danger", chip: "bg-danger-soft text-danger" },
};

function complianceMeta(status) {
  return COMPLIANCE_COLORS[status] ?? COMPLIANCE_COLORS.medium;
}

function Tile({ label, value, sub, accent = "text-ink" }) {
  return (
    <div className="bg-white border border-line rounded-2xl px-5 py-4">
      <div className="text-[11px] font-bold text-ink-faint uppercase tracking-wide">{label}</div>
      <div className={`text-[26px] leading-tight font-semibold font-mono mt-1 ${accent}`}>{value}</div>
      {sub && <div className="text-[11.5px] text-ink-soft mt-0.5">{sub}</div>}
    </div>
  );
}

function Radar({ domains }) {
  const W = 470;
  const H = 410;
  const cx = 235;
  const cy = 200;
  const R = 126;
  const n = domains.length;

  const point = (i, ratio) => {
    const angle = (-90 + (360 / n) * i) * (Math.PI / 180);
    return [cx + Math.cos(angle) * R * ratio, cy + Math.sin(angle) * R * ratio];
  };

  const ringPoly = (level) =>
    domains.map((_, i) => point(i, level / 5).join(",")).join(" ");

  const valuePoly = domains.map((d, i) => point(i, Math.max(0, Math.min(5, d.maturity_score)) / 5).join(",")).join(" ");

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full max-w-[470px]">
      {/* Scale rings -- one per maturity level, so the reader can read a
          domain's score straight off the chart instead of guessing. */}
      {[1, 2, 3, 4, 5].map((level) => (
        <polygon
          key={level}
          points={ringPoly(level)}
          fill="none"
          stroke="#ECECEE"
          strokeWidth={level === 5 ? 1.4 : 1}
        />
      ))}

      {domains.map((d, i) => {
        const [x, y] = point(i, 1);
        return <line key={`axis-${d.code}`} x1={cx} y1={cy} x2={x} y2={y} stroke="#F2F2F4" strokeWidth="1" />;
      })}

      <polygon points={valuePoly} fill="#0F7A6B" fillOpacity="0.14" stroke="#0F7A6B" strokeWidth="1.8" strokeLinejoin="round" />

      {domains.map((d, i) => {
        const [x, y] = point(i, Math.max(0, Math.min(5, d.maturity_score)) / 5);
        return <circle key={`pt-${d.code}`} cx={x} cy={y} r="3.2" fill={complianceMeta(d.compliance_status).bar} />;
      })}

      {domains.map((d, i) => {
        const [x, y] = point(i, 1.17);
        const anchor = Math.abs(x - cx) < 6 ? "middle" : x > cx ? "start" : "end";
        return (
          <g key={`label-${d.code}`}>
            <text x={x} y={y} textAnchor={anchor} className="fill-ink" fontSize="11" fontWeight="700">
              {d.code}
            </text>
            <text x={x} y={y + 12} textAnchor={anchor} className="fill-ink-faint" fontSize="9.5" fontFamily="ui-monospace, monospace">
              {d.maturity_score.toFixed(1)}
            </text>
          </g>
        );
      })}

      <text x={cx} y={H - 8} textAnchor="middle" className="fill-ink-faint" fontSize="10">
        Maturity 0–5 · outer ring = 5 (Leading) · dot colour = compliance band
      </text>
    </svg>
  );
}

function DomainRow({ domain }) {
  const meta = complianceMeta(domain.compliance_status);
  return (
    <tr className="border-b border-line last:border-0">
      <td className="py-2.5 pr-3">
        <div className="flex items-center gap-2">
          <span className="text-[12px] font-bold text-ink font-mono">{domain.code}</span>
          {domain.is_oe_domain && (
            <span className="text-[9px] font-bold px-1.5 py-0.5 rounded-full bg-teal-soft text-teal">OE</span>
          )}
        </div>
        <div className="text-[11.5px] text-ink-soft leading-tight">{domain.name}</div>
      </td>
      <td className="py-2.5 px-3 text-right text-[12.5px] font-mono text-ink-soft">{domain.spec_count}</td>
      <td className="py-2.5 px-3 text-right text-[13px] font-mono font-semibold text-ink">
        {domain.maturity_score.toFixed(1)}
      </td>
      <td className="py-2.5 px-3 min-w-[150px]">
        <div className="flex items-center gap-2.5">
          <div className="flex-1 h-[6px] rounded-full bg-[#F2F2F4] overflow-hidden">
            <div
              className="h-full rounded-full"
              style={{ width: `${Math.max(0, Math.min(100, domain.compliance_pct))}%`, backgroundColor: meta.bar }}
            />
          </div>
          <span className={`text-[12px] font-mono font-semibold w-[42px] text-right ${meta.text}`}>
            {domain.compliance_pct}%
          </span>
        </div>
      </td>
      <td className="py-2.5 pl-3 text-[11.5px] text-ink-soft">{domain.evidence}</td>
    </tr>
  );
}

function TrendMini({ history }) {
  const snaps = history?.snapshots ?? [];
  const chrono = [...snaps].reverse(); // oldest -> newest, for a left-to-right read

  if (chrono.length === 0) {
    return (
      <div className="bg-white border border-line rounded-card px-5 py-4">
        <div className="text-[12px] font-bold text-ink-faint uppercase tracking-wide mb-1">Trend</div>
        <div className="text-[12px] text-ink-faint">No assessments recorded yet.</div>
      </div>
    );
  }

  const W = 260;
  const H = 90;
  const pad = 8;
  const scores = chrono.map((s) => s.display_score);
  const min = Math.min(...scores);
  const max = Math.max(...scores);
  const span = max - min || 1; // avoid divide-by-zero when every point is identical
  const x = (i) => pad + (chrono.length > 1 ? (i * (W - 2 * pad)) / (chrono.length - 1) : (W - 2 * pad) / 2);
  const y = (v) => H - pad - ((v - min) / span) * (H - 2 * pad);
  const linePoints = chrono.map((s, i) => `${x(i)},${y(s.display_score)}`).join(" ");

  return (
    <div className="bg-white border border-line rounded-card px-5 py-4">
      <div className="flex items-center justify-between mb-1">
        <div className="text-[12px] font-bold text-ink-faint uppercase tracking-wide">Trend</div>
        <Link to="/ndi/history" className="text-[11px] font-semibold text-teal hover:underline">
          Full history →
        </Link>
      </div>
      {history.all_identical ? (
        <div className="text-[11.5px] text-ink-soft mb-2">
          {chrono.length} assessment{chrono.length === 1 ? "" : "s"} recorded, all reading identically — the
          baseline hasn't changed since the first one.
        </div>
      ) : (
        <div className="text-[11.5px] text-ink-soft mb-2">
          {chrono.length} assessments recorded — display score {chrono[0].display_score} → {chrono[chrono.length - 1].display_score}.
        </div>
      )}
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full max-w-[260px]">
        <line x1={pad} y1={H - pad} x2={W - pad} y2={H - pad} stroke="#F2F2F4" strokeWidth="1" />
        {chrono.length > 1 && (
          <polyline points={linePoints} fill="none" stroke="#0F7A6B" strokeWidth="1.8" strokeLinejoin="round" />
        )}
        {chrono.map((s, i) => (
          <circle key={s.id} cx={x(i)} cy={y(s.display_score)} r="2.6" fill="#0F7A6B" />
        ))}
      </svg>
    </div>
  );
}

function PriorityDomains({ domains }) {
  const sorted = [...domains].sort((a, b) => a.compliance_pct - b.compliance_pct).slice(0, 6);
  const maxPct = Math.max(...domains.map((d) => d.compliance_pct), 1);

  return (
    <div className="bg-white border border-line rounded-card px-5 py-4">
      <div className="text-[12px] font-bold text-ink-faint uppercase tracking-wide mb-2">
        Priority domains — lowest compliance first
      </div>
      <div className="flex flex-col gap-2">
        {sorted.map((d) => {
          const meta = complianceMeta(d.compliance_status);
          return (
            <div key={d.code} className="flex items-center gap-2.5">
              <span className="text-[11px] font-bold text-ink font-mono w-9 shrink-0">{d.code}</span>
              <div className="flex-1 h-[7px] rounded-full bg-[#F2F2F4] overflow-hidden">
                <div
                  className="h-full rounded-full"
                  style={{ width: `${Math.max(0, Math.min(100, (d.compliance_pct / maxPct) * 100))}%`, backgroundColor: meta.bar }}
                />
              </div>
              <span className={`text-[11.5px] font-mono font-semibold w-[38px] text-right ${meta.text}`}>
                {d.compliance_pct}%
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function OEComparison({ domains }) {
  const oe = domains.filter((d) => d.is_oe_domain);
  const other = domains.filter((d) => !d.is_oe_domain);
  const avg = (arr, key) => (arr.length ? arr.reduce((s, d) => s + d[key], 0) / arr.length : 0);

  const rows = [
    { label: "Avg. maturity (0–5)", oeVal: avg(oe, "maturity_score"), otherVal: avg(other, "maturity_score"), max: 5, fmt: (v) => v.toFixed(1) },
    { label: "Avg. compliance", oeVal: avg(oe, "compliance_pct"), otherVal: avg(other, "compliance_pct"), max: 100, fmt: (v) => `${Math.round(v)}%` },
  ];

  return (
    <div className="bg-white border border-line rounded-card px-5 py-4">
      <div className="text-[12px] font-bold text-ink-faint uppercase tracking-wide mb-2">
        Operational Excellence ({oe.length}) vs. other domains ({other.length})
      </div>
      <div className="flex flex-col gap-3">
        {rows.map((r) => (
          <div key={r.label}>
            <div className="text-[11px] text-ink-faint mb-1">{r.label}</div>
            <div className="flex items-center gap-2 mb-1">
              <span className="text-[10px] font-bold text-teal w-7 shrink-0">OE</span>
              <div className="flex-1 h-[7px] rounded-full bg-[#F2F2F4] overflow-hidden">
                <div className="h-full rounded-full bg-teal" style={{ width: `${Math.max(0, Math.min(100, (r.oeVal / r.max) * 100))}%` }} />
              </div>
              <span className="text-[11px] font-mono font-semibold w-[38px] text-right text-ink">{r.fmt(r.oeVal)}</span>
            </div>
            <div className="flex items-center gap-2">
              <span className="text-[10px] font-bold text-ink-faint w-7 shrink-0">Other</span>
              <div className="flex-1 h-[7px] rounded-full bg-[#F2F2F4] overflow-hidden">
                <div className="h-full rounded-full bg-[#B8952E]" style={{ width: `${Math.max(0, Math.min(100, (r.otherVal / r.max) * 100))}%` }} />
              </div>
              <span className="text-[11px] font-mono font-semibold w-[38px] text-right text-ink">{r.fmt(r.otherVal)}</span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function RecordSnapshot({ onRecorded }) {
  const [recordedBy, setRecordedBy] = useState("");
  const [note, setNote] = useState("");
  const [state, setState] = useState({ saving: false, error: null, saved: null });

  async function submit() {
    if (!recordedBy.trim()) {
      setState({ saving: false, error: "Enter who's recording this assessment.", saved: null });
      return;
    }
    setState({ saving: true, error: null, saved: null });
    try {
      const saved = await api.recordNdiSnapshot(recordedBy.trim(), note.trim());
      setState({ saving: false, error: null, saved });
      setNote("");
      onRecorded?.();
    } catch (e) {
      setState({ saving: false, error: e.message, saved: null });
    }
  }

  return (
    <div className="bg-white border border-line rounded-card px-6 py-5">
      <div className="text-[12px] font-bold text-ink-faint uppercase tracking-wide mb-1">Record this assessment</div>
      <p className="text-[12.5px] text-ink-soft mb-4 max-w-2xl leading-relaxed">
        Saves the full 14-domain result above as a dated, attributed record in the History tab. Stored as it reads
        right now — an old record keeps showing what was actually assessed then, even if the baseline or methodology
        changes later.
      </p>
      <div className="flex flex-wrap items-start gap-2.5">
        <input
          value={recordedBy}
          onChange={(e) => setRecordedBy(e.target.value)}
          placeholder="Recorded by (name or email)"
          className="text-[12.5px] bg-white border border-line rounded-xl px-3.5 py-2 w-[250px] focus:outline-none focus:border-ink-faint"
        />
        <input
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="Note (optional) — e.g. Q3 baseline review"
          className="text-[12.5px] bg-white border border-line rounded-xl px-3.5 py-2 flex-1 min-w-[240px] focus:outline-none focus:border-ink-faint"
        />
        <button
          onClick={submit}
          disabled={state.saving}
          className="text-[12.5px] font-semibold px-4 py-2 rounded-xl bg-teal text-white hover:opacity-90 disabled:opacity-40 disabled:cursor-not-allowed"
        >
          {state.saving ? "Recording…" : "Record assessment"}
        </button>
      </div>

      {state.error && (
        <div className="mt-3 text-[12.5px] text-danger bg-danger-soft border border-danger/20 rounded-xl px-4 py-2.5">
          {state.error}
        </div>
      )}
      {state.saved && (
        <div className="mt-3 text-[12.5px] text-success bg-success-soft border border-success/20 rounded-xl px-4 py-2.5">
          Recorded as #{state.saved.id} by {state.saved.recorded_by}.{" "}
          <Link to="/ndi/history" className="underline font-semibold">
            View in History
          </Link>
        </div>
      )}
    </div>
  );
}

export default function NdiDashboard() {
  const [state, setState] = useState({ loading: true, data: null, error: null });
  const [history, setHistory] = useState(null);

  // Fetched once, not polled on an interval like the Lakehouse/SAMA
  // pages. Those read live infrastructure that genuinely changes
  // underneath them; this one is a deterministic computation over a
  // fixed baseline, so a 15s poll would burn requests to redraw an
  // identical screen.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await api.getNdi();
        if (!cancelled) setState({ loading: false, data: res, error: null });
      } catch (e) {
        if (!cancelled) setState({ loading: false, data: null, error: e.message });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // Fetched once alongside the main assessment, same non-polling
  // reasoning as above -- powers TrendMini below.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await api.getNdiHistory();
        if (!cancelled) setHistory(res);
      } catch (e) {
        if (!cancelled) setHistory(null);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const d = state.data;

  return (
    <div className="p-7 md:px-8">
      <div className="mb-5">
        <h1 className="text-xl font-semibold text-ink tracking-tight">NDI Assessment</h1>
        <p className="text-[13px] text-ink-faint mt-1">
          SDAIA National Data Index v1.1 — 14 domains, official weights, 6-level maturity scale
        </p>
      </div>

      {state.error && (
        <div className="mb-4 text-[12.5px] text-danger bg-danger-soft border border-danger/20 rounded-xl px-4 py-3">
          Couldn't reach the API: {state.error}
        </div>
      )}

      {d && (
        <>
          <div className="grid grid-cols-2 lg:grid-cols-5 gap-3 mb-5">
            <Tile label="Display score" value={`${d.display_score}`} sub="out of 100" accent="text-teal" />
            <Tile label="Maturity level" value={d.maturity_level} sub={`weighted score ${d.overall_maturity_score}/5`} />
            <Tile label="Compliance" value={`${d.overall_compliance_pct}%`} sub="across all specifications" />
            <Tile label="OE score" value={`${d.overall_oe_score}`} sub="6 operational-excellence domains" />
            <Tile label="Specifications" value={`${d.compliant_specs}/${d.total_specs}`} sub="compliant / total" />
          </div>

          <div className="grid grid-cols-1 xl:grid-cols-[minmax(0,470px)_1fr] gap-5 mb-5 items-start">
            <div className="bg-white border border-line rounded-card px-5 py-4 flex flex-col items-center">
              <div className="text-[12px] font-bold text-ink-faint uppercase tracking-wide mb-1 self-start">
                Domain radar
              </div>
              <Radar domains={d.domains} />
            </div>

            <div className="bg-white border border-line rounded-card px-6 py-4 overflow-x-auto">
              <div className="text-[12px] font-bold text-ink-faint uppercase tracking-wide mb-2">
                Domains ({d.domains.length})
              </div>
              <table className="w-full text-left">
                <thead>
                  <tr className="border-b border-line">
                    <th className="py-2 pr-3 text-[10.5px] font-bold text-ink-faint uppercase tracking-wide">Domain</th>
                    <th className="py-2 px-3 text-[10.5px] font-bold text-ink-faint uppercase tracking-wide text-right">Specs</th>
                    <th className="py-2 px-3 text-[10.5px] font-bold text-ink-faint uppercase tracking-wide text-right">Maturity</th>
                    <th className="py-2 px-3 text-[10.5px] font-bold text-ink-faint uppercase tracking-wide">Compliance</th>
                    <th className="py-2 pl-3 text-[10.5px] font-bold text-ink-faint uppercase tracking-wide">Evidence</th>
                  </tr>
                </thead>
                <tbody>
                  {d.domains.map((domain) => (
                    <DomainRow key={domain.code} domain={domain} />
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-3 gap-5 mb-5">
            <PriorityDomains domains={d.domains} />
            <OEComparison domains={d.domains} />
            <TrendMini history={history} />
          </div>

          <div className="mb-5">
            <RecordSnapshot />
          </div>

          <div className="text-[11px] text-ink-faint leading-relaxed max-w-3xl">{d.methodology_note}</div>
        </>
      )}

      <div className="mt-4 text-[11.5px] text-ink-faint">
        {state.loading ? "Loading assessment…" : "Computed live from the NDI v1.1 methodology."}
      </div>
    </div>
  );
}
