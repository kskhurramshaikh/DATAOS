# Real OPA policy enforcement client, wired in 2026-08-19.
#
# WHY THIS EXISTS: classification_adapter.py and stewardship_adapter.py
# both originally shipped with an explicit, disclosed deferral -- OPA
# policy enforcement (blocking/allowing based on role) was documented
# as "not built... requires authenticated users and roles to enforce
# against, which don't exist on this dashboard yet." That was honest
# at the time. It's no longer true: Keycloak (real login, real roles)
# and OPA (real policy service, real .rego rules) are both live now --
# see /areas/onetech-dataos-rbac-opa-keycloak.md for the full build
# history. This module is what actually closes that gap: a thin client
# that calls OPA's already-deployed policy service, rather than
# reimplementing the same role logic inline in main.py a second time.
#
# The two OPA rules this calls (classification_allow,
# stewardship_assign_allow) were written and deployed during the OPA
# build -- see that same memory file for their exact content. Both
# take a single input shape: {"role": "<one Keycloak realm role>"}.
#
# FAIL-CLOSED, DELIBERATELY: if OPA is unreachable or returns anything
# other than a clean 200 with a boolean result, this returns False
# (deny), never True. A policy engine that fails open on a network
# blip would make actual enforcement worse than the honest "deferred"
# state this replaces -- silently permissive is worse than visibly
# broken.

import os

import requests

OPA_URL = os.environ.get("OPA_URL", "https://dataos-opa.onrender.com")


def _query_opa(rule: str, role: str) -> bool:
    try:
        r = requests.post(
            f"{OPA_URL}/v1/data/dataos/authz/{rule}",
            json={"input": {"role": role}},
            timeout=5,
        )
    except requests.RequestException:
        return False
    if r.status_code != 200:
        return False
    try:
        return bool(r.json().get("result", False))
    except ValueError:
        return False


def is_allowed(rule: str, roles: list[str]) -> bool:
    """True if ANY of the user's Keycloak realm roles satisfies the
    named OPA rule -- a user can carry more than one realm role, and
    the policy question is "can this person do it," not "does their
    first-listed role allow it." `admin` is not special-cased here;
    OPA's own deployed policy already grants admin on every rule (see
    the .rego content in memory), so this stays a pure pass-through to
    the real policy rather than a second, parallel admin bypass."""
    return any(_query_opa(rule, role) for role in roles)
