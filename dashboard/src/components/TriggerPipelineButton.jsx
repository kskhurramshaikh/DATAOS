import { useState } from "react";
import { api } from "../api";

// Manual, per-dataset trigger for the Lakehouse (Silver/Gold Iceberg)
// pipeline -- deliberately the ONLY way that pipeline ever runs. No
// auto-trigger on upload anywhere in this codebase: per Khurram's
// explicit instruction, that would add load and page-load delay to
// every single upload for a stage most uploads don't need run
// immediately. This is always a real, deliberate click.
//
// Only reports whether the TRIGGER CALL itself succeeded or failed
// (a network/auth/config problem) -- ongoing run progress is shown by
// the separate, richer PipelineRunStatus component, not duplicated
// here as a static caption that goes stale the moment the run
// actually starts doing work.
export default function TriggerPipelineButton({ datasetName, onTriggered }) {
  const [state, setState] = useState({ busy: false, error: null });

  async function handleTrigger() {
    setState({ busy: true, error: null });
    try {
      const res = await api.triggerPipeline(datasetName);
      setState({ busy: false, error: null });
      onTriggered?.(res);
    } catch (e) {
      setState({ busy: false, error: e.message });
    }
  }

  return (
    <div className="flex flex-col items-end gap-1.5">
      <button
        disabled={!datasetName || state.busy}
        onClick={handleTrigger}
        className="text-[12px] font-semibold px-3.5 py-2 rounded-xl bg-teal text-white disabled:opacity-40 flex items-center gap-1.5"
      >
        {state.busy ? (
          "Starting…"
        ) : (
          <>
            <svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor">
              <path d="M8 5v14l11-7z" />
            </svg>
            Run pipeline
          </>
        )}
      </button>
      {state.error && <div className="text-[10.5px] text-danger text-right max-w-[260px]">{state.error}</div>}
    </div>
  );
}
