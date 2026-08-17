import { NavLink } from "react-router-dom";

const ICONS = {
  chat: <path d="M4 4.5h16a1 1 0 011 1v10a1 1 0 01-1 1H9l-4 3.5v-3.5H4a1 1 0 01-1-1v-10a1 1 0 011-1z" />,
  lakehouse: <path d="M12 3 2 8l10 5 10-5-10-5zM2 12l10 5 10-5M2 16l10 5 10-5" />,
  mdm: <path d="M12 3a4 4 0 100 8 4 4 0 000-8zM4 20a8 8 0 0116 0" />,
  ndi: <path d="M12 2v20M2 12h20M6 6l12 12M18 6L6 18" />,
  governance: <path d="M12 2 3 6v6c0 5 4 8.5 9 10 5-1.5 9-5 9-10V6l-9-4z" />,
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
];

export default function Sidebar() {
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
    </div>
  );
}
