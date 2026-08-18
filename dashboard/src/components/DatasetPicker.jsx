import { useEffect, useRef, useState } from "react";

// Shared, always-visible dataset selector -- used anywhere a page's
// data is scoped to one dataset (SAMA Compliance, Golden Record
// Registry, Audit Log). Deliberately never auto-picks "show
// everything" when more than one dataset exists: the caller decides
// what "no selection yet" means (usually "show a prompt, fetch
// nothing"), this component only renders the control and reports the
// choice.
//
// allOption: optional {value, label, sublabel} for pages where a
// cross-dataset view is a real, useful thing to explicitly choose
// (e.g. Audit Log's compliance-wide history) -- rendered as a pinned
// first row above the per-dataset list, visually distinct so it never
// reads as "the default." Omit it entirely for pages where "all"
// isn't a meaningful state (SAMA, Golden Records).
export default function DatasetPicker({ datasets, value, onChange, placeholder = "Select dataset", allOption = null }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);

  useEffect(() => {
    function onClickOutside(e) {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false);
    }
    document.addEventListener("mousedown", onClickOutside);
    return () => document.removeEventListener("mousedown", onClickOutside);
  }, []);

  const selected = allOption && value === allOption.value ? allOption : datasets.find((d) => d.dataset_name === value);
  const isAll = allOption && value === allOption.value;

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex items-center gap-2 text-[12.5px] font-medium bg-white border border-line rounded-xl pl-3 pr-2.5 py-2 min-w-[220px] hover:border-ink-faint transition-colors"
      >
        <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${isAll ? "bg-gold" : selected ? "bg-teal" : "bg-[#D6D6DA]"}`} />
        <span className={`flex-1 text-left truncate ${selected ? "text-ink" : "text-ink-faint"}`}>
          {selected ? selected.display_name ?? selected.dataset_name : placeholder}
        </span>
        {selected && !isAll && <span className="text-[10.5px] text-ink-faint font-mono shrink-0">{selected.rows} rows</span>}
        <svg
          width="14"
          height="14"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          className={`text-ink-faint shrink-0 transition-transform ${open ? "rotate-180" : ""}`}
        >
          <path d="M6 9l6 6 6-6" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </button>

      {open && (
        <div className="absolute right-0 mt-1.5 w-80 bg-white border border-line rounded-xl shadow-[0_4px_20px_-4px_rgba(0,0,0,0.14)] py-1.5 z-20 max-h-96 overflow-auto">
          {allOption && (
            <>
              <button
                onClick={() => {
                  onChange(allOption.value);
                  setOpen(false);
                }}
                className={`w-full flex items-center justify-between gap-3 px-3.5 py-2 text-left hover:bg-[#FAFAFB] transition-colors ${
                  isAll ? "bg-[#F2F2F4]" : ""
                }`}
              >
                <div className="flex items-center gap-2 min-w-0">
                  <span className="w-1.5 h-1.5 rounded-full bg-gold shrink-0" />
                  <div className="min-w-0">
                    <div className="text-[12.5px] font-medium text-ink truncate">{allOption.label}</div>
                    {allOption.sublabel && <div className="text-[10.5px] text-ink-faint truncate">{allOption.sublabel}</div>}
                  </div>
                </div>
                {isAll && (
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" className="text-teal shrink-0">
                    <path d="M20 6L9 17l-5-5" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                )}
              </button>
              <div className="h-px bg-line my-1.5" />
            </>
          )}
          {datasets.length === 0 && <div className="px-3.5 py-3 text-[12px] text-ink-faint">No datasets yet</div>}
          {datasets.map((d) => (
            <button
              key={d.dataset_name}
              onClick={() => {
                onChange(d.dataset_name);
                setOpen(false);
              }}
              className={`w-full flex items-center justify-between gap-3 px-3.5 py-2 text-left hover:bg-[#FAFAFB] transition-colors ${
                d.dataset_name === value ? "bg-[#F2F2F4]" : ""
              }`}
            >
              <div className="min-w-0">
                <div className="text-[12.5px] font-medium text-ink truncate">{d.display_name ?? d.dataset_name}</div>
                <div className="text-[10.5px] text-ink-faint font-mono truncate">
                  {d.dataset_name} · {d.rows} rows
                </div>
              </div>
              {d.dataset_name === value && (
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" className="text-teal shrink-0">
                  <path d="M20 6L9 17l-5-5" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
              )}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
