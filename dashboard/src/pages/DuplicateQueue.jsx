import { useEffect, useRef, useState } from "react";
import { api } from "../api";

function Pill({ tier }) {
  const isHigh = tier === "high_confidence";
  return (
    <span
      className={`text-[10px] font-bold px-2 py-0.5 rounded-full ${
        isHigh ? "bg-success-soft text-success" : "bg-[#FFF8E8] text-[#B8952E]"
      }`}
    >
      {isHigh ? "High confidence" : "Needs review"}
    </span>
  );
}

function PendingCard({ cluster, onDecide, deciding, actionsDisabled }) {
  return (
    <div className="bg-white border border-line rounded-xl px-4 py-3.5">
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <span className="text-[12.5px] font-semibold text-ink">{cluster.members.length} records</span>
          <Pill tier={cluster.confidence_tier} />
        </div>
        <span className="text-[10.5px] text-ink-faint font-mono">cluster #{cluster.id}</span>
      </div>
      <div className="flex flex-wrap gap-2 mb-3">
        {cluster.members.map((m) => (
          <div key={m.row_id} className="bg-[#FAFAFB] border border-line rounded-lg px-2.5 py-1.5 text-[11.5px] text-ink-soft">
            <span className="font-semibold text-ink">{m.name}</span> · DOB {m.dob}
            {m.phone && <span className="text-ink-faint"> · {m.phone}</span>}
          </div>
        ))}
      </div>
      <div className="flex gap-2">
        <button
          disabled={deciding || actionsDisabled}
          onClick={() => onDecide(cluster.id, "confirmed_duplicate")}
          className="text-[12px] font-semibold px-3 py-1.5 rounded-lg bg-teal text-white disabled:opacity-50"
        >
          Confirm — same person
        </button>
        <button
          disabled={deciding || actionsDisabled}
          onClick={() => onDecide(cluster.id, "not_duplicate")}
          className="text-[12px] font-semibold px-3 py-1.5 rounded-lg border border-line text-ink-soft disabled:opacity-50"
        >
          Reject — different people
        </button>
      </div>
    </div>
  );
}

function DecidedRow({ entry }) {
  const confirmed = entry.status === "confirmed_duplicate";
  return (
    <tr className="border-b border-line last:border-0">
      <td className="py-2 pr-4 text-[12px] text-ink">{entry.members?.[0]?.name ?? "—"}</td>
      <td className="py-2 pr-4 text-[11.5px] text-ink-faint">{entry.members?.length ?? 0} records</td>
      <td className="py-2 pr-4">
        <span className={`text-[10.5px] font-semibold px-2 py-0.5 rounded-full ${confirmed ? "bg-success-soft text-success" : "bg-[#F4F4F5] text-ink-faint"}`}>
          {confirmed ? "Merged" : "Rejected"}
        </span>
      </td>
      <td className="py-2 pr-4 text-[11.5px] text-ink-faint">{entry.decided_by}</td>
      <td className="py-2 text-[11.5px] text-ink-faint font-mono">{entry.decided_at?.slice(0, 16).replace("T", " ")}</td>
    </tr>
  );
}

// Own, self-contained entry point onto the same pipeline chat's upload +
// "find duplicates" chip use -- deliberately NOT dependent on anything
// having happened in chat first. A dataset uploaded here shows up in
// chat too (and vice versa), since both write to the same underlying
// dataset_adapter/dedup_adapter layer -- one shared data layer, two
// genuinely independent front doors.
//
// selectedDataset/onSelectedDatasetChange are lifted up to the parent
// (2026-08-17, per Khurram's ask) -- this SAME selection now also
// gates what the Pending section below shows, so the page doesn't load
// every dataset's pending clusters by default. One dataset picker,
// two things it controls (what "Find duplicates" targets, what Pending
// displays), instead of a second, redundant selector.
function GetStartedPanel({ datasets, onDatasetsChanged, onDetected, selectedDataset, onSelectedDatasetChange, busy, setBusy, setError }) {
  const [uploadName, setUploadName] = useState("");
  const fileInputRef = useRef(null);

  async function handleUpload() {
    const file = fileInputRef.current?.files?.[0];
    if (!file || !uploadName.trim()) {
      setError("Pick a file and give the dataset a name first.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const result = await api.uploadDataset(file, uploadName.trim());
      setUploadName("");
      if (fileInputRef.current) fileInputRef.current.value = "";
      await onDatasetsChanged();
      onSelectedDatasetChange(result.dataset_name);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function handleDetect() {
    if (!selectedDataset) {
      setError("Pick a dataset to check for duplicates first.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const result = await api.detectDuplicates(selectedDataset);
      await onDetected(result);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="bg-white border border-line rounded-card px-5 py-4 mb-6">
      <div className="text-[12.5px] font-semibold text-ink mb-3">Get started</div>
      <div className="flex flex-wrap items-end gap-3">
        <div>
          <label className="block text-[10.5px] text-ink-faint mb-1">1. Upload a dataset</label>
          <div className="flex items-center gap-2">
            <input
              value={uploadName}
              onChange={(e) => setUploadName(e.target.value)}
              placeholder="dataset name"
              className="text-[12px] border border-line rounded-lg px-2.5 py-1.5 w-36"
            />
            <input ref={fileInputRef} type="file" accept=".csv,.xlsx,.xls" className="text-[11.5px] w-44" />
            <button
              disabled={busy}
              onClick={handleUpload}
              className="text-[12px] font-semibold px-3 py-1.5 rounded-lg bg-teal text-white disabled:opacity-50"
            >
              Upload
            </button>
          </div>
        </div>

        <div className="h-8 w-px bg-line mx-1" />

        <div>
          <label className="block text-[10.5px] text-ink-faint mb-1">2. Check for duplicates</label>
          <div className="flex items-center gap-2">
            <select
              value={selectedDataset}
              onChange={(e) => onSelectedDatasetChange(e.target.value)}
              className="text-[12px] border border-line rounded-lg px-2.5 py-1.5 w-44"
            >
              <option value="">Select dataset…</option>
              {datasets.map((d) => (
                <option key={d.dataset_name} value={d.dataset_name}>
                  {d.display_name} ({d.rows} rows)
                </option>
              ))}
            </select>
            <button
              disabled={busy}
              onClick={handleDetect}
              className="text-[12px] font-semibold px-3 py-1.5 rounded-lg border border-line text-ink-soft disabled:opacity-50"
            >
              Find duplicates
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

// One row per dataset that currently has pending clusters -- carries
// the two bulk-resolve options chat's own smart recommendations already
// offer ("Confirm all high-confidence matches" / "Confirm all pending"),
// scoped explicitly to this dataset via the dataset_name each pending
// cluster is now tagged with (see /api/mdm/duplicate-queue). Passing
// dataset_name explicitly here -- rather than leaving it null and
// relying on the backend's single-dataset auto-detect -- means bulk
// actions work correctly even with multiple datasets' clusters mixed
// in the same pending list, instead of erroring out asking "which one."
//
// actionsDisabled (2026-08-18, real bug found in live testing): pending
// clusters here come from Postgres (detected and persisted earlier) --
// completely independent of whether the underlying Silver file the
// clusters were originally detected against still exists right now.
// Confirmed live: a dataset whose Silver file had gone missing still
// showed a fully clickable "Confirm all pending" bar with real merge
// actions available, alongside an unrelated error banner from a failed
// re-detection attempt -- the error and the pending list were never
// connected. Every action button below is now disabled whenever the
// currently selected dataset has an active error, so acting on
// clusters tied to a source the system itself just flagged as broken
// is never one click away.
function DatasetBulkBar({ datasetName, pendingInDataset, onBulkConfirm, busyKey, actionsDisabled }) {
  const highConfidenceCount = pendingInDataset.filter((c) => c.confidence_tier === "high_confidence").length;
  const busy = busyKey === datasetName;

  return (
    <div className="flex items-center justify-between flex-wrap gap-2 bg-[#FAFAFB] border border-line rounded-xl px-4 py-2.5 mb-3">
      <div className="text-[12px] font-semibold text-ink-soft">
        {datasetName} <span className="text-ink-faint font-normal">— {pendingInDataset.length} pending</span>
      </div>
      <div className="flex gap-2">
        <button
          disabled={busy || highConfidenceCount === 0 || actionsDisabled}
          onClick={() => onBulkConfirm(datasetName, "high_confidence")}
          className="text-[11.5px] font-semibold px-2.5 py-1.5 rounded-lg bg-success-soft text-success disabled:opacity-40"
        >
          Confirm all high-confidence ({highConfidenceCount})
        </button>
        <button
          disabled={busy || actionsDisabled}
          onClick={() => onBulkConfirm(datasetName, "all")}
          className="text-[11.5px] font-semibold px-2.5 py-1.5 rounded-lg border border-line text-ink-soft disabled:opacity-40"
        >
          Confirm all pending ({pendingInDataset.length})
        </button>
      </div>
    </div>
  );
}

export default function DuplicateQueue() {
  const [state, setState] = useState({ loading: false, pending: [], decided: [], error: null });
  const [datasets, setDatasets] = useState([]);
  const [decidingId, setDecidingId] = useState(null);
  const [reviewerName, setReviewerName] = useState("");
  const [busy, setBusy] = useState(false);
  const [bulkBusyKey, setBulkBusyKey] = useState(null);
  const [detectResult, setDetectResult] = useState(null);
  const [bulkResult, setBulkResult] = useState(null);
  // Gates the Pending section below -- "" means no dataset chosen yet,
  // deliberately not auto-loading every dataset's pending clusters by
  // default (2026-08-17, per Khurram's ask: a growing pending list
  // across many datasets was noisy to land on with nothing selected).
  // Shared with GetStartedPanel's "2. Check for duplicates" picker --
  // one selection, not a second redundant dropdown.
  const [selectedDataset, setSelectedDataset] = useState("");

  // Real bug found in live testing (2026-08-18) -- see DatasetBulkBar's
  // own comment for the full story. Any error tied to the currently
  // selected dataset (a failed re-detection, a failed queue load) now
  // disables every confirm/reject/bulk action on this page, not just
  // the action that produced the error -- a pending cluster's own
  // source data can't be independently re-verified from here, so the
  // safe default is "don't allow acting on it while ANYTHING about
  // this dataset is erroring."
  const actionsDisabled = !!state.error;

  // datasetName === "" intentionally shows an empty Pending section --
  // see the state note above. The Decided/audit-log half is unaffected
  // either way: still scoped to the same dataset when one's picked
  // (matching bulk-confirm's own scoping), just not fetched at all
  // until a dataset is chosen, same as Pending.
  async function loadQueue(datasetName) {
    if (!datasetName) {
      setState({ loading: false, pending: [], decided: [], error: null });
      return;
    }
    setState((s) => ({ ...s, loading: true }));
    try {
      const res = await api.getDuplicateQueue(datasetName);
      setState({ loading: false, pending: res.pending, decided: res.decided, error: null });
    } catch (e) {
      setState((s) => ({ ...s, loading: false, error: e.message }));
    }
  }

  async function loadDatasets() {
    try {
      const res = await api.getDatasets();
      setDatasets(res.datasets);
    } catch {
      // Non-fatal for the queue itself -- the picker just stays empty.
    }
  }

  useEffect(() => {
    loadDatasets();
  }, []);

  function handleSelectedDatasetChange(name) {
    setSelectedDataset(name);
    setDetectResult(null);
    setBulkResult(null);
    loadQueue(name);
  }

  async function handleDecide(clusterId, status) {
    const name = reviewerName.trim() || "dashboard reviewer";
    setDecidingId(clusterId);
    try {
      await api.decideCluster(clusterId, status, name);
      await loadQueue(selectedDataset);
    } catch (e) {
      setState((s) => ({ ...s, error: e.message }));
    } finally {
      setDecidingId(null);
    }
  }

  async function handleBulkConfirm(datasetName, tier) {
    const name = reviewerName.trim() || "dashboard reviewer";
    setBulkBusyKey(datasetName);
    setBulkResult(null);
    try {
      const result = await api.bulkConfirm(tier, name, datasetName);
      setBulkResult(result);
      await loadQueue(selectedDataset);
    } catch (e) {
      setState((s) => ({ ...s, error: e.message }));
    } finally {
      setBulkBusyKey(null);
    }
  }

  async function handleDetected(result) {
    setDetectResult(result);
    await loadQueue(selectedDataset);
  }

  // Setting an error via GetStartedPanel's "Find duplicates" failure
  // path (setError prop below) also needs to disable actions, not just
  // errors surfaced from loadQueue/decide/bulk-confirm -- so it goes
  // through the same state.error field rather than a separate one.
  function setPageError(msg) {
    setState((s) => ({ ...s, error: msg }));
  }

  // Pending clusters carry their own dataset_name -- kept as a grouped
  // map even though there's only ever one group now (Pending is always
  // scoped to selectedDataset), so DatasetBulkBar's per-dataset bulk-
  // confirm bar didn't need restructuring.
  const pendingByDataset = state.pending.reduce((acc, c) => {
    const key = c.dataset_name || "unknown dataset";
    (acc[key] ||= []).push(c);
    return acc;
  }, {});

  return (
    <div className="p-7 md:px-8">
      <div className="mb-5 flex items-end justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-xl font-semibold text-ink tracking-tight">Duplicate Resolution Queue</h1>
          <p className="text-[13px] text-ink-faint mt-1">
            Confirming a cluster merges it immediately — this isn't a two-step process.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <label className="text-[11.5px] text-ink-faint">Reviewing as</label>
          <input
            value={reviewerName}
            onChange={(e) => setReviewerName(e.target.value)}
            placeholder="your name"
            className="text-[12px] border border-line rounded-lg px-2.5 py-1.5 w-36"
          />
        </div>
      </div>

      <GetStartedPanel
        datasets={datasets}
        onDatasetsChanged={loadDatasets}
        onDetected={handleDetected}
        selectedDataset={selectedDataset}
        onSelectedDatasetChange={handleSelectedDatasetChange}
        busy={busy}
        setBusy={setBusy}
        setError={setPageError}
      />

      {detectResult && (
        <div className="mb-4 text-[12.5px] text-ink-soft bg-teal-soft border border-teal/20 rounded-xl px-4 py-3">
          {detectResult.applicable
            ? `Found ${detectResult.total_clusters} possible duplicate cluster(s) in "${detectResult.dataset_name}" — ${detectResult.high_confidence_clusters} high-confidence, ${detectResult.needs_review_clusters} needing review. They're in the Pending list below.`
            : `"${detectResult.dataset_name}" doesn't have the columns duplicate detection needs (name + date of birth) — nothing to check.`}
        </div>
      )}

      {bulkResult && (
        <div className="mb-4 text-[12.5px] text-ink-soft bg-success-soft border border-success/20 rounded-xl px-4 py-3">
          Confirmed {bulkResult.clusters_confirmed} cluster(s) in "{bulkResult.dataset_name}" ({bulkResult.tier_confirmed === "high_confidence" ? "high-confidence only" : "all tiers"}) —
          {" "}{bulkResult.golden_records_created} golden record(s) created, {bulkResult.clusters_remaining_pending} still pending.
          {bulkResult.merge_errors?.length > 0 && (
            <span className="text-danger"> {bulkResult.merge_errors.length} cluster(s) failed to merge — see audit log.</span>
          )}
        </div>
      )}

      {state.error && (
        <div className="mb-4 text-[12.5px] text-danger bg-danger-soft border border-danger/20 rounded-xl px-4 py-3">
          <div>{state.error}</div>
          {state.pending.length > 0 && (
            <div className="mt-1.5 font-semibold">
              Confirm/Reject and bulk actions below are paused until this clears — the pending clusters shown may have
              been detected against a version of this dataset's data that's no longer available.
            </div>
          )}
        </div>
      )}

      <div className="mb-3 text-[12.5px] font-semibold text-ink-soft">
        Pending {selectedDataset ? `(${state.pending.length})` : ""}
      </div>
      {!selectedDataset && (
        <div className="text-[12.5px] text-ink-faint bg-[#FAFAFB] border border-line rounded-xl px-4 py-3 mb-6">
          Select a dataset above ("2. Check for duplicates") to view its pending duplicate clusters.
        </div>
      )}
      {selectedDataset && state.loading && <div className="text-[12.5px] text-ink-faint">Loading…</div>}
      {selectedDataset && !state.loading && state.pending.length === 0 && (
        <div className="text-[12.5px] text-ink-faint bg-[#FAFAFB] border border-line rounded-xl px-4 py-3 mb-6">
          Nothing pending for this dataset — check it for duplicates above if you haven't yet.
        </div>
      )}
      {selectedDataset && (
        <div className="flex flex-col gap-6 mb-8">
          {Object.entries(pendingByDataset).map(([datasetName, clusters]) => (
            <div key={datasetName}>
              <DatasetBulkBar
                datasetName={datasetName}
                pendingInDataset={clusters}
                onBulkConfirm={handleBulkConfirm}
                busyKey={bulkBusyKey}
                actionsDisabled={actionsDisabled}
              />
              <div className="flex flex-col gap-3">
                {clusters.map((c) => (
                  <PendingCard
                    key={c.id}
                    cluster={c}
                    onDecide={handleDecide}
                    deciding={decidingId === c.id}
                    actionsDisabled={actionsDisabled}
                  />
                ))}
              </div>
            </div>
          ))}
        </div>
      )}

      <div className="mb-3 text-[12.5px] font-semibold text-ink-soft">
        Decided {selectedDataset ? `(${state.decided.length})` : ""} — permanent audit record
      </div>
      {!selectedDataset && (
        <div className="text-[12.5px] text-ink-faint bg-[#FAFAFB] border border-line rounded-xl px-4 py-3">
          Select a dataset above to view its decided history.
        </div>
      )}
      {selectedDataset && state.decided.length > 0 && (
        <div className="bg-white border border-line rounded-card overflow-hidden">
          <table className="w-full">
            <thead>
              <tr className="border-b border-line bg-[#FAFAFB]">
                <th className="text-left py-2 px-4 text-[10.5px] font-bold text-ink-faint uppercase tracking-wide">Lead name</th>
                <th className="text-left py-2 px-4 text-[10.5px] font-bold text-ink-faint uppercase tracking-wide">Size</th>
                <th className="text-left py-2 px-4 text-[10.5px] font-bold text-ink-faint uppercase tracking-wide">Outcome</th>
                <th className="text-left py-2 px-4 text-[10.5px] font-bold text-ink-faint uppercase tracking-wide">Decided by</th>
                <th className="text-left py-2 px-4 text-[10.5px] font-bold text-ink-faint uppercase tracking-wide">When</th>
              </tr>
            </thead>
            <tbody className="px-4">
              {state.decided.map((e) => (
                <DecidedRow key={e.id} entry={e} />
              ))}
            </tbody>
          </table>
        </div>
      )}
      {selectedDataset && state.decided.length === 0 && (
        <div className="text-[12.5px] text-ink-faint bg-[#FAFAFB] border border-line rounded-xl px-4 py-3">
          Nothing decided yet for this dataset.
        </div>
      )}
    </div>
  );
}
