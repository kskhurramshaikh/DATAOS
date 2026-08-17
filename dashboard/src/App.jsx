import { Routes, Route, Navigate, Link, useLocation } from "react-router-dom";
import Sidebar from "./components/Sidebar";
import LakehouseZones from "./pages/LakehouseZones";
import PipelineMonitoring from "./pages/PipelineMonitoring";
import ComingSoon from "./pages/ComingSoon";

const TABS = [
  { key: "zones", label: "Zones", path: "/lakehouse" },
  { key: "pipeline", label: "Pipeline Monitoring", path: "/lakehouse/pipeline" },
];

function LakehouseTabs() {
  const location = useLocation();
  return (
    <div className="h-[52px] border-b border-line flex items-center justify-between px-6 bg-white">
      <div className="flex gap-1">
        {TABS.map((t) => (
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
      <div className="text-[11.5px] text-ink-faint font-mono">DataOS 3.0</div>
    </div>
  );
}

export default function App() {
  return (
    <div className="flex h-screen bg-canvas font-sans text-ink">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <LakehouseTabs />
        <div className="flex-1 overflow-auto">
          <Routes>
            <Route path="/" element={<Navigate to="/lakehouse" replace />} />
            <Route path="/lakehouse" element={<LakehouseZones />} />
            <Route path="/lakehouse/pipeline" element={<PipelineMonitoring />} />
            <Route path="/mdm" element={<ComingSoon title="MDM" />} />
            <Route path="/ndi" element={<ComingSoon title="NDI" />} />
            <Route path="/governance" element={<ComingSoon title="Governance" />} />
          </Routes>
        </div>
      </div>
    </div>
  );
}
