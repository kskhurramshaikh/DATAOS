import { useEffect, useState } from "react";
import { api } from "../api";
import { useAuth } from "../auth";
import DatasetPicker from "../components/DatasetPicker";
import { Link } from "react-router-dom";

function RoleCard({ role, onAssign, onUnassign, busy, canAssign }) {
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(role.assignee_name || "");
  const [email, setEmail] = useState(role.assignee_email || "");
  const [note, setNote] = useState(role.note || "");
  const [error, setError] = useState(null);

  function startEdit() {
    setName(role.assignee_name || "");
    setEmail(role.assignee_email || "");
    setNote(role.note || "");
    setError(null);
    setEditing(true);
  }

  async function submit() {
    if (!name.trim()) {
      setError("Assignee name is required.");
      return;
    }
    setError(null);
    try {
      await onAssign(role.role, name.trim(), email.trim() || null, note.trim() || null);
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
        {!editing && canAssign && (
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

const REVIEW_FREQUENCIES = [
  { value: "monthly", label: "Monthly" },
  { value: "quarterly", label: "Quarterly" },
  { value: "semi_annual", label: "Semi-annual" },
  { value: "annual", label: "Annual" },
];

// Stewardship Policy Wizard -- closes the "no policy wizard anywhere
// on the page" gap. A guided, multi-step way to define WHAT the
// accountability rules are for this dataset (retention period, review
// cadence, a real quality bar, who to escalate to) -- a separate
// concern from the role cards above, which only cover WHO holds each
// role. See app/adapters/stewardship_adapter.py's Policy Wizard
// section for the backend reasoning.
function PolicyWizard({ datasetName, canEdit }) {
  const [state, setState] = useState({ loading: true, data: null, error: null });
  const [wizardOpen, setWizardOpen] = useState(false);
  const [step, setStep] = useState(0);
  const [form, setForm] = useState({
    retention_period_days: "",
    review_frequency: "",
    quality_threshold_pct: "",
    escalation_contact: "",
    notes: "",
  });
  const [saveError, setSaveError] = useState(null);
  const [saving, setSaving] = useState(false);

  async function load() {
    setState((s) => ({ ...s, loading: true }));
    try {
      const res = await api.getStewardshipPolicy(datasetName);
      setState({ loading: false, data: res, error: null });
    } catch (e) {
      setState({ loading: false, data: null, error: e.message });
    }
  }

  useEffect(() => {
    if (datasetName) load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [datasetName]);

  function openWizard() {
    const d = state.data;
    setForm({
      retention_period_days: d?.retention_period_days ?? "",
      review_frequency: d?.review_frequency ?? "",
      quality_threshold_pct: d?.quality_threshold_pct ?? "",
      escalation_contact: d?.escalation_contact ?? "",
      notes: d?.notes ?? "",
    });
    setStep(0);
    setSaveError(null);
    setWizardOpen(true);
  }

  async function handleSave() {
    setSaving(true);
    setSaveError(null);
    try {
      await api.setStewardshipPolicy(datasetName, {
        retention_period_days: form.retention_period_days || null,
        review_frequency: form.review_frequency || null,
        quality_threshold_pct: form.quality_threshold_pct || null,
        escalation_contact: form.escalation_contact.trim() || null,
        notes: form.notes.trim() || null,
      });
      setWizardOpen(false);
      await load();
    } catch (e) {
      setSaveError(e.message);
    } finally {
      setSaving(false);
    }
  }

  const steps = ["Retention & Review", "Quality & Escalation", "Review & Save"];

  return (
    <div className="bg-white border border-line rounded-card px-6 py-4 mb-5">
      <div className="flex items-center justify-between mb-1">
        <div className="text-[12px] font-bold text-ink-faint uppercase tracking-wide">Stewardship Policy</div>
        {canEdit && !wizardOpen && (
          <button
            onClick={openWizard}
            className="text-[11px] font-semibold text-teal bg-teal-soft px-2.5 py-1 rounded-full hover:opacity-80"
          >
            {state.data?.configured ? "Edit policy" : "Set up policy"}
          </button>
        )}
      </div>

      {!wizardOpen && state.loading && <div className="text-[12px] text-ink-faint">Loading…</div>}
      {!wizardOpen && state.error && <div className="text-[12px] text-danger">{state.error}</div>}

      {!wizardOpen && state.data && !state.data.configured && (
        <div className="text-[11.5px] text-ink-faint">No stewardship policy configured yet for this dataset.</div>
      )}

      {!wizardOpen && state.data?.configured && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mt-2">
          <div>
            <div className="text-[10.5px] font-bold text-ink-faint uppercase tracking-wide">Retention</div>
            <div className="text-[13px] text-ink mt-0.5">
              {state.data.retention_period_days ? `${state.data.retention_period_days} days` : "—"}
            </div>
          </div>
          <div>
            <div className="text-[10.5px] font-bold text-ink-faint uppercase tracking-wide">Review cadence</div>
            <div className="text-[13px] text-ink mt-0.5">{state.data.review_frequency_label || "—"}</div>
          </div>
          <div>
            <div className="text-[10.5px] font-bold text-ink-faint uppercase tracking-wide">Quality threshold</div>
            <div className="text-[13px] text-ink mt-0.5">
              {state.data.quality_threshold_pct != null ? `${state.data.quality_threshold_pct}%` : "—"}
            </div>
          </div>
          <div>
            <div className="text-[10.5px] font-bold text-ink-faint uppercase tracking-wide">Escalation contact</div>
            <div className="text-[13px] text-ink mt-0.5">{state.data.escalation_contact || "—"}</div>
          </div>
          {state.data.notes && (
            <div className="col-span-2 sm:col-span-4">
              <div className="text-[10.5px] font-bold text-ink-faint uppercase tracking-wide">Notes</div>
              <div className="text-[12.5px] text-ink-soft mt-0.5">{state.data.notes}</div>
            </div>
          )}
          <div className="col-span-2 sm:col-span-4 text-[10.5px] text-ink-faint mt-1">
            Set by {state.data.set_by} on {state.data.updated_at?.slice(0, 16).replace("T", " ")}
          </div>
        </div>
      )}

      {wizardOpen && (
        <div className="mt-3">
          <div className="flex items-center gap-2 mb-3.5 flex-wrap">
            {steps.map((label, i) => (
              <div key={label} className="flex items-center gap-2">
                <span
                  className={`w-5 h-5 rounded-full flex items-center justify-center text-[10.5px] font-semibold ${
                    i === step ? "bg-teal text-white" : i < step ? "bg-teal-soft text-teal" : "bg-[#F0F0F2] text-ink-faint"
                  }`}
                >
                  {i + 1}
                </span>
                <span className={`text-[11.5px] ${i === step ? "font-semibold text-ink" : "text-ink-faint"}`}>{label}</span>
                {i < steps.length - 1 && <span className="text-ink-faint mx-1">→</span>}
              </div>
            ))}
          </div>

          {step === 0 && (
            <div className="flex flex-col gap-2.5 max-w-sm">
              <label className="text-[11.5px] text-ink-soft">
                Retention period (days)
                <input
                  type="number"
                  min="1"
                  value={form.retention_period_days}
                  onChange={(e) => setForm((f) => ({ ...f, retention_period_days: e.target.value }))}
                  className="mt-1 w-full text-[12.5px] border border-line rounded-lg px-3 py-1.5"
                  placeholder="e.g. 365"
                />
              </label>
              <label className="text-[11.5px] text-ink-soft">
                Review cadence
                <select
                  value={form.review_frequency}
                  onChange={(e) => setForm((f) => ({ ...f, review_frequency: e.target.value }))}
                  className="mt-1 w-full text-[12.5px] border border-line rounded-lg px-3 py-1.5 bg-white"
                >
                  <option value="">Select…</option>
                  {REVIEW_FREQUENCIES.map((f) => (
                    <option key={f.value} value={f.value}>
                      {f.label}
                    </option>
                  ))}
                </select>
              </label>
            </div>
          )}

          {step === 1 && (
            <div className="flex flex-col gap-2.5 max-w-sm">
              <label className="text-[11.5px] text-ink-soft">
                Data quality threshold (%)
                <input
                  type="number"
                  min="0"
                  max="100"
                  value={form.quality_threshold_pct}
                  onChange={(e) => setForm((f) => ({ ...f, quality_threshold_pct: e.target.value }))}
                  className="mt-1 w-full text-[12.5px] border border-line rounded-lg px-3 py-1.5"
                  placeholder="e.g. 95"
                />
              </label>
              <label className="text-[11.5px] text-ink-soft">
                Escalation contact
                <input
                  value={form.escalation_contact}
                  onChange={(e) => setForm((f) => ({ ...f, escalation_contact: e.target.value }))}
                  className="mt-1 w-full text-[12.5px] border border-line rounded-lg px-3 py-1.5"
                  placeholder="name or email"
                />
              </label>
            </div>
          )}

          {step === 2 && (
            <div className="flex flex-col gap-2.5 max-w-sm">
              <label className="text-[11.5px] text-ink-soft">
                Notes (optional)
                <textarea
                  value={form.notes}
                  onChange={(e) => setForm((f) => ({ ...f, notes: e.target.value }))}
                  className="mt-1 w-full text-[12.5px] border border-line rounded-lg px-3 py-1.5"
                  rows={3}
                />
              </label>
              <div className="bg-[#FAFAFB] border border-line rounded-lg px-3 py-2.5 text-[11.5px] text-ink-soft leading-relaxed">
                <div>
                  <span className="text-ink-faint">Retention:</span>{" "}
                  {form.retention_period_days ? `${form.retention_period_days} days` : "not set"}
                </div>
                <div>
                  <span className="text-ink-faint">Review cadence:</span>{" "}
                  {REVIEW_FREQUENCIES.find((f) => f.value === form.review_frequency)?.label || "not set"}
                </div>
                <div>
                  <span className="text-ink-faint">Quality threshold:</span>{" "}
                  {form.quality_threshold_pct ? `${form.quality_threshold_pct}%` : "not set"}
                </div>
                <div>
                  <span className="text-ink-faint">Escalation contact:</span> {form.escalation_contact || "not set"}
                </div>
              </div>
              {saveError && <div className="text-[11px] text-danger">{saveError}</div>}
            </div>
          )}

          <div className="flex items-center gap-2 mt-3.5">
            {step > 0 && (
              <button
                onClick={() => setStep((s) => s - 1)}
                className="text-[11.5px] font-medium text-ink-faint hover:text-ink px-2 py-1.5"
              >
                Back
              </button>
            )}
            {step < steps.length - 1 && (
              <button
                onClick={() => setStep((s) => s + 1)}
                className="text-[11.5px] font-semibold text-white bg-teal px-3 py-1.5 rounded-lg hover:opacity-90"
              >
                Next
              </button>
            )}
            {step === steps.length - 1 && (
              <button
                onClick={handleSave}
                disabled={saving}
                className="text-[11.5px] font-semibold text-white bg-teal px-3 py-1.5 rounded-lg hover:opacity-90 disabled:opacity-50"
              >
                {saving ? "Saving…" : "Save policy"}
              </button>
            )}
            <button onClick={() => setWizardOpen(false)} className="text-[11.5px] font-medium text-ink-faint hover:text-ink px-1">
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

const TASK_STATUS_STYLES = {
  open: "bg-[#F0F0F2] text-ink-faint",
  in_progress: "bg-teal-soft text-teal",
  done: "bg-[#E4F5EA] text-[#1F8A4C]",
  cancelled: "bg-[#F0F0F2] text-ink-faint line-through",
};

const TASK_STATUS_CYCLE = { open: "in_progress", in_progress: "done", done: "open" };

function TaskRow({ task, onAdvance, onCancel, onDelete, canEdit, busy }) {
  const isTerminal = task.status === "done" || task.status === "cancelled";
  return (
    <div className="bg-white border border-line rounded-xl px-4 py-3 flex items-start justify-between gap-3">
      <div className="min-w-0">
        <div className="flex items-center gap-2 flex-wrap">
          <span className={`text-[10.5px] font-semibold px-2 py-0.5 rounded-full ${TASK_STATUS_STYLES[task.status]}`}>
            {task.status_label}
          </span>
          <span className="text-[12.5px] font-semibold text-ink">{task.title}</span>
        </div>
        <div className="text-[11.5px] text-ink-soft mt-1">
          {task.assignee_name}
          {task.assignee_email && <span className="text-ink-faint"> · {task.assignee_email}</span>}
          {task.due_date && <span className="text-ink-faint"> · due {task.due_date}</span>}
        </div>
        {task.notes && <div className="text-[11.5px] text-ink-soft mt-1">{task.notes}</div>}
        <div className="text-[10.5px] text-ink-faint mt-1.5">
          Created by {task.created_by} on {task.created_at?.slice(0, 16).replace("T", " ")}
        </div>
      </div>
      {canEdit && (
        <div className="flex items-center gap-2 shrink-0">
          {!isTerminal && (
            <>
              <button
                onClick={() => onAdvance(task)}
                disabled={busy}
                className="text-[11px] font-semibold text-teal bg-teal-soft px-2.5 py-1 rounded-full hover:opacity-80 disabled:opacity-50"
              >
                {task.status === "open" ? "Start" : "Mark done"}
              </button>
              <button
                onClick={() => onCancel(task)}
                disabled={busy}
                className="text-[11px] font-medium text-ink-faint hover:text-danger px-1"
              >
                Cancel
              </button>
            </>
          )}
          <button
            onClick={() => onDelete(task)}
            disabled={busy}
            className="text-[11px] font-medium text-ink-faint hover:text-danger px-1"
          >
            Delete
          </button>
        </div>
      )}
    </div>
  );
}

// Data Stewardship Task Assignment -- the last named gap on this page.
// Distinct from BOTH the role cards (WHO holds each standing role) and
// the Policy Wizard above (WHAT the accountability rules are): tasks
// answer WHAT NEEDS DOING, right now, by whom, by when. Deliberately a
// plain status list a human moves themselves (open -> in_progress ->
// done, or -> cancelled) -- no approval workflow, no notifications, no
// due-date reminders. See stewardship_adapter.py's Task Assignment
// section for the full backend reasoning.
function TaskList({ datasetName, canEdit }) {
  const [state, setState] = useState({ loading: true, data: null, error: null });
  const [busy, setBusy] = useState(false);
  const [formOpen, setFormOpen] = useState(false);
  const [form, setForm] = useState({ title: "", assignee_name: "", assignee_email: "", due_date: "", notes: "" });
  const [formError, setFormError] = useState(null);
  const [showDone, setShowDone] = useState(false);

  async function load() {
    setState((s) => ({ ...s, loading: true }));
    try {
      const res = await api.getStewardshipTasks(datasetName);
      setState({ loading: false, data: res, error: null });
    } catch (e) {
      setState({ loading: false, data: null, error: e.message });
    }
  }

  useEffect(() => {
    if (datasetName) load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [datasetName]);

  function openForm() {
    setForm({ title: "", assignee_name: "", assignee_email: "", due_date: "", notes: "" });
    setFormError(null);
    setFormOpen(true);
  }

  async function handleCreate() {
    if (!form.title.trim() || !form.assignee_name.trim()) {
      setFormError("Title and assignee name are required.");
      return;
    }
    setBusy(true);
    setFormError(null);
    try {
      const res = await api.createStewardshipTask(datasetName, {
        title: form.title.trim(),
        assignee_name: form.assignee_name.trim(),
        assignee_email: form.assignee_email.trim() || null,
        due_date: form.due_date || null,
        notes: form.notes.trim() || null,
      });
      setState({ loading: false, data: res, error: null });
      setFormOpen(false);
    } catch (e) {
      setFormError(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function handleAdvance(task) {
    setBusy(true);
    try {
      const nextStatus = TASK_STATUS_CYCLE[task.status] || "done";
      const res = await api.updateStewardshipTaskStatus(datasetName, task.id, nextStatus);
      setState({ loading: false, data: res, error: null });
    } catch (e) {
      setState((s) => ({ ...s, error: e.message }));
    } finally {
      setBusy(false);
    }
  }

  async function handleCancel(task) {
    setBusy(true);
    try {
      const res = await api.updateStewardshipTaskStatus(datasetName, task.id, "cancelled");
      setState({ loading: false, data: res, error: null });
    } catch (e) {
      setState((s) => ({ ...s, error: e.message }));
    } finally {
      setBusy(false);
    }
  }

  async function handleDelete(task) {
    setBusy(true);
    try {
      const res = await api.deleteStewardshipTask(datasetName, task.id);
      setState({ loading: false, data: res, error: null });
    } catch (e) {
      setState((s) => ({ ...s, error: e.message }));
    } finally {
      setBusy(false);
    }
  }

  const tasks = state.data?.tasks ?? [];
  const visibleTasks = showDone ? tasks : tasks.filter((t) => t.status !== "done" && t.status !== "cancelled");

  return (
    <div className="bg-white border border-line rounded-card px-6 py-4 mb-5">
      <div className="flex items-center justify-between mb-1 flex-wrap gap-2">
        <div className="flex items-center gap-2">
          <div className="text-[12px] font-bold text-ink-faint uppercase tracking-wide">Tasks</div>
          {state.data && (
            <span className="text-[10.5px] text-ink-faint">
              {state.data.tasks_open} open of {state.data.tasks_total}
            </span>
          )}
        </div>
        <div className="flex items-center gap-3">
          {tasks.some((t) => t.status === "done" || t.status === "cancelled") && (
            <button
              onClick={() => setShowDone((v) => !v)}
              className="text-[11px] font-medium text-ink-faint hover:text-ink"
            >
              {showDone ? "Hide done/cancelled" : "Show done/cancelled"}
            </button>
          )}
          {canEdit && !formOpen && (
            <button
              onClick={openForm}
              className="text-[11px] font-semibold text-teal bg-teal-soft px-2.5 py-1 rounded-full hover:opacity-80"
            >
              New task
            </button>
          )}
        </div>
      </div>

      {state.loading && <div className="text-[12px] text-ink-faint">Loading…</div>}
      {state.error && <div className="text-[12px] text-danger">{state.error}</div>}

      {formOpen && (
        <div className="mt-3 mb-3 flex flex-col gap-2 max-w-sm">
          <input
            value={form.title}
            onChange={(e) => setForm((f) => ({ ...f, title: e.target.value }))}
            placeholder="Task title *"
            className="text-[12.5px] border border-line rounded-lg px-3 py-1.5"
          />
          <input
            value={form.assignee_name}
            onChange={(e) => setForm((f) => ({ ...f, assignee_name: e.target.value }))}
            placeholder="Assignee name *"
            className="text-[12.5px] border border-line rounded-lg px-3 py-1.5"
          />
          <input
            value={form.assignee_email}
            onChange={(e) => setForm((f) => ({ ...f, assignee_email: e.target.value }))}
            placeholder="Assignee email (optional)"
            className="text-[12.5px] border border-line rounded-lg px-3 py-1.5"
          />
          <label className="text-[11.5px] text-ink-soft">
            Due date (optional)
            <input
              type="date"
              value={form.due_date}
              onChange={(e) => setForm((f) => ({ ...f, due_date: e.target.value }))}
              className="mt-1 w-full text-[12.5px] border border-line rounded-lg px-3 py-1.5"
            />
          </label>
          <textarea
            value={form.notes}
            onChange={(e) => setForm((f) => ({ ...f, notes: e.target.value }))}
            placeholder="Notes (optional)"
            className="text-[12.5px] border border-line rounded-lg px-3 py-1.5"
            rows={2}
          />
          {formError && <div className="text-[11px] text-danger">{formError}</div>}
          <div className="flex items-center gap-2 mt-0.5">
            <button
              onClick={handleCreate}
              disabled={busy}
              className="text-[11.5px] font-semibold text-white bg-teal px-3 py-1.5 rounded-lg hover:opacity-90 disabled:opacity-50"
            >
              Create task
            </button>
            <button
              onClick={() => setFormOpen(false)}
              className="text-[11.5px] font-medium text-ink-faint hover:text-ink px-1"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {!state.loading && !state.error && visibleTasks.length === 0 && (
        <div className="text-[11.5px] text-ink-faint mt-2">
          {tasks.length === 0 ? "No tasks yet for this dataset." : "No open tasks -- all caught up."}
        </div>
      )}

      {visibleTasks.length > 0 && (
        <div className="flex flex-col gap-2.5 mt-3">
          {visibleTasks.map((task) => (
            <TaskRow
              key={task.id}
              task={task}
              onAdvance={handleAdvance}
              onCancel={handleCancel}
              onDelete={handleDelete}
              canEdit={canEdit}
              busy={busy}
            />
          ))}
        </div>
      )}
    </div>
  );
}

export default function DataStewardship() {
  const { isAuthenticated } = useAuth();
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

  async function handleAssign(role, assigneeName, assigneeEmail, note) {
    setBusy(true);
    try {
      // assigned_by is no longer passed from here -- see api.js's
      // assignStewardship() docstring. The server takes it from the
      // caller's own verified login, wired 2026-08-19 alongside the
      // RBAC/OPA gate on this same endpoint.
      const res = await api.assignStewardship(selected, role, assigneeName, assigneeEmail, note);
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

      {!isAuthenticated && (
        <div className="mb-4 text-[12.5px] text-ink-soft bg-[#FAFAFB] border border-line rounded-xl px-4 py-3">
          Viewing only.{" "}
          <Link to="/account" className="text-teal font-semibold hover:opacity-80">
            Sign in
          </Link>{" "}
          with a Data Owner or Admin role to assign or reassign these roles.
        </div>
      )}

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
          <PolicyWizard datasetName={selected} canEdit={isAuthenticated} />
          <TaskList datasetName={selected} canEdit={isAuthenticated} />

          <div className="text-[11.5px] text-ink-faint mb-3">
            {state.data.roles_assigned} of {state.data.roles_total} roles assigned
          </div>
          <div className="flex flex-col gap-2.5">
            {state.data.roles.map((role) => (
              <RoleCard
                key={role.role}
                role={role}
                onAssign={handleAssign}
                onUnassign={handleUnassign}
                busy={busy}
                canAssign={isAuthenticated}
              />
            ))}
          </div>
        </>
      )}
    </div>
  );
}
