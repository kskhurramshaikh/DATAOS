// Real login/signup page for the dashboard, wired 2026-08-19 -- see
// auth.jsx's module docstring for the full reasoning. Same visual
// conventions as every other dashboard page (rounded-card white
// panels on canvas, teal accent, ink/ink-faint text scale).

import { useState } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../auth";

const ROLE_LABELS = {
  admin: "Admin",
  business_owner: "Business Owner",
  data_owner: "Data Owner",
  data_steward: "Data Steward",
  data_custodian: "Data Custodian",
  data_consumer: "Data Consumer",
};

export default function Account() {
  const { isAuthenticated, email, name, roles, login, signup, logout } = useAuth();
  const [mode, setMode] = useState("login"); // "login" | "signup"
  const [form, setForm] = useState({ name: "", email: "", password: "" });
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  function updateField(key, value) {
    setForm((f) => ({ ...f, [key]: value }));
  }

  async function submit(e) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      if (mode === "login") {
        await login(form.email, form.password);
      } else {
        await signup(form.name, form.email, form.password);
      }
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  if (isAuthenticated) {
    const isAdmin = roles.includes("admin");
    return (
      <div className="p-7 md:px-8 max-w-md">
        <h1 className="text-xl font-semibold text-ink tracking-tight mb-1">Account</h1>
        <p className="text-[13px] text-ink-faint mb-5">
          Signed in -- this is what governs access to Classification detail and Data Stewardship
          assignment, the two pages with real role-based access control.
        </p>
        <div className="bg-white border border-line rounded-card px-6 py-5">
          <div className="text-[13px] font-semibold text-ink">{name}</div>
          <div className="text-[12px] text-ink-faint mt-0.5">{email}</div>
          <div className="mt-3">
            <div className="text-[11px] font-bold text-ink-faint uppercase tracking-wide mb-1.5">
              Realm roles
            </div>
            {roles.length ? (
              <div className="flex flex-wrap gap-1.5">
                {roles.map((r) => (
                  <span
                    key={r}
                    className="text-[10.5px] font-semibold text-teal bg-teal-soft px-2 py-0.5 rounded-full"
                  >
                    {ROLE_LABELS[r] || r}
                  </span>
                ))}
              </div>
            ) : (
              <div className="text-[12px] text-ink-faint">
                No realm role assigned yet -- an admin can grant one from Manage Users.
              </div>
            )}
          </div>
        </div>

        {/* Manage Users link, wired 2026-08-19 -- see
            ManageUsers.jsx's module docstring. Only rendered for a
            real admin, matching the backend's own admin-only gate on
            every /api/admin/* route -- a non-admin would just get a
            403 from every call on that page, so there's no reason to
            show the entry point to them at all. */}
        {isAdmin && (
          <Link
            to="/admin/users"
            className="mt-4 inline-block text-[12.5px] font-semibold text-teal bg-teal-soft px-3.5 py-2 rounded-lg hover:opacity-80"
          >
            Manage Users →
          </Link>
        )}

        <div>
          <button
            onClick={logout}
            className="mt-4 text-[12.5px] font-semibold text-danger bg-danger-soft px-3.5 py-2 rounded-lg hover:opacity-80"
          >
            Log out
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="p-7 md:px-8 max-w-sm">
      <h1 className="text-xl font-semibold text-ink tracking-tight mb-1">
        {mode === "login" ? "Sign in" : "Create an account"}
      </h1>
      <p className="text-[13px] text-ink-faint mb-5">
        {mode === "login"
          ? "Most of this dashboard works without signing in. Classification detail and Data Stewardship assignment need a real role."
          : "New accounts start with the least-privileged role (Data Consumer). An admin can promote you from Manage Users."}
      </p>
      <form onSubmit={submit} className="bg-white border border-line rounded-card px-6 py-5 flex flex-col gap-3">
        {mode === "signup" && (
          <input
            value={form.name}
            onChange={(e) => updateField("name", e.target.value)}
            placeholder="Name"
            required
            className="text-[12.5px] border border-line rounded-lg px-3 py-2"
          />
        )}
        <input
          type="email"
          value={form.email}
          onChange={(e) => updateField("email", e.target.value)}
          placeholder="Email"
          required
          className="text-[12.5px] border border-line rounded-lg px-3 py-2"
        />
        <input
          type="password"
          value={form.password}
          onChange={(e) => updateField("password", e.target.value)}
          placeholder="Password"
          required
          className="text-[12.5px] border border-line rounded-lg px-3 py-2"
        />
        {error && <div className="text-[11.5px] text-danger">{error}</div>}
        <button
          type="submit"
          disabled={busy}
          className="text-[12.5px] font-semibold text-white bg-teal px-3.5 py-2 rounded-lg hover:opacity-90 disabled:opacity-50"
        >
          {busy ? "Please wait…" : mode === "login" ? "Sign in" : "Create account"}
        </button>
      </form>
      <button
        onClick={() => {
          setMode(mode === "login" ? "signup" : "login");
          setError(null);
        }}
        className="mt-3 text-[12px] text-ink-faint hover:text-ink"
      >
        {mode === "login" ? "New here? Create an account" : "Already have an account? Sign in"}
      </button>
    </div>
  );
}
