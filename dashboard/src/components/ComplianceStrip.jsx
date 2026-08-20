import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";

// Pinned SAMA compliance strip (2026-08-20) -- Item B (UI polish),
// part 1 of the remaining 4. Sourced from a reference platform's own
// pinned SAMA status bar under its topbar; rebuilt here on real data
// only. See banking_adapter.compute_sama_compliance()'s own docstring
// for why some domains show "not measured" instead of a fabricated
// score -- DIS/DC/BIA/DS have no corresponding real signal in DataOS
// today, and this strip stays honest about that rather than hiding
// or inventing them, matching the SAMA Dashboard page's own standard.
//
// Renders across every page in the Governance section (see App.jsx's
// GovernanceSection) -- SAMA compliance is the one cross-cutting
// signal relevant regardless of which Governance sub-page is open.
// Fails silently (renders nothing) on error or with no dataset yet --
// this is a supplementary strip, not the primary view; SamaDashboard
// itself already surfaces the real error/empty state.
export default function ComplianceStrip() {
  const [state, setState] = useState({ loading: true, data: null });

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await api.getSama();
        if (!cancelled) setState({ loading: false, data: res });
      } catch {
        if (!cancelled) setState({ loading: false, data: null });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const d = state.data;
  if (state.loading || !d || !d.domain_scores) return null;

  const hasPendingAction = d.priority_alert && !d.priority_alert.startsWith("No measured domains");

  return (
    <div className="h-9 border-b border-line bg-[#FAFAFB] flex items-center gap-4 px-6 overflow-x-auto shrink-0">
      <span className="text-[9.5px] font-bold text-ink-faint uppercase tracking-wide shrink-0">SAMA</span>
      {d.domain_scores.map((domain) => {
        const measured = domain.status === "measured";
        const ok = measured && domain.score >= 70;
        const dotColor = !measured ? "bg-[#D6D6DA]" : ok ? "bg-success" : "bg-[#B8952E]";
        return (
          <span key={domain.code} className="flex items-center gap-1.5 text-[11px] text-ink-soft shrink-0 whitespace-nowrap">
            <span className={`w-1.5 h-1.5 rounded-full ${dotColor}`} />
            {domain.code}: {measured ? `${domain.score}%` : "not measured"}
          </span>
        );
      })}
      {hasPendingAction && d.dataset_name && (
        <Link
          to={`/mdm/queue?dataset=${encodeURIComponent(d.dataset_name)}`}
          className="text-[11px] font-semibold text-teal hover:underline shrink-0 whitespace-nowrap ml-auto"
        >
          {d.priority_alert} →
        </Link>
      )}
    </div>
  );
}
