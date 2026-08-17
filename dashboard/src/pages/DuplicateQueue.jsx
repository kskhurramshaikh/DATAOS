import { useEffect, useState } from "react";
import { api } from "../api";

function Pill({ tier }) {
  const isHigh = tier === "high_confidence";
  return (
    <span
      className={`text-[10px] font-bold px-2 py-0.5 rounded-full ${
        isHigh ? "bg-success-soft text-success" : "bg-[#FFF8E8] text-[#B8952E]"
      }`}
    >
      {isHigh ? "High confidence" : "Needs review"}
    </span>
  );
}

function PendingCard({ cluster, onDecide, deciding }) {
  return (
    <div className="bg-white border border-line rounded-xl px-4 py-3.5">
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <span className="text-[12.5px] font-semibold text-ink">{cluster.members.length} records</span>
          <Pill tier={cluster.confidence_tier} />
        </div>
        <span className="text-[10.5px] text-ink-faint font-mono">cluster #{cluster.id}</span>
      </div>
      <div className="flex flex-wrap gap-2 mb-3">
        {cluster.members.map((m) => (
          <div key={m.row_id} className="bg-[#FAFAFB] border border-line rounded-lg px-2.5 py-1.5 text-[11.5px] text-ink-soft">
            <span className="font-semibold text-ink">{m.name}</span> · DOB {m.dob}
            {m.phone && <span className="text-ink-faint"> · {m.phone}</span>}
          </div>
        ))}
      </div>
      <div className="flex gap-2">
        <button
          disabled={deciding}
          onClick={() => onDecide(cluster.id, "confirmed_duplicate")}
          className="text-[12px] font-semibold px-3 py-1.5 rounded-lg bg-teal text-white disabled:opacity-50"
        >
          Confirm — same person
        </button>
        <button
          disabled={deciding}
          onClick={() => onDecide(cluster.id, "not_duplicate")}
          className="text-[12px] font-semibold px-3 py-1.5 rounded-lg border border-line text-ink-soft disabled:opacity-50"
        >
          Reject — different people
        </button>
      </div>
    </div>
  );
}

function DecidedRow({ entry }) {
  const confirmed = entry.status === "confirmed_duplicate";
  return (
    <tr className="border-b border-line last:border-0">
      <td className="py-2 pr-4 text-[12px] text-ink">{entry.members?.[0]?.name ?? "—"}</td>
      <td className="py-2 pr-4 text-[11.5px] text-ink-faint">{entry.members?.length ?? 0} records</td>
      <td className="py-2 pr-4">
        <span className={`text-[10.5px] font-semibold px-2 py-0.5 rounded-full ${confirmed ? "bg-success-soft text-success" : "bg-[#F4F4F5] text-ink-faint"}`}>
          {confirmed ? "Merged" : "Rejected"}
        </span>
      </td>
      <td className="py-2 pr-4 text-[11.5px] text-ink-faint">{entry.decided_by}</td>
      <td className="py-2 text-[11.5px] text-ink-faint font-mono">{entry.decided_at?.slice(0, 16).replace("T", " ")}</td>
    </tr>
  );
}

export default function DuplicateQueue() {
  const [state, setState] = useState({ loading: true, pending: [], decided: [], error: null });
  const [decidingId, setDecidingId] = useState(null);
  const [reviewerName, setReviewerName] = useState("");

  async function load() {
    try {
      const res = await api.getDuplicateQueue();
      setState({ loading: false, pending: res.pending, decided: res.decided, error: null });
    } catch (e) {
      setState((s) => ({ ...s, loading: false, error: e.message }));
    }
  }

  useEffect(() => {
    load();
  }, []);

  async function handleDecide(clusterId, status) {
    const name = reviewerName.trim() || "dashboard reviewer";
    setDecidingId(clusterId);
    try {
      await api.decideCluster(clusterId, status, name);
      await load();
    } catch (e) {
      setState((s) => ({ ...s, error: e.message }));
    } finally {
      setDecidingId(null);
    }
  }

  return (
    <div className="p-7 md:px-8">
      <div className="mb-5 flex items-end justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-xl font-semibold text-ink tracking-tight">Duplicate Resolution Queue</h1>
          <p className="text-[13px] text-ink-faint mt-1">
            Confirming a cluster merges it immediately — this isn't a two-step process.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <label className="text-[11.5px] text-ink-faint">Reviewing as</label>
          <input
            value={reviewerName}
            onChange={(e) => setReviewerName(e.target.value)}
            placeholder="your name"
            className="text-[12px] border border-line rounded-lg px-2.5 py-1.5 w-36"
          />
        </div>
      </div>

      {state.error && (
        <div className="mb-4 text-[12.5px] text-danger bg-danger-soft border border-danger/20 rounded-xl px-4 py-3">
          {state.error}
        </div>
      )}

      <div className="mb-3 text-[12.5px] font-semibold text-ink-soft">
        Pending ({state.pending.length})
      </div>
      {state.loading && <div className="text-[12.5px] text-ink-faint">Loading…</div>}
      {!state.loading && state.pending.length === 0 && (
        <div className="text-[12.5px] text-ink-faint bg-[#FAFAFB] border border-line rounded-xl px-4 py-3 mb-6">
          Nothing pending — every detected cluster has a decision.
        </div>
      )}
      <div className="flex flex-col gap-3 mb-8">
        {state.pending.map((c) => (
          <PendingCard key={c.id} cluster={c} onDecide={handleDecide} deciding={decidingId === c.id} />
        ))}
      </div>

      <div className="mb-3 text-[12.5px] font-semibold text-ink-soft">
        Decided ({state.decided.length}) — permanent audit record
      </div>
      {state.decided.length > 0 && (
        <div className="bg-white border border-line rounded-card overflow-hidden">
          <table className="w-full">
            <thead>
              <tr className="border-b border-line bg-[#FAFAFB]">
                <th className="text-left py-2 px-4 text-[10.5px] font-bold text-ink-faint uppercase tracking-wide">Lead name</th>
                <th className="text-left py-2 px-4 text-[10.5px] font-bold text-ink-faint uppercase tracking-wide">Size</th>
                <th className="text-left py-2 px-4 text-[10.5px] font-bold text-ink-faint uppercase tracking-wide">Outcome</th>
                <th className="text-left py-2 px-4 text-[10.5px] font-bold text-ink-faint uppercase tracking-wide">Decided by</th>
                <th className="text-left py-2 px-4 text-[10.5px] font-bold text-ink-faint uppercase tracking-wide">When</th>
              </tr>
            </thead>
            <tbody className="px-4">
              {state.decided.map((e) => (
                <DecidedRow key={e.id} entry={e} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
