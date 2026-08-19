// Manage Users -- real Keycloak user/role administration, wired
// 2026-08-19. Closes the "no admin-promotion UI" gap left by the
// RBAC/OPA build: before this, promoting a user off the self-signup
// default (data_consumer) to a real role required going into
// Keycloak's own admin console directly. See app/adapters/
// user_admin_adapter.py's module docstring for the backend side.
//
// Admin-only -- the backend's own /api/admin/* routes 403 for anyone
// without the admin role, so this page shows a plain "not available"
// state rather than a broken/empty table for a non-admin who lands
// here directly (e.g. a stale bookmark after a role change).

import { useEffect, useState } from "react";
import { api } from "../api";
import { useAuth } from "../auth";

const ROLE_LABELS = {
  admin: "Admin",
  business_owner: "Business Owner",
  data_owner: "Data Owner",
  data_steward: "Data Steward",
  data_custodian: "Data Custodian",
  data_consumer: "Data Consumer",
};

function RoleToggle({ role, active, onToggle, busy }) {
  return (
    <button
      onClick={() => onToggle(role, active)}
      disabled={busy}
      className={`text-[10.5px] font-semibold px-2 py-0.5 rounded-full transition-opacity hover:opacity-80 disabled:opacity-40 ${
        active ? "text-teal bg-teal-soft" : "text-ink-faint bg-[#F2F2F4]"
      }`}
    >
      {ROLE_LABELS[role] || role}
    </button>
  );
}

function UserRow({ user, roles, onToggleRole, busyUserId }) {
  const busy = busyUserId === user.id;
  return (
    <div className="flex items-center justify-between gap-4 py-3 border-b border-line last:border-0">
      <div className="min-w-0">
        <div className="text-[12.5px] font-medium text-ink">{user.name}</div>
        <div className="text-[11.5px] text-ink-faint">{user.email}</div>
      </div>
      <div className="flex flex-wrap gap-1.5 justify-end">
        {roles.map((role) => (
          <RoleToggle
            key={role}
            role={role}
            active={user.roles.includes(role)}
            onToggle={(r, isActive) => onToggleRole(user.id, r, isActive)}
            busy={busy}
          />
        ))}
      </div>
    </div>
  );
}

export default function ManageUsers() {
  const { roles: myRoles } = useAuth();
  const isAdmin = myRoles.includes("admin");
  const [state, setState] = useState({ loading: true, users: [], roles: [], error: null });
  const [busyUserId, setBusyUserId] = useState(null);

  async function load() {
    setState((s) => ({ ...s, loading: true }));
    try {
      const [usersRes, rolesRes] = await Promise.all([api.getAdminUsers(), api.getAdminRoles()]);
      setState({ loading: false, users: usersRes.users, roles: rolesRes.roles, error: null });
    } catch (e) {
      setState({ loading: false, users: [], roles: [], error: e.message });
    }
  }

  useEffect(() => {
    if (isAdmin) load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isAdmin]);

  async function handleToggleRole(userId, role, isActive) {
    setBusyUserId(userId);
    try {
      const res = isActive ? await api.removeUserRole(userId, role) : await api.assignUserRole(userId, role);
      setState((s) => ({ ...s, users: res.users, error: null }));
    } catch (e) {
      setState((s) => ({ ...s, error: e.message }));
    } finally {
      setBusyUserId(null);
    }
  }

  if (!isAdmin) {
    return (
      <div className="p-7 md:px-8">
        <h1 className="text-xl font-semibold text-ink tracking-tight mb-2">Manage Users</h1>
        <div className="text-[12.5px] text-ink-faint bg-[#FAFAFB] border border-line rounded-xl px-4 py-3">
          This page is only available to admins.
        </div>
      </div>
    );
  }

  return (
    <div className="p-7 md:px-8">
      <div className="mb-5">
        <h1 className="text-xl font-semibold text-ink tracking-tight">Manage Users</h1>
        <p className="text-[13px] text-ink-faint mt-1">
          Real Keycloak realm roles -- click a role to grant or revoke it. Everyone starts as a Data
          Consumer from self-signup.
        </p>
      </div>

      {state.error && (
        <div className="mb-4 text-[12.5px] text-danger bg-danger-soft border border-danger/20 rounded-xl px-4 py-3">
          {state.error}
        </div>
      )}

      {state.loading ? (
        <div className="text-[12.5px] text-ink-faint">Loading…</div>
      ) : (
        <div className="bg-white border border-line rounded-card px-6 py-4">
          <div className="text-[11px] font-bold text-ink-faint uppercase tracking-wide mb-2">
            {state.users.length} {state.users.length === 1 ? "user" : "users"}
          </div>
          {state.users.length === 0 ? (
            <div className="text-[12.5px] text-ink-faint py-3">No users have signed up yet.</div>
          ) : (
            state.users.map((user) => (
              <UserRow
                key={user.id}
                user={user}
                roles={state.roles}
                onToggleRole={handleToggleRole}
                busyUserId={busyUserId}
              />
            ))
          )}
        </div>
      )}
    </div>
  );
}
