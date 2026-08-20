import { useEffect, useState } from "react";
import { NavLink } from "react-router-dom";
import { useAuth } from "../auth";
import { api } from "../api";

const ICONS = {
  chat: <path d="M4 4.5h16a1 1 0 011 1v10a1 1 0 01-1 1H9l-4 3.5v-3.5H4a1 1 0 01-1-1v-10a1 1 0 011-1z" />,
  lakehouse: <path d="M12 3 2 8l10 5 10-5-10-5zM2 12l10 5 10-5M2 16l10 5 10-5" />,
  mdm: <path d="M12 3a4 4 0 100 8 4 4 0 000-8zM4 20a8 8 0 0116 0" />,
  ndi: <path d="M12 2v20M2 12h20M6 6l12 12M18 6L6 18" />,
  governance: <path d="M12 2 3 6v6c0 5 4 8.5 9 10 5-1.5 9-5 9-10V6l-9-4z" />,
  catalog: <path d="M4 19.5A2.5 2.5 0 016.5 17H20M4 19.5A2.5 2.5 0 006.5 22H20V4H6.5A2.5 2.5 0 004 6.5v13z" />,
  account: <path d="M12 12a4 4 0 100-8 4 4 0 000 8zM4 20a8 8 0 0116 0" />,
};

function Icon({ name }) {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
      {ICONS[name]}
    </svg>
  );
}

const NAV_ITEMS = [
  { to: "/chat", label: "Chat", icon: "chat", external: "/app" },
  { to: "/lakehouse", label: "Lakehouse", icon: "lakehouse" },
  { to: "/mdm", label: "MDM", icon: "mdm" },
  { to: "/ndi", label: "NDI", icon: "ndi" },
  { to: "/governance", label: "Governance", icon: "governance" },
  { to: "/catalog", label: "Catalog", icon: "catalog" },
];

// Sidebar mini-KPIs (2026-08-20) -- Item B (UI polish), part 2 of the
// remaining 4. Sourced from a reference platform's own sidebar mini-KPI
// cards; rebuilt on DataOS's real endpoints. NDI's display_score comes
// straight from api.getNdi() -- the exact number the NDI Assessment
// page itself shows. The SAMA average is computed here the identical
// way sama_history.py's own average_measured_score is: the mean of
// whichever domains are currently status="measured" -- never all 8,
// since 4 have no real signal in DataOS today (see
// banking_adapter.compute_sama_compliance()'s docstring). Not the
// same number as a recorded snapshot's average (this is the LIVE
// value, snapshots are point-in-time), but the same honest formula.
//
// Fails silently on either fetch -- this is a supplementary glance,
// not the primary view of either number; the NDI/SAMA pages
// themselves already surface real errors/empty states.
function MiniKpis() {
  const [ndi, setNdi] = useState(null);
  const [sama, setSama] = useState(null);

  useEffect(() => {
    let cancelled = false;
    api.getNdi().then((res) => { if (!cancelled) setNdi(res); }).catch(() => {});
    api.getSama().then((res) => { if (!cancelled) setSama(res); }).catch(() => {});
    return () => { cancelled = true; };
  }, []);

  const measured = sama?.domain_scores?.filter((d) => d.status === "measured") ?? [];
  const samaAvg = measured.length ? Math.round((measured.reduce((s, d) => s + d.score, 0) / measured.length) * 10) / 10 : null;

  if (!ndi && samaAvg === null) return null;

  return (
    <div className="flex flex-col gap-1.5 border-t border-line mt-2 pt-2.5 mb-1">
      {ndi && (
        <NavLink to="/ndi" className="block px-2.5 py-1.5 rounded-lg hover:bg-[#F2F2F4]">
          <div className="text-[9px] font-bold text-ink-faint uppercase tracking-wide">NDI Score</div>
          <div className="flex items-baseline gap-1.5 mt-0.5">
            <span className="text-[15px] font-bold font-mono text-teal">{ndi.display_score}</span>
            <span className="text-[9.5px] text-ink-faint truncate">{ndi.maturity_level}</span>
          </div>
          <div className="h-[3px] rounded-full bg-[#F0F0F2] mt-1 overflow-hidden">
            <div className="h-full rounded-full bg-teal" style={{ width: `${Math.max(0, Math.min(100, ndi.display_score))}%` }} />
          </div>
        </NavLink>
      )}
      {samaAvg !== null && (
        <NavLink to="/governance" className="block px-2.5 py-1.5 rounded-lg hover:bg-[#F2F2F4]">
          <div className="text-[9px] font-bold text-ink-faint uppercase tracking-wide">SAMA (measured)</div>
          <div className="flex items-baseline gap-1.5 mt-0.5">
            <span className="text-[15px] font-bold font-mono text-success">{samaAvg}%</span>
            <span className="text-[9.5px] text-ink-faint">{measured.length}/{sama.domain_scores.length} domains</span>
          </div>
          <div className="h-[3px] rounded-full bg-[#F0F0F2] mt-1 overflow-hidden">
            <div className="h-full rounded-full bg-success" style={{ width: `${Math.max(0, Math.min(100, samaAvg))}%` }} />
          </div>
        </NavLink>
      )}
    </div>
  );
}

export default function Sidebar() {
  const { isAuthenticated, name } = useAuth();

  return (
    <div className="w-[200px] shrink-0 bg-[#FBFBFC] border-r border-line p-3 flex flex-col gap-0.5">
      <div className="flex items-center gap-2 px-2 mb-5">
        <div className="w-6 h-6 rounded-[7px] bg-gradient-to-br from-teal to-[#0A5C50]" />
        <span className="text-sm font-bold tracking-tight">DataOS</span>
      </div>
      {NAV_ITEMS.map((item) =>
        item.external ? (
          <a
            key={item.to}
            href={item.external}
            className="flex items-center gap-2.5 px-2.5 py-2 rounded-lg text-[13px] font-medium text-ink-soft hover:bg-[#F2F2F4]"
          >
            <Icon name={item.icon} />
            {item.label}
          </a>
        ) : (
          <NavLink
            key={item.to}
            to={item.to}
            className={({ isActive }) =>
              `flex items-center gap-2.5 px-2.5 py-2 rounded-lg text-[13px] font-medium ${
                isActive ? "bg-teal-soft text-teal" : "text-ink-soft hover:bg-[#F2F2F4]"
              }`
            }
          >
            <Icon name={item.icon} />
            {item.label}
          </NavLink>
        )
      )}

      <MiniKpis />

      <div className="flex-1" />

      {/* Real auth status, wired 2026-08-19 -- see auth.jsx's module
          docstring. This is the only place a signed-out visitor sees
          any mention of login; nothing else in the dashboard requires
          it, matching every existing page's own "deliberately
          unauthenticated" design. */}
      <NavLink
        to="/account"
        className={({ isActive }) =>
          `flex items-center gap-2.5 px-2.5 py-2 rounded-lg text-[13px] font-medium ${
            isActive ? "bg-teal-soft text-teal" : "text-ink-soft hover:bg-[#F2F2F4]"
          }`
        }
      >
        <Icon name="account" />
        {isAuthenticated ? name || "Account" : "Sign in"}
      </NavLink>
    </div>
  );
}
