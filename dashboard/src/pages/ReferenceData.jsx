import { Fragment, useEffect, useState } from "react";
import { useAuth } from "../auth";
import { api } from "../api";

// Reference Data Management (2026-08-20) -- Item A from the
// baj-dashboard reference-platform review. Real Postgres-backed named
// lists of code/label pairs (country codes, currency codes, region
// codes), unlike the reference platform's localStorage-only version.
// Same RBAC/OPA gate pattern as Glossary/Classification/Stewardship
// -- viewing is open, maintaining requires sign-in. See
// app/adapters/reference_data_adapter.py's module docstring for the
// full seeding reasoning: 3 lists ship pre-populated with real,
// verified public standards (ISO countries, ISO currencies, Saudi
// regions); org-specific lists (department codes, budget categories)
// are deliberately NOT seeded -- a real one only gets created when
// someone supplies real values.

function formatWhen(ts) {
  if (!ts) return "—";
  return String(ts).replace("T", " ").slice(0, 16) + " UTC";
}

// The detail drawer for one list -- shown inline below the row it
// belongs to when expanded, rather than a separate route/modal, same
// "expand in place" pattern GoldenRecordRegistry's own cards use.
function ListDetailDrawer({ list, canEdit, onListChanged, onClose }) {
  const [detail, setDetail] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [search, setSearch] = useState("");
  const [addingValue, setAddingValue] = useState(false);
  const [newCode, setNewCode] = useState("");
  const [newLabel, setNewLabel] = useState("");
  const [editingValueId, setEditingValueId] = useState(null);
  const [editingLabel, setEditingLabel] = useState("");
  const [busy, setBusy] = useState(false);
  const [rowError, setRowError] = useState(null);

  async function load() {
    setLoading(true);
    try {
      const res = await api.getReferenceDataList(list.id);
      setDetail(res);
      setError(null);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [list.id]);

  async function handleAddValue() {
    if (!newCode.trim() || !newLabel.trim()) {
      setRowError("Both a code and a label are required.");
      return;
    }
    setBusy(true);
    setRowError(null);
    try {
      const res = await api.addReferenceDataValue(list.id, newCode.trim(), newLabel.trim());
      setDetail(res);
      setNewCode("");
      setNewLabel("");
      setAddingValue(false);
      onListChanged();
    } catch (e) {
      setRowError(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function handleSaveEdit(valueId) {
    if (!editingLabel.trim()) {
      setRowError("Label is required.");
      return;
    }
    setBusy(true);
    setRowError(null);
    try {
      const res = await api.updateReferenceDataValue(valueId, editingLabel.trim());
      setDetail(res);
      setEditingValueId(null);
    } catch (e) {
      setRowError(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function handleDeleteValue(valueId) {
    setBusy(true);
    try {
      const res = await api.deleteReferenceDataValue(valueId);
      setDetail(res);
      onListChanged();
    } catch (e) {
      setRowError(e.message);
    } finally {
      setBusy(false);
    }
  }

  const q = search.trim().toLowerCase();
  const values = detail?.values ?? [];
  const visibleValues = !q
    ? values
    : values.filter((v) => v.code.toLowerCase().includes(q) || v.label.toLowerCase().includes(q));

  return (
    <div className="border-t border-line bg-[#FAFAFB] px-5 py-4">
      <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
        <div>
          {list.description && <div className="text-[12px] text-ink-soft">{list.description}</div>}
        </div>
        <div className="flex items-center gap-2">
          {values.length > 5 && (
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search code or label…"
              className="text-[12px] border border-line rounded-lg px-3 py-1.5 w-52 bg-white"
            />
          )}
          {canEdit && !addingValue && (
            <button
              onClick={() => setAddingValue(true)}
              className="text-[11px] font-semibold text-teal bg-teal-soft px-2.5 py-1 rounded-full hover:opacity-80"
            >
              Add value
            </button>
          )}
          <button onClick={onClose} className="text-[11px] font-medium text-ink-faint hover:text-ink px-1">
            Close
          </button>
        </div>
      </div>

      {error && (
        <div className="mb-3 text-[12.5px] text-danger bg-danger-soft border border-danger/20 rounded-xl px-4 py-3">
          {error}
        </div>
      )}
      {rowError && (
        <div className="mb-3 text-[12px] text-danger bg-danger-soft border border-danger/20 rounded-xl px-3 py-2">
          {rowError}
        </div>
      )}

      {addingValue && (
        <div className="bg-white border border-line rounded-xl px-4 py-3 mb-3 flex flex-wrap items-end gap-2.5">
          <div>
            <label className="block text-[10.5px] text-ink-faint mb-1">Code *</label>
            <input
              value={newCode}
              onChange={(e) => setNewCode(e.target.value)}
              placeholder="e.g. FR"
              className="text-[12.5px] border border-line rounded-lg px-3 py-1.5 w-32"
            />
          </div>
          <div>
            <label className="block text-[10.5px] text-ink-faint mb-1">Label *</label>
            <input
              value={newLabel}
              onChange={(e) => setNewLabel(e.target.value)}
              placeholder="e.g. France"
              className="text-[12.5px] border border-line rounded-lg px-3 py-1.5 w-56"
            />
          </div>
          <button
            onClick={handleAddValue}
            disabled={busy}
            className="text-[11.5px] font-semibold text-white bg-teal px-3 py-1.5 rounded-lg hover:opacity-90 disabled:opacity-50"
          >
            Add
          </button>
          <button
            onClick={() => {
              setAddingValue(false);
              setRowError(null);
            }}
            className="text-[11.5px] font-medium text-ink-faint hover:text-ink px-1"
          >
            Cancel
          </button>
        </div>
      )}

      {loading && <div className="text-[12.5px] text-ink-faint py-3">Loading…</div>}

      {!loading && values.length === 0 && (
        <div className="bg-white border border-line rounded-xl px-5 py-6 text-center text-[12.5px] text-ink-soft">
          No values in this list yet.
        </div>
      )}

      {!loading && values.length > 0 && (
        <div className="bg-white border border-line rounded-xl overflow-hidden">
          <table className="w-full text-left">
            <thead>
              <tr className="border-b border-line bg-[#FAFAFB]">
                <th className="py-2 px-4 text-[10.5px] font-bold text-ink-faint uppercase tracking-wide w-32">Code</th>
                <th className="py-2 px-4 text-[10.5px] font-bold text-ink-faint uppercase tracking-wide">Label</th>
                {canEdit && <th className="py-2 px-4 text-[10.5px] font-bold text-ink-faint uppercase tracking-wide text-right">Actions</th>}
              </tr>
            </thead>
            <tbody>
              {visibleValues.length === 0 && (
                <tr>
                  <td colSpan={canEdit ? 3 : 2} className="py-4 px-4 text-[12px] text-ink-faint text-center">
                    No values match "{search}".
                  </td>
                </tr>
              )}
              {visibleValues.map((v) => (
                <tr key={v.id} className="border-b border-line last:border-0">
                  <td className="py-2 px-4 text-[12px] font-mono font-semibold text-ink">{v.code}</td>
                  <td className="py-2 px-4 text-[12.5px] text-ink-soft">
                    {editingValueId === v.id ? (
                      <input
                        value={editingLabel}
                        onChange={(e) => setEditingLabel(e.target.value)}
                        autoFocus
                        className="text-[12.5px] border border-line rounded-lg px-2.5 py-1 w-full"
                      />
                    ) : (
                      v.label
                    )}
                  </td>
                  {canEdit && (
                    <td className="py-2 px-4 text-right whitespace-nowrap">
                      {editingValueId === v.id ? (
                        <>
                          <button
                            onClick={() => handleSaveEdit(v.id)}
                            disabled={busy}
                            className="text-[11px] font-semibold text-teal hover:underline mr-2.5"
                          >
                            Save
                          </button>
                          <button
                            onClick={() => setEditingValueId(null)}
                            className="text-[11px] font-medium text-ink-faint hover:text-ink"
                          >
                            Cancel
                          </button>
                        </>
                      ) : (
                        <>
                          <button
                            onClick={() => {
                              setEditingValueId(v.id);
                              setEditingLabel(v.label);
                              setRowError(null);
                            }}
                            className="text-[11px] font-medium text-ink-faint hover:text-teal mr-2.5"
                          >
                            Edit
                          </button>
                          <button
                            onClick={() => handleDeleteValue(v.id)}
                            disabled={busy}
                            className="text-[11px] font-medium text-ink-faint hover:text-danger"
                          >
                            Delete
                          </button>
                        </>
                      )}
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function CreateListForm({ onCreated, onCancel }) {
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [description, setDescription] = useState("");
  const [owner, setOwner] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  function handleNameChange(value) {
    setName(value);
    setSlug(
      value
        .toLowerCase()
        .trim()
        .replace(/[^a-z0-9]+/g, "-")
        .replace(/(^-|-$)/g, "")
    );
  }

  async function handleCreate() {
    if (!name.trim()) {
      setError("Name is required.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await api.createReferenceDataList({
        name: name.trim(),
        slug: slug.trim(),
        description: description.trim() || null,
        owner: owner.trim() || null,
      });
      onCreated();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="bg-white border border-line rounded-card px-5 py-4 mb-4 flex flex-col gap-2.5 max-w-md">
      <input
        value={name}
        onChange={(e) => handleNameChange(e.target.value)}
        placeholder="List name * (e.g. Department Codes)"
        className="text-[12.5px] border border-line rounded-lg px-3 py-1.5"
      />
      <input
        value={slug}
        readOnly
        placeholder="slug"
        className="text-[11.5px] font-mono border border-line rounded-lg px-3 py-1.5 bg-[#FAFAFB] text-ink-faint"
      />
      <textarea
        value={description}
        onChange={(e) => setDescription(e.target.value)}
        placeholder="Description (optional)"
        rows={2}
        className="text-[12.5px] border border-line rounded-lg px-3 py-1.5"
      />
      <input
        value={owner}
        onChange={(e) => setOwner(e.target.value)}
        placeholder="Owner (optional)"
        className="text-[12.5px] border border-line rounded-lg px-3 py-1.5"
      />
      {error && <div className="text-[11px] text-danger">{error}</div>}
      <div className="flex items-center gap-2 mt-0.5">
        <button
          onClick={handleCreate}
          disabled={busy}
          className="text-[11.5px] font-semibold text-white bg-teal px-3 py-1.5 rounded-lg hover:opacity-90 disabled:opacity-50"
        >
          Create list
        </button>
        <button onClick={onCancel} className="text-[11.5px] font-medium text-ink-faint hover:text-ink px-1">
          Cancel
        </button>
      </div>
    </div>
  );
}

export default function ReferenceData() {
  const { isAuthenticated } = useAuth();
  const [state, setState] = useState({ loading: true, data: null, error: null });
  const [expandedId, setExpandedId] = useState(null);
  const [creating, setCreating] = useState(false);
  const [busy, setBusy] = useState(false);

  async function load() {
    setState((s) => ({ ...s, loading: true }));
    try {
      const res = await api.getReferenceDataLists();
      setState({ loading: false, data: res, error: null });
    } catch (e) {
      setState({ loading: false, data: null, error: e.message });
    }
  }

  useEffect(() => {
    load();
  }, []);

  async function handleDeleteList(list) {
    setBusy(true);
    try {
      const res = await api.deleteReferenceDataList(list.id);
      setState({ loading: false, data: res, error: null });
      if (expandedId === list.id) setExpandedId(null);
    } catch (e) {
      setState((s) => ({ ...s, error: e.message }));
    } finally {
      setBusy(false);
    }
  }

  const lists = state.data?.lists ?? [];

  return (
    <div className="p-7 md:px-8">
      <div className="mb-5 flex items-start justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-xl font-semibold text-ink tracking-tight">Reference Data</h1>
          <p className="text-[13px] text-ink-faint mt-1">
            Named lists of code/label pairs — country codes, currency codes, region codes, and any org-specific
            lists you add. Real, persisted, and editable — not a static mockup.
          </p>
        </div>
        {isAuthenticated && !creating && (
          <button
            onClick={() => setCreating(true)}
            className="text-[12px] font-semibold text-white bg-teal px-3.5 py-2 rounded-lg hover:opacity-90"
          >
            New list
          </button>
        )}
      </div>

      {state.error && (
        <div className="mb-4 text-[12.5px] text-danger bg-danger-soft border border-danger/20 rounded-xl px-4 py-3">
          Couldn't reach the API: {state.error}
        </div>
      )}

      {creating && (
        <CreateListForm
          onCreated={() => {
            setCreating(false);
            load();
          }}
          onCancel={() => setCreating(false)}
        />
      )}

      {!state.loading && lists.length === 0 && !state.error && (
        <div className="bg-white border border-line rounded-card px-6 py-8 text-center">
          <div className="text-[14px] font-semibold text-ink mb-1">No reference lists yet</div>
          <p className="text-[12.5px] text-ink-soft max-w-md mx-auto">
            {isAuthenticated ? 'Click "New list" above to create one.' : "Sign in to create one."}
          </p>
        </div>
      )}

      {lists.length > 0 && (
        <div className="bg-white border border-line rounded-card overflow-hidden">
          <table className="w-full text-left">
            <thead>
              <tr className="border-b border-line bg-[#FAFAFB]">
                <th className="py-2.5 px-5 text-[10.5px] font-bold text-ink-faint uppercase tracking-wide">Name</th>
                <th className="py-2.5 px-3 text-[10.5px] font-bold text-ink-faint uppercase tracking-wide text-right">Values</th>
                <th className="py-2.5 px-3 text-[10.5px] font-bold text-ink-faint uppercase tracking-wide">Owner</th>
                <th className="py-2.5 px-3 text-[10.5px] font-bold text-ink-faint uppercase tracking-wide">Last updated</th>
                <th className="py-2.5 px-5 text-[10.5px] font-bold text-ink-faint uppercase tracking-wide text-right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {lists.map((list) => (
                <Fragment key={list.id}>
                  <tr className="border-b border-line last:border-0">
                    <td className="py-3 px-5">
                      <button
                        onClick={() => setExpandedId(expandedId === list.id ? null : list.id)}
                        className="text-[13px] font-semibold text-ink hover:text-teal text-left"
                      >
                        {list.name}
                      </button>
                      <div className="text-[10.5px] text-ink-faint font-mono">{list.slug}</div>
                    </td>
                    <td className="py-3 px-3 text-right text-[13px] font-mono font-semibold text-ink">{list.value_count}</td>
                    <td className="py-3 px-3 text-[12.5px] text-ink-soft">{list.owner || "—"}</td>
                    <td className="py-3 px-3 text-[12px] font-mono text-ink-faint">{formatWhen(list.updated_at)}</td>
                    <td className="py-3 px-5 text-right whitespace-nowrap">
                      <button
                        onClick={() => setExpandedId(expandedId === list.id ? null : list.id)}
                        className="text-[11.5px] font-semibold text-teal hover:underline mr-3"
                      >
                        {expandedId === list.id ? "Hide values" : "View values"}
                      </button>
                      {isAuthenticated && (
                        <button
                          onClick={() => handleDeleteList(list)}
                          disabled={busy}
                          className="text-[11.5px] font-medium text-ink-faint hover:text-danger"
                        >
                          Delete
                        </button>
                      )}
                    </td>
                  </tr>
                  {expandedId === list.id && (
                    <tr>
                      <td colSpan={5} className="p-0">
                        <ListDetailDrawer
                          list={list}
                          canEdit={isAuthenticated}
                          onListChanged={load}
                          onClose={() => setExpandedId(null)}
                        />
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="mt-4 text-[11.5px] text-ink-faint">
        {state.loading ? "Loading…" : "ISO Country Codes, ISO Currency Codes, and Saudi Administrative Regions ship pre-seeded from real published standards."}
      </div>
    </div>
  );
}
