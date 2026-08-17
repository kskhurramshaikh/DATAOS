import { useEffect, useState } from "react";
import { api } from "../api";

function StatusPill({ status }) {
  const confirmed = status === "confirmed_duplicate";
  return (
    <span
      className={`text-[10.5px] font-semibold px-2 py-0.5 rounded-full ${
        confirmed ? "bg-success-soft text-success" : "bg-[#F4F4F5] text-ink-faint"
      }`}
    >
      {confirmed ? "Merged" : "Rejected"}
    </span>
  );
}

function AuditRow({ entry }) {
  return (
    <tr className="border-b border-line last:border-0">
      <td className="py-2.5 pr-4 text-[12px] text-ink font-medium">{entry.members?.[0]?.name ?? "—"}</td>
      <td className="py-2.5 pr-4 text-[11.5px] text-ink-faint">{entry.members?.length ?? 0} records</td>
      <td className="py-2.5 pr-4 text-[11.5px] text-ink-soft font-mono">{entry.dataset_safe_name}</td>
      <td className="py-2.5 pr-4">
        <span
          className={`text-[10px] font-bold px-2 py-0.5 rounded-full ${
            entry.confidence_tier === "high_confidence" ? "bg-success-soft text-success" : "bg-[#FFF8E8] text-[#B8952E]"
          }`}
        >
          {entry.confidence_tier === "high_confidence" ? "High confidence" : "Needs review"}
        </span>
      </td>
      <td className="py-2.5 pr-4">
        <StatusPill status={entry.status} />
      </td>
      <td className="py-2.5 pr-4 text-[11.5px] text-ink-faint">{entry.decided_by}</td>
      <td className="py-2.5 text-[11.5px] text-ink-faint font-mono">{entry.decided_at?.slice(0, 16).replace("T", " ")}</td>
    </tr>
  );
}

export default function AuditLog() {
  const [state, setState] = useState({ loading: true, entries: [], error: null });

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const res = await api.getAuditLog();
        if (!cancelled) setState({ loading: false, entries: res.entries ?? [], error: null });
      } catch (e) {
        if (!cancelled) setState({ loading: false, entries: [], error: e.message });
      }
    }
    load();
    const interval = setInterval(load, 15000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, []);

  return (
    <div className="p-7 md:px-8">
      <div className="mb-5">
        <h1 className="text-xl font-semibold text-ink tracking-tight">Audit Log</h1>
        <p className="text-[13px] text-ink-faint mt-1">
          Every duplicate-review decision — who decided what, and when. The durable compliance record, independent of any one chat session.
        </p>
      </div>

      {state.error && (
        <div className="mb-4 text-[12.5px] text-danger bg-danger-soft border border-danger/20 rounded-xl px-4 py-3">
          Couldn't reach the API: {state.error}
        </div>
      )}

      <div className="bg-white border border-line rounded-card px-6 py-5">
        {state.entries.length === 0 && !state.loading ? (
          <div className="text-[12.5px] text-ink-faint py-4 text-center">
            No decisions recorded yet — Confirm or Reject a cluster in the Duplicate Queue to see it here.
          </div>
        ) : (
          <table className="w-full">
            <thead>
              <tr className="border-b border-line text-left">
                <th className="pb-2 pr-4 text-[10.5px] font-bold text-ink-faint uppercase tracking-wide">Person</th>
                <th className="pb-2 pr-4 text-[10.5px] font-bold text-ink-faint uppercase tracking-wide">Cluster</th>
                <th className="pb-2 pr-4 text-[10.5px] font-bold text-ink-faint uppercase tracking-wide">Dataset</th>
                <th className="pb-2 pr-4 text-[10.5px] font-bold text-ink-faint uppercase tracking-wide">Confidence</th>
                <th className="pb-2 pr-4 text-[10.5px] font-bold text-ink-faint uppercase tracking-wide">Decision</th>
                <th className="pb-2 pr-4 text-[10.5px] font-bold text-ink-faint uppercase tracking-wide">Decided by</th>
                <th className="pb-2 text-[10.5px] font-bold text-ink-faint uppercase tracking-wide">When</th>
              </tr>
            </thead>
            <tbody>
              {state.entries.map((e) => (
                <AuditRow key={e.id} entry={e} />
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="mt-4 text-[11.5px] text-ink-faint">
        {state.loading ? "Loading live data…" : "Live data — refreshes every 15s."}
      </div>
    </div>
  );
}
