import { useEffect, useState } from "react";
import { api } from "../api";
import DatasetPicker from "../components/DatasetPicker";

function RoleCard({ role, onAssign, onUnassign, busy }) {
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(role.assignee_name || "");
  const [email, setEmail] = useState(role.assignee_email || "");
  const [note, setNote] = useState(role.note || "");
  const [assignedBy, setAssignedBy] = useState("");
  const [error, setError] = useState(null);

  function startEdit() {
    setName(role.assignee_name || "");
    setEmail(role.assignee_email || "");
    setNote(role.note || "");
    setAssignedBy("");
    setError(null);
    setEditing(true);
  }

  async function submit() {
    if (!name.trim()) {
      setError("Assignee name is required.");
      return;
    }
    if (!assignedBy.trim()) {
      setError("Your name (assigned by) is required.");
      return;
    }
    setError(null);
    try {
      await onAssign(role.role, name.trim(), email.trim() || null, assignedBy.trim(), note.trim() || null);
      setEditing(false);
    } catch (e) {
      setError(e.message);
    }
  }

  return (
    <div className="bg-white border border-line rounded-xl px-4 py-3.5">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className={`w-2 h-2 rounded-full inline-block ${role.assigned ? "bg-teal" : "bg-[#D6D6DA]"}`} />
            <span className="text-[12.5px] font-semibold text-ink">{role.label}</span>
          </div>
          <p className="text-[11.5px] text-ink-faint mt-1">{role.description}</p>
        </div>
        {!editing && (
          <div className="flex items-center gap-2 shrink-0">
            <button
              onClick={startEdit}
              className="text-[11px] font-semibold text-teal bg-teal-soft px-2.5 py-1 rounded-full hover:opacity-80"
            >
              {role.assigned ? "Reassign" : "Assign"}
            </button>
            {role.assigned && (
              <button
                onClick={() => onUnassign(role.role)}
                disabled={busy}
                className="text-[11px] font-medium text-ink-faint hover:text-danger px-1"
              >
                Unassign
              </button>
            )}
          </div>
        )}
      </div>

      {!editing && role.assigned && (
        <div className="mt-2.5 bg-[#FAFAFB] border border-line rounded-lg px-3 py-2.5">
          <div className="text-[12px] text-ink font-medium">
            {role.assignee_name}
            {role.assignee_email && <span className="text-ink-faint font-normal"> · {role.assignee_email}</span>}
          </div>
          {role.note && <div className="text-[11.5px] text-ink-soft mt-1">{role.note}</div>}
          <div className="text-[10.5px] text-ink-faint mt-1.5">
            Assigned by {role.assigned_by} on {role.assigned_at?.slice(0, 16).replace("T", " ")}
          </div>
        </div>
      )}

      {!editing && !role.assigned && (
        <div className="mt-2.5 text-[11.5px] text-ink-faint">Unassigned</div>
      )}

      {editing && (
        <div className="mt-3 flex flex-col gap-2">
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Assignee name *"
            className="text-[12.5px] border border-line rounded-lg px-3 py-1.5"
          />
          <input
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="Assignee email (optional)"
            className="text-[12.5px] border border-line rounded-lg px-3 py-1.5"
          />
          <input
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="Note (optional)"
            className="text-[12.5px] border border-line rounded-lg px-3 py-1.5"
          />
          <input
            value={assignedBy}
            onChange={(e) => setAssignedBy(e.target.value)}
            placeholder="Your name (assigned by) *"
            className="text-[12.5px] border border-line rounded-lg px-3 py-1.5"
          />
          {error && <div className="text-[11px] text-danger">{error}</div>}
          <div className="flex items-center gap-2 mt-0.5">
            <button
              onClick={submit}
              disabled={busy}
              className="text-[11.5px] font-semibold text-white bg-teal px-3 py-1.5 rounded-lg hover:opacity-90 disabled:opacity-50"
            >
              Save
            </button>
            <button
              onClick={() => setEditing(false)}
              className="text-[11.5px] font-medium text-ink-faint hover:text-ink px-1"
            >
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

export default function DataStewardship() {
  const [datasets, setDatasets] = useState([]);
  const [datasetsLoaded, setDatasetsLoaded] = useState(false);
  const [selected, setSelected] = useState("");
  const [state, setState] = useState({ loading: false, data: null, error: null });
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    async function loadDatasets() {
      try {
        const res = await api.getDatasets();
        if (cancelled) return;
        const list = res.datasets ?? [];
        setDatasets(list);
        if (list.length === 1) setSelected(list[0].dataset_name);
        setDatasetsLoaded(true);
      } catch (e) {
        if (!cancelled) {
          setDatasetsLoaded(true);
          setState((s) => ({ ...s, error: e.message }));
        }
      }
    }
    loadDatasets();
    return () => {
      cancelled = true;
    };
  }, []);

  async function reload() {
    if (!selected) return;
    setState((s) => ({ ...s, loading: true }));
    try {
      const res = await api.getStewardship(selected);
      setState({ loading: false, data: res, error: null });
    } catch (e) {
      setState({ loading: false, data: null, error: e.message });
    }
  }

  useEffect(() => {
    if (!selected) {
      setState({ loading: false, data: null, error: null });
      return;
    }
    reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected]);

  async function handleAssign(role, assigneeName, assigneeEmail, assignedBy, note) {
    setBusy(true);
    try {
      const res = await api.assignStewardship(selected, role, assigneeName, assigneeEmail, assignedBy, note);
      setState({ loading: false, data: res, error: null });
    } finally {
      setBusy(false);
    }
  }

  async function handleUnassign(role) {
    setBusy(true);
    try {
      const res = await api.unassignStewardship(selected, role);
      setState({ loading: false, data: res, error: null });
    } catch (e) {
      setState((s) => ({ ...s, error: e.message }));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="p-7 md:px-8">
      <div className="mb-5 flex items-start justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-xl font-semibold text-ink tracking-tight">Data Stewardship</h1>
          <p className="text-[13px] text-ink-faint mt-1">
            Who's accountable for this dataset -- Business Owner, Data Owner, Data Steward, Data Custodian, and Data Consumer.
          </p>
        </div>
        <DatasetPicker datasets={datasets} value={selected} onChange={setSelected} />
      </div>

      {!selected && datasetsLoaded && datasets.length > 1 && (
        <div className="text-[12.5px] text-ink-faint bg-[#FAFAFB] border border-line rounded-xl px-4 py-3">
          Select a dataset above to view or assign its stewardship roles.
        </div>
      )}
      {!selected && datasetsLoaded && datasets.length === 0 && (
        <div className="text-[12.5px] text-ink-faint bg-[#FAFAFB] border border-line rounded-xl px-4 py-3">
          No datasets yet -- upload one first.
        </div>
      )}

      {state.error && (
        <div className="mb-4 text-[12.5px] text-danger bg-danger-soft border border-danger/20 rounded-xl px-4 py-3">
          {state.error}
        </div>
      )}
      {selected && state.loading && <div className="text-[12.5px] text-ink-faint">Loading…</div>}

      {selected && state.data && (
        <>
          <div className="text-[11.5px] text-ink-faint mb-3">
            {state.data.roles_assigned} of {state.data.roles_total} roles assigned
          </div>
          <div className="flex flex-col gap-2.5">
            {state.data.roles.map((role) => (
              <RoleCard
                key={role.role}
                role={role}
                onAssign={(r, n, e, ab, note) => handleAssign(r, n, e, ab, note)}
                onUnassign={handleUnassign}
                busy={busy}
              />
            ))}
          </div>
        </>
      )}
    </div>
  );
}
