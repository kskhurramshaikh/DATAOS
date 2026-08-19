# Data Stewardship (Item 3, MDM page group -- Section 04 page 3's
# 4-required-pages list: Golden Record Registry, Duplicate Resolution
# Queue, Field-Level Lineage, Data Stewardship). This is the 4th and
# last of that group.
#
# Scoped 2026-08-19, confirmed with Khurram before building (same
# pattern as Item 6's OpenMetadata resource-wall and Item 3's
# Field-Level Lineage scoping): nothing in this codebase tracked WHO
# owns or stewards a dataset before this -- datasets.uploaded_by is a
# bare integer (always 0, since the dashboard has no login system).
# The Function Specifications v3.0 reference doc names five governance
# roles (Business Owner, Data Owner, Data Steward, Data Custodian, Data
# Consumer); this module tracks real, human-entered assignments of
# those five roles to real datasets -- not simulated or pre-filled data.
# A role with no assignment shows as genuinely unassigned, the same
# "never fabricate" standard field_lineage.py holds for a field with no
# real trace.
#
# Deliberately NOT built (confirmed out of scope, per the scope-
# boundary directive): approval workflows, notifications. RBAC
# enforcement tied to these roles WAS out of scope at the time this
# module was first built (no login existed yet) -- that's since
# closed: assigning/reassigning/unassigning now requires a real
# Keycloak login and an OPA stewardship_assign_allow grant, wired in
# main.py via app/opa_client.py. Assignment itself is still just a
# record of who is responsible, not a workflow system.

from datetime import datetime, timezone

from app.db import get_conn

# Order matters for display -- this is the actual accountability chain
# per the Function Specifications v3.0 doc (Business Owner sets policy,
# Data Owner is accountable for the dataset, Data Steward manages day-
# to-day quality/definitions, Data Custodian handles the technical
# storage, Data Consumer uses the output).
ROLES = ["business_owner", "data_owner", "data_steward", "data_custodian", "data_consumer"]

ROLE_LABELS = {
    "business_owner": "Business Owner",
    "data_owner": "Data Owner",
    "data_steward": "Data Steward",
    "data_custodian": "Data Custodian",
    "data_consumer": "Data Consumer",
}

ROLE_DESCRIPTIONS = {
    "business_owner": "Sets policy and business priorities for this dataset's use.",
    "data_owner": "Accountable for this dataset overall -- its accuracy, access, and lifecycle.",
    "data_steward": "Manages day-to-day data quality, definitions, and issue resolution.",
    "data_custodian": "Handles the technical storage, security, and infrastructure this dataset runs on.",
    "data_consumer": "Uses this dataset's output for analysis, reporting, or downstream decisions.",
}


def _validate_role(role: str) -> None:
    if role not in ROLES:
        raise ValueError(f"Invalid role '{role}' -- must be one of {', '.join(ROLES)}.")


def get_assignments(dataset_safe_name: str) -> dict:
    """The current assignment (or None) for every one of the 5 roles on
    one dataset. Always returns all 5 role keys, even when nothing has
    ever been assigned -- an unassigned role is a real, honest state to
    show, not an absence to hide."""
    if not dataset_safe_name:
        raise ValueError("dataset_name is required.")

    with get_conn() as conn:
        # BUG FOUND VIA TESTCLIENT (2026-08-19), fixed before ship: this
        # query alone can't distinguish "dataset exists, nothing
        # assigned yet" from "dataset doesn't exist at all" -- both
        # return zero rows. Without this existence check, a bogus
        # dataset_name silently returned a fake "5 unassigned roles"
        # response instead of a 404, unlike assign_role/unassign_role
        # below (which already checked). Caught by a real HTTP test
        # against a genuinely nonexistent dataset name, not by reading
        # the code.
        dataset_row = conn.execute(
            "SELECT id FROM datasets WHERE safe_name = ?", (dataset_safe_name,)
        ).fetchone()
        if dataset_row is None:
            raise ValueError(f"No dataset found matching '{dataset_safe_name}'.")

        rows = conn.execute(
            """SELECT role, assignee_name, assignee_email, assigned_by, assigned_at, note
               FROM stewardship_assignments WHERE dataset_safe_name = ?""",
            (dataset_safe_name,),
        ).fetchall()

    by_role = {r["role"]: r for r in rows}
    roles_out = []
    for role in ROLES:
        r = by_role.get(role)
        roles_out.append({
            "role": role,
            "label": ROLE_LABELS[role],
            "description": ROLE_DESCRIPTIONS[role],
            "assigned": r is not None,
            "assignee_name": r["assignee_name"] if r else None,
            "assignee_email": r["assignee_email"] if r else None,
            "assigned_by": r["assigned_by"] if r else None,
            "assigned_at": r["assigned_at"] if r else None,
            "note": r["note"] if r else None,
        })

    return {
        "dataset_name": dataset_safe_name,
        "roles": roles_out,
        "roles_assigned": len(by_role),
        "roles_total": len(ROLES),
    }


def assign_role(payload: dict) -> dict:
    """Assigns (or reassigns) one role for one dataset. Upsert on
    (dataset_safe_name, role) -- a role can only have one current
    assignee at a time, matching how these roles work in practice (one
    accountable person per role, not a list). Reassigning overwrites
    the previous assignee outright; there is no assignment history kept
    here (unlike duplicate_clusters' audit_log) since this module
    tracks current state, not a decision trail -- confirmed as
    sufficient for this scope."""
    dataset_safe_name = payload.get("dataset_name")
    role = payload.get("role")
    assignee_name = payload.get("assignee_name")
    assignee_email = payload.get("assignee_email")
    assigned_by = payload.get("assigned_by")
    note = payload.get("note")

    if not dataset_safe_name:
        raise ValueError("dataset_name is required.")
    _validate_role(role)
    if not assignee_name:
        raise ValueError("assignee_name is required.")
    if not assigned_by:
        raise ValueError("assigned_by is required.")

    with get_conn() as conn:
        dataset_row = conn.execute(
            "SELECT id FROM datasets WHERE safe_name = ?", (dataset_safe_name,)
        ).fetchone()
        if dataset_row is None:
            raise ValueError(f"No dataset found matching '{dataset_safe_name}'.")

        existing = conn.execute(
            "SELECT id FROM stewardship_assignments WHERE dataset_safe_name = ? AND role = ?",
            (dataset_safe_name, role),
        ).fetchone()

        now = datetime.now(timezone.utc).isoformat()
        if existing:
            conn.execute(
                """UPDATE stewardship_assignments
                   SET assignee_name = ?, assignee_email = ?, assigned_by = ?, assigned_at = ?, note = ?
                   WHERE dataset_safe_name = ? AND role = ?""",
                (assignee_name, assignee_email, assigned_by, now, note, dataset_safe_name, role),
            )
        else:
            conn.execute(
                """INSERT INTO stewardship_assignments
                   (dataset_safe_name, role, assignee_name, assignee_email, assigned_by, assigned_at, note)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (dataset_safe_name, role, assignee_name, assignee_email, assigned_by, now, note),
            )
        conn.commit()

    return get_assignments(dataset_safe_name)


def unassign_role(payload: dict) -> dict:
    """Removes the current assignment for one role on one dataset,
    returning it to genuinely unassigned. Idempotent -- unassigning an
    already-unassigned role is a no-op, not an error, since the end
    state either way is "nothing assigned"."""
    dataset_safe_name = payload.get("dataset_name")
    role = payload.get("role")

    if not dataset_safe_name:
        raise ValueError("dataset_name is required.")
    _validate_role(role)

    with get_conn() as conn:
        dataset_row = conn.execute(
            "SELECT id FROM datasets WHERE safe_name = ?", (dataset_safe_name,)
        ).fetchone()
        if dataset_row is None:
            raise ValueError(f"No dataset found matching '{dataset_safe_name}'.")

        conn.execute(
            "DELETE FROM stewardship_assignments WHERE dataset_safe_name = ? AND role = ?",
            (dataset_safe_name, role),
        )
        conn.commit()

    return get_assignments(dataset_safe_name)


def get_coverage_summary() -> dict:
    """Across every dataset, how many of the 5 roles are assigned --
    used for a small at-a-glance summary on the page (e.g. '2 of 3
    datasets have every role assigned') rather than a rebuilt list
    view. Genuinely computed from real assignment rows, not estimated."""
    with get_conn() as conn:
        dataset_rows = conn.execute("SELECT safe_name, display_name FROM datasets").fetchall()
        assignment_rows = conn.execute(
            "SELECT dataset_safe_name, COUNT(*) AS c FROM stewardship_assignments GROUP BY dataset_safe_name"
        ).fetchall()

    counts = {r["dataset_safe_name"]: r["c"] for r in assignment_rows}
    datasets = [
        {
            "dataset_name": r["safe_name"],
            "display_name": r["display_name"],
            "roles_assigned": counts.get(r["safe_name"], 0),
            "roles_total": len(ROLES),
        }
        for r in dataset_rows
    ]
    fully_assigned = sum(1 for d in datasets if d["roles_assigned"] == len(ROLES))
    return {"datasets": datasets, "fully_assigned_count": fully_assigned, "dataset_count": len(datasets)}
