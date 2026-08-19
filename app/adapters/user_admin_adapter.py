# User & role administration -- real Keycloak Admin REST API calls,
# wired 2026-08-19 as the natural follow-on to the RBAC/OPA build (see
# /areas/onetech-dataos-rbac-opa-keycloak.md). Before this, promoting a
# user off the self-signup default (data_consumer) to a real role
# (data_owner, admin, etc.) required going into Keycloak's own admin
# console directly -- this module and the /api/admin/* routes in
# main.py are what let that happen from inside DataOS instead, without
# ever touching Keycloak's UI.
#
# DELIBERATELY admin-only, checked directly in main.py (not via
# opa_client.is_allowed), not one of the two OPA policy points this
# RBAC build otherwise gates (classification_allow,
# stewardship_assign_allow). Account administration -- who can grant
# roles to other people -- is qualitatively different from a data
# governance decision: it's the root of the whole permission system,
# not a decision made within it. Every existing OPA rule already
# grants `admin` unconditionally (see the deployed .rego policy), so a
# direct `"admin" in user["roles"]` check here is the same authority
# level OPA would grant anyway, just without the extra network hop for
# a case that has no genuine nuance -- either you're admin or you're
# not, there's no equivalent of "data_owner can also assign
# stewardship" for user administration.
#
# REAL ROLES ONLY, deliberately: Keycloak realms carry a handful of
# built-in technical roles alongside the 6 real ones this project
# created (offline_access, uma_authorization, default-roles-dataos --
# visible on the Account page's own "Realm roles" list for any signed-
# in user). This module filters every listing/mutation down to
# MANAGED_ROLES so an admin never sees or accidentally touches
# Keycloak's own plumbing roles through this UI.

import requests

from app import auth

MANAGED_ROLES = ["admin", "business_owner", "data_owner", "data_steward", "data_custodian", "data_consumer"]


def _admin_headers() -> dict:
    return {"Authorization": f"Bearer {auth._get_admin_token()}"}


def list_users() -> dict:
    """Every real user in the realm, each with their current managed
    realm roles. Two real API calls per user (list, then that user's
    own role-mappings) -- Keycloak's bulk user-list endpoint doesn't
    include role mappings inline, confirmed against its own Admin REST
    API docs rather than assumed."""
    r = requests.get(f"{auth._ADMIN_BASE}/users", headers=_admin_headers(), params={"max": 200}, timeout=10)
    if r.status_code != 200:
        raise RuntimeError(f"Could not list users from Keycloak (status {r.status_code}).")

    users_out = []
    for u in r.json():
        rr = requests.get(
            f"{auth._ADMIN_BASE}/users/{u['id']}/role-mappings/realm",
            headers=_admin_headers(), timeout=10,
        )
        role_names = [role["name"] for role in rr.json()] if rr.status_code == 200 else []
        managed_roles = [r_ for r_ in role_names if r_ in MANAGED_ROLES]
        users_out.append({
            "id": u["id"],
            "email": u.get("email"),
            "name": u.get("firstName") or u.get("username"),
            "enabled": u.get("enabled", True),
            "roles": managed_roles,
        })

    # Real users first (has at least one managed role or a real email),
    # alphabetical by email -- keeps the list usable as it grows,
    # rather than whatever order Keycloak happens to return.
    users_out.sort(key=lambda u: (u["email"] or "").lower())
    return {"users": users_out, "user_count": len(users_out)}


def _get_role_object(role_name: str) -> dict:
    if role_name not in MANAGED_ROLES:
        raise ValueError(f"'{role_name}' is not a managed role -- must be one of {', '.join(MANAGED_ROLES)}.")
    r = requests.get(f"{auth._ADMIN_BASE}/roles/{role_name}", headers=_admin_headers(), timeout=10)
    if r.status_code != 200:
        raise RuntimeError(f"Could not look up role '{role_name}' in Keycloak (status {r.status_code}).")
    return r.json()


def assign_role(user_id: str, role_name: str) -> None:
    """Grants one realm role to one user. Idempotent in effect --
    Keycloak's own role-mappings POST doesn't error on a role the user
    already has, so no pre-check needed here."""
    role_obj = _get_role_object(role_name)
    r = requests.post(
        f"{auth._ADMIN_BASE}/users/{user_id}/role-mappings/realm",
        json=[role_obj],
        headers=_admin_headers(), timeout=10,
    )
    if r.status_code not in (200, 204):
        raise RuntimeError(f"Could not assign role '{role_name}' (status {r.status_code}).")


def remove_role(user_id: str, role_name: str) -> None:
    """Revokes one realm role from one user. Idempotent -- removing a
    role the user doesn't have is a no-op on Keycloak's side, not an
    error, same principle as stewardship_adapter's unassign_role()."""
    role_obj = _get_role_object(role_name)
    r = requests.delete(
        f"{auth._ADMIN_BASE}/users/{user_id}/role-mappings/realm",
        json=[role_obj],
        headers=_admin_headers(), timeout=10,
    )
    if r.status_code not in (200, 204):
        raise RuntimeError(f"Could not remove role '{role_name}' (status {r.status_code}).")
