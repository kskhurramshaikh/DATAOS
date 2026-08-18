import { useEffect, useState } from "react";
import { api } from "../api";

const STEP_ORDER = ["verify_silver_ready", "silver_to_iceberg", "gold_compute"];
const STEP_LABELS = { verify_silver_ready: "Verify Silver", silver_to_iceberg: "Silver → Iceberg", gold_compute: "Compute Gold" };

const STATE_META = {
  success: { color: "bg-success", text: "text-success" },
  running: { color: "bg-running", text: "text-running" },
  failed: { color: "bg-danger", text: "text-danger" },
  up_for_retry: { color: "bg-[#C99A2E]", text: "text-[#C99A2E]" },
  queued: { color: "bg-[#D6D6DA]", text: "text-ink-faint" },
};

function StepDot({ taskId, task }) {
  const state = task?.state || "queued";
  const meta = STATE_META[state] || STATE_META.queued;
  return (
    <div className="flex flex-col items-center gap-1 min-w-[92px]">
      <span className={`w-2.5 h-2.5 rounded-full ${meta.color}`} />
      <span className="text-[10.5px] text-ink-soft text-center leading-tight">{STEP_LABELS[taskId]}</span>
      <span className={`text-[9.5px] font-semibold ${meta.text}`}>{state}</span>
    </div>
  );
}

// Real-time run status -- a clearer, always-visible version of what
// Airflow's own UI shows for the DAG's 3 tasks, embedded directly on
// the dashboard so nobody has to cross-check Airflow separately.
// Polls fast (4s) while the latest run is genuinely in flight, falls
// back to a slower cadence once it settles, and tells the parent when
// a run finishes so Zones can refresh its Silver/Gold counts right
// when there's actually something new to show -- not on a fixed timer
// that might miss the window.
export default function PipelineRunStatus({ datasetName, onRunSettled }) {
  const [run, setRun] = useState(null);
  const [loading, setLoading] = useState(false);
  const [notifiedRunId, setNotifiedRunId] = useState(null);

  useEffect(() => {
    if (!datasetName) {
      setRun(null);
      return;
    }
    let cancelled = false;
    let timer;

    async function poll() {
      try {
        const res = await api.getPipelineRuns(datasetName, 1);
        if (cancelled) return;
        const latest = res.runs?.[0] || null;
        setRun(latest);
        setLoading(false);

        const settled = latest && (latest.state === "success" || latest.state === "failed");
        if (settled && latest.run_id !== notifiedRunId) {
          setNotifiedRunId(latest.run_id);
          onRunSettled?.();
        }

        const active = latest && !settled;
        timer = setTimeout(poll, active ? 4000 : 15000);
      } catch {
        if (!cancelled) timer = setTimeout(poll, 15000);
      }
    }
    setLoading(true);
    poll();

    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [datasetName]);

  if (!datasetName || (!run && !loading)) return null;

  if (loading && !run) {
    return <div className="text-[11.5px] text-ink-faint">Checking pipeline status…</div>;
  }
  if (!run) return null;

  const overallMeta = run.state === "success" ? STATE_META.success : run.state === "failed" ? STATE_META.failed : STATE_META.running;

  return (
    <div className="bg-white border border-line rounded-2xl px-5 py-3.5 flex items-center justify-between gap-4 flex-wrap">
      <div className="flex items-center gap-4">
        <div className={`inline-flex items-center gap-1.5 text-[11.5px] font-semibold px-2.5 py-1 rounded-full ${overallMeta.text} bg-[#FAFAFB]`}>
          <span className={`w-1.5 h-1.5 rounded-full ${overallMeta.color}`} />
          {run.state === "success" ? "Last run succeeded" : run.state === "failed" ? "Last run failed" : "Running…"}
        </div>
        <span className="text-[10.5px] text-ink-faint font-mono">{run.run_id}</span>
      </div>
      <div className="flex items-center gap-3">
        {STEP_ORDER.map((taskId) => (
          <StepDot key={taskId} taskId={taskId} task={run.tasks?.find((t) => t.task_id === taskId)} />
        ))}
      </div>
    </div>
  );
}
