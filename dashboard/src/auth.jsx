// Real login for the dashboard, wired 2026-08-19 -- the backend side
// (Keycloak identity, OPA policy enforcement on the classification and
// stewardship-assign/unassign endpoints) has been live for a while;
// this is what finally makes it reachable from the UI. Reuses the
// exact same /auth/signup and /auth/login endpoints the chat app
// (app/static/index.html) already calls -- same Keycloak-backed
// auth.py on the server, so a login here and a login in chat are
// interchangeable, not two separate user pools.
//
// Deliberately NOT gating the whole dashboard behind login -- every
// existing page keeps working exactly as before for a signed-out
// visitor (matching every other dashboard endpoint's own "deliberately
// unauthenticated" comments in app/main.py). Only the two real policy
// points (Classification's RESTRICTED/CONFIDENTIAL detail, Data
// Stewardship assign/unassign) actually require it -- enforced
// server-side regardless of what this file does, so a missing/wrong
// token there just surfaces as a real 401/403 from the API, handled by
// the calling page, not hidden by a client-side-only gate.

import { createContext, useContext, useEffect, useState } from "react";

const TOKEN_KEY = "dataos_dashboard_token";
const AuthContext = createContext(null);

function decodeRoles(token) {
  try {
    const payload = JSON.parse(atob(token.split(".")[1]));
    return {
      roles: payload.realm_access?.roles || [],
      email: payload.email || payload.preferred_username || null,
      name: payload.given_name || payload.name || payload.preferred_username || null,
    };
  } catch {
    return { roles: [], email: null, name: null };
  }
}

export function AuthProvider({ children }) {
  const [token, setToken] = useState(() => localStorage.getItem(TOKEN_KEY));
  const [claims, setClaims] = useState(() => {
    const existing = localStorage.getItem(TOKEN_KEY);
    return existing ? decodeRoles(existing) : { roles: [], email: null, name: null };
  });

  useEffect(() => {
    if (token) {
      localStorage.setItem(TOKEN_KEY, token);
      setClaims(decodeRoles(token));
    } else {
      localStorage.removeItem(TOKEN_KEY);
      setClaims({ roles: [], email: null, name: null });
    }
  }, [token]);

  async function login(email, password) {
    const res = await fetch("/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || "Login failed.");
    setToken(data.token);
    return data.user;
  }

  async function signup(name, email, password) {
    const res = await fetch("/auth/signup", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, email, password }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || "Signup failed.");
    setToken(data.token);
    return data.user;
  }

  function logout() {
    setToken(null);
  }

  const value = {
    token,
    isAuthenticated: !!token,
    email: claims.email,
    name: claims.name,
    roles: claims.roles,
    login,
    signup,
    logout,
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth() must be used inside an AuthProvider.");
  return ctx;
}

// Small helper for api.js -- reads the token straight out of
// localStorage rather than needing the React context threaded through
// every api.js call site. Kept in sync with AuthProvider's own
// TOKEN_KEY so both always agree on where the token lives.
export function getStoredToken() {
  return localStorage.getItem(TOKEN_KEY);
}
