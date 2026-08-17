import { Routes, Route, Navigate, Link, useLocation } from "react-router-dom";
import Sidebar from "./components/Sidebar";
import LakehouseZones from "./pages/LakehouseZones";
import PipelineMonitoring from "./pages/PipelineMonitoring";
import GoldenRecordRegistry from "./pages/GoldenRecordRegistry";
import DuplicateQueue from "./pages/DuplicateQueue";
import ComingSoon from "./pages/ComingSoon";
import SamaDashboard from "./pages/SamaDashboard";
import AuditLog from "./pages/AuditLog";

const TAB_GROUPS = {
  lakehouse: [
    { key: "zones", label: "Zones", path: "/lakehouse" },
    { key: "pipeline", label: "Pipeline Monitoring", path: "/lakehouse/pipeline" },
  ],
  mdm: [
    { key: "golden", label: "Golden Records", path: "/mdm" },
    { key: "queue", label: "Duplicate Queue", path: "/mdm/queue" },
  ],
  governance: [
    { key: "sama", label: "SAMA Compliance", path: "/governance" },
    { key: "audit", label: "Audit Log", path: "/governance/audit-log" },
  ],
};

function TopTabs({ group }) {
  const location = useLocation();
  const tabs = TAB_GROUPS[group];
  if (!tabs) return <div className="h-[52px] border-b border-line bg-white" />;
  return (
    <div className="h-[52px] border-b border-line flex items-center justify-between px-6 bg-white">
      <div className="flex gap-1">
        {tabs.map((t) => (
          <Link
            key={t.key}
            to={t.path}
            className={`text-[12.5px] font-semibold px-3 py-1.5 rounded-[7px] ${
              location.pathname === t.path ? "bg-[#F2F2F4] text-ink" : "text-ink-faint hover:text-ink-soft"
            }`}
          >
            {t.label}
          </Link>
        ))}
      </div>
      <div className="text-[11.5px] text-ink-faint font-mono">DataOS Banking Demo · Postgres</div>
    </div>
  );
}

function LakehouseSection() {
  return (
    <>
      <TopTabs group="lakehouse" />
      <div className="flex-1 overflow-auto">
        <Routes>
          <Route path="/" element={<LakehouseZones />} />
          <Route path="/pipeline" element={<PipelineMonitoring />} />
        </Routes>
      </div>
    </>
  );
}

function MdmSection() {
  return (
    <>
      <TopTabs group="mdm" />
      <div className="flex-1 overflow-auto">
        <Routes>
          <Route path="/" element={<GoldenRecordRegistry />} />
          <Route path="/queue" element={<DuplicateQueue />} />
        </Routes>
      </div>
    </>
  );
}

function GovernanceSection() {
  return (
    <>
      <TopTabs group="governance" />
      <div className="flex-1 overflow-auto">
        <Routes>
          <Route path="/" element={<SamaDashboard />} />
          <Route path="/audit-log" element={<AuditLog />} />
        </Routes>
      </div>
    </>
  );
}

export default function App() {
  return (
    <div className="flex h-screen bg-canvas font-sans text-ink">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <Routes>
          <Route path="/" element={<Navigate to="/lakehouse" replace />} />
          <Route path="/lakehouse/*" element={<LakehouseSection />} />
          <Route path="/mdm/*" element={<MdmSection />} />
          <Route
            path="/ndi"
            element={
              <>
                <TopTabs group={null} />
                <div className="flex-1 overflow-auto">
                  <ComingSoon title="NDI" />
                </div>
              </>
            }
          />
          <Route path="/governance/*" element={<GovernanceSection />} />
        </Routes>
      </div>
    </div>
  );
}
