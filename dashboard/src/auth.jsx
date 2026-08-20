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
//
// Expiry check (2026-08-20) -- real bug fix. Keycloak's access tokens
// here are short-lived (10 minutes as of 2026-08-20, raised from the
// realm's original 5-minute default via the Keycloak Admin REST API --
// see the realm's accessTokenLifespan setting) and this app has no
// refresh-token flow, so a token still goes stale within one longer
// session even at the higher figure. Previously isAuthenticated was
// just `!!token` -- presence, not validity -- so the Account page kept
// showing "Signed in" for many minutes (even across a page reload)
// after the token had actually expired, and the person only found out
// when a write silently failed with the API's raw "Invalid or expired
// token." error, deep inside whatever form they were filling out.
// Confirmed live: filling the 3-step Stewardship Policy Wizard alone
// took long enough to cross a fresh token's own expiry.
//
// Fix: decode the JWT's `exp` claim (seconds since epoch, standard
// JWT field) and compare it to the current time on every read, not
// just once at login. isExpired() is checked (a) on initial load from
// localStorage, so a stale token left over from a previous session
// never reports as signed-in, and (b) periodically while the tab stays
// open (isTokenValid's setInterval below), so a session that goes
// stale mid-use flips the UI to signed-out within a few seconds
// instead of silently staying "Signed in" until the person happens to
// submit something. A token that fails to decode at all (malformed,
// truncated) is treated as expired/invalid, not as a crash -- same
// fail-closed posture as the server-side OPA checks this gates.
import { createContext, useContext, useEffect, useState } from "react";

function isTokenExpired(token) {
  if (!token) return true;
  try {
    const payload = JSON.parse(atob(token.split(".")[1]));
    if (!payload.exp) return true;
    // 5s clock-skew buffer so a token that's about to expire doesn't
    // get treated as valid for a request that then lands just after.
    return payload.exp * 1000 < Date.now() + 5000;
  } catch {
    return true;
  }
}

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

// Reads localStorage and immediately discards anything already
// expired, rather than trusting presence alone -- used both for the
// initial useState() seed below and by the periodic re-check.
function readValidToken() {
  const existing = localStorage.getItem(TOKEN_KEY);
  if (existing && isTokenExpired(existing)) {
    localStorage.removeItem(TOKEN_KEY);
    return null;
  }
  return existing;
}

export function AuthProvider({ children }) {
  const [token, setToken] = useState(() => readValidToken());
  const [claims, setClaims] = useState(() => {
    const existing = readValidToken();
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

  // Periodic liveness check (2026-08-20) -- catches a token expiring
  // WHILE the tab is open and idle on an authenticated page, not just
  // at the next full page load. 15s interval is frequent enough that
  // the "Signed in" state never drifts far from reality, cheap enough
  // (a JWT decode, no network call) not to matter running in the
  // background.
  useEffect(() => {
    const id = setInterval(() => {
      setToken((current) => (current && isTokenExpired(current) ? null : current));
    }, 15000);
    return () => clearInterval(id);
  }, []);

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
//
// Also expiry-checked now (2026-08-20), same as readValidToken() above
// -- api.js's authHeaders() calls this directly, so without this check
// a stale token would still get attached to outgoing requests and
// produce the exact raw-401 "Invalid or expired token." failure this
// whole fix is closing.
export function getStoredToken() {
  const token = localStorage.getItem(TOKEN_KEY);
  if (token && isTokenExpired(token)) {
    localStorage.removeItem(TOKEN_KEY);
    return null;
  }
  return token;
}
