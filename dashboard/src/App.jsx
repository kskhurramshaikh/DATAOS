import { Routes, Route, Navigate, Link, useLocation } from "react-router-dom";
import Sidebar from "./components/Sidebar";
import LakehouseZones from "./pages/LakehouseZones";
import PipelineMonitoring from "./pages/PipelineMonitoring";
import GoldenRecordRegistry from "./pages/GoldenRecordRegistry";
import DuplicateQueue from "./pages/DuplicateQueue";
import FieldLevelLineage from "./pages/FieldLevelLineage";
import DataStewardship from "./pages/DataStewardship";
import SamaDashboard from "./pages/SamaDashboard";
import AuditLog from "./pages/AuditLog";
import Classification from "./pages/Classification";
import DataQualityRules from "./pages/DataQualityRules";
import NdiDashboard from "./pages/NdiDashboard";
import NdiHistory from "./pages/NdiHistory";
import DataCatalog from "./pages/DataCatalog";
import FieldLineage from "./pages/FieldLineage";
import Account from "./pages/Account";
import ManageUsers from "./pages/ManageUsers";

const TAB_GROUPS = {
  lakehouse: [
    { key: "zones", label: "Zones", path: "/lakehouse" },
    { key: "pipeline", label: "Pipeline Monitoring", path: "/lakehouse/pipeline" },
  ],
  mdm: [
    { key: "golden", label: "Golden Records", path: "/mdm" },
    { key: "queue", label: "Duplicate Queue", path: "/mdm/queue" },
    { key: "lineage", label: "Field Lineage", path: "/mdm/lineage" },
    { key: "stewardship", label: "Data Stewardship", path: "/mdm/stewardship" },
  ],
  ndi: [
    { key: "assessment", label: "Assessment", path: "/ndi" },
    { key: "history", label: "History", path: "/ndi/history" },
  ],
  governance: [
    { key: "sama", label: "SAMA Compliance", path: "/governance" },
    { key: "audit", label: "Audit Log", path: "/governance/audit-log" },
    { key: "classification", label: "Classification & PDPL", path: "/governance/classification" },
    { key: "quality", label: "Data Quality Rules", path: "/governance/quality-rules" },
  ],
  catalog: [
    { key: "catalog", label: "Data Catalog", path: "/catalog" },
    { key: "lineage", label: "Field Lineage", path: "/catalog/lineage" },
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
          <Route path="/lineage" element={<FieldLevelLineage />} />
          <Route path="/stewardship" element={<DataStewardship />} />
        </Routes>
      </div>
    </>
  );
}

function NdiSection() {
  return (
    <>
      <TopTabs group="ndi" />
      <div className="flex-1 overflow-auto">
        <Routes>
          <Route path="/" element={<NdiDashboard />} />
          <Route path="/history" element={<NdiHistory />} />
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
          <Route path="/classification" element={<Classification />} />
          <Route path="/quality-rules" element={<DataQualityRules />} />
        </Routes>
      </div>
    </>
  );
}

function CatalogSection() {
  return (
    <>
      <TopTabs group="catalog" />
      <div className="flex-1 overflow-auto">
        <Routes>
          <Route path="/" element={<DataCatalog />} />
          <Route path="/lineage" element={<FieldLineage />} />
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
          <Route path="/ndi/*" element={<NdiSection />} />
          <Route path="/governance/*" element={<GovernanceSection />} />
          <Route path="/catalog/*" element={<CatalogSection />} />
          <Route path="/account" element={<Account />} />
          <Route path="/admin/users" element={<ManageUsers />} />
        </Routes>
      </div>
    </div>
  );
}
