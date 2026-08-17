export default function FlowConnector({ label }) {
  return (
    <div className="flex flex-col items-center px-1.5 min-w-[56px]">
      <div className="relative w-11 h-0.5 bg-line rounded">
        <div
          className="flow-pulse absolute top-1/2 -translate-y-1/2 left-0 w-1.5 h-1.5 rounded-full bg-teal shadow-[0_0_6px_1px_rgba(15,122,107,0.5)]"
          style={{ offsetPath: "path('M0,0 L44,0')" }}
        />
        <div
          className="absolute -right-px -top-[3px] w-0 h-0 border-t-4 border-b-4 border-t-transparent border-b-transparent"
          style={{ borderLeft: "6px solid #D4D4D8" }}
        />
      </div>
      {label && <span className="text-[10px] text-ink-faint mt-1.5 whitespace-nowrap font-medium">{label}</span>}
    </div>
  );
}
