import { useState } from "react";
import { api } from "../api";

// Manual, per-dataset trigger for the Lakehouse (Silver/Gold Iceberg)
// pipeline -- deliberately the ONLY way that pipeline ever runs. No
// auto-trigger on upload anywhere in this codebase: per Khurram's
// explicit instruction, that would add load and page-load delay to
// every single upload for a stage most uploads don't need run
// immediately. This is always a real, deliberate click.
export default function TriggerPipelineButton({ datasetName, onTriggered }) {
  const [state, setState] = useState({ busy: false, result: null, error: null });

  async function handleTrigger() {
    setState({ busy: true, result: null, error: null });
    try {
      const res = await api.triggerPipeline(datasetName);
      setState({ busy: false, result: res, error: null });
      onTriggered?.(res);
    } catch (e) {
      setState({ busy: false, result: null, error: e.message });
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
      {state.result && (
        <div className="text-[10.5px] text-success text-right max-w-[220px]">Triggered — run {state.result.run_id}</div>
      )}
      {state.error && <div className="text-[10.5px] text-danger text-right max-w-[220px]">{state.error}</div>}
    </div>
  );
}
