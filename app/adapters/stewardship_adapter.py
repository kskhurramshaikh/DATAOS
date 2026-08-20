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

from app import db as db_module
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


# ---------------------------------------------------------------------
# Stewardship Policy Wizard (2026-08-20) -- closes the "no policy
# wizard anywhere on the page" gap. A policy is a separate, real
# concern from role assignments above: WHO is accountable (roles) vs
# WHAT the accountability rules actually are for this dataset
# (retention period, review cadence, a real quality bar, who to
# escalate to). One current policy per dataset (UNIQUE on
# dataset_safe_name) -- same "current state, not a history" reasoning
# assign_role() above already applies; reconfiguring overwrites the
# previous policy outright.
#
# SCHEMA LOCATION NOTE (same as ndi_history.py/sama_history.py/
# policy_documents_adapter.py): self-contained _ensure_policy_schema()
# rather than living in db.py's init_db() alongside stewardship_
# assignments -- this table is read/written only by the two functions
# below, and _ensure_policy_schema() is idempotent on both backends.
# ---------------------------------------------------------------------

POLICY_REVIEW_FREQUENCIES = ["monthly", "quarterly", "semi_annual", "annual"]
POLICY_REVIEW_FREQUENCY_LABELS = {
    "monthly": "Monthly",
    "quarterly": "Quarterly",
    "semi_annual": "Semi-annual",
    "annual": "Annual",
}


def _ensure_policy_schema():
    pk = "SERIAL PRIMARY KEY" if db_module._is_postgres() else "INTEGER PRIMARY KEY AUTOINCREMENT"
    with get_conn() as conn:
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS stewardship_policies (
                id {pk},
                dataset_safe_name TEXT NOT NULL UNIQUE,
                retention_period_days INTEGER,
                review_frequency TEXT,
                quality_threshold_pct REAL,
                escalation_contact TEXT,
                notes TEXT,
                set_by TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.commit()


def get_policy(dataset_safe_name: str) -> dict:
    """The current stewardship policy for one dataset, or the honest
    "not yet configured" state if none has been set -- never a
    fabricated default. Validates the dataset itself exists first,
    same existence-check bug class get_assignments() above already
    guards against."""
    if not dataset_safe_name:
        raise ValueError("dataset_name is required.")
    with get_conn() as conn:
        dataset_row = conn.execute(
            "SELECT id FROM datasets WHERE safe_name = ?", (dataset_safe_name,)
        ).fetchone()
        if dataset_row is None:
            raise ValueError(f"No dataset found matching '{dataset_safe_name}'.")

    _ensure_policy_schema()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM stewardship_policies WHERE dataset_safe_name = ?", (dataset_safe_name,)
        ).fetchone()

    if row is None:
        return {
            "dataset_name": dataset_safe_name,
            "configured": False,
            "retention_period_days": None,
            "review_frequency": None,
            "review_frequency_label": None,
            "quality_threshold_pct": None,
            "escalation_contact": None,
            "notes": None,
            "set_by": None,
            "updated_at": None,
        }

    return {
        "dataset_name": dataset_safe_name,
        "configured": True,
        "retention_period_days": row["retention_period_days"],
        "review_frequency": row["review_frequency"],
        "review_frequency_label": POLICY_REVIEW_FREQUENCY_LABELS.get(row["review_frequency"]),
        "quality_threshold_pct": row["quality_threshold_pct"],
        "escalation_contact": row["escalation_contact"],
        "notes": row["notes"],
        "set_by": row["set_by"],
        "updated_at": row["updated_at"],
    }


def set_policy(payload: dict) -> dict:
    """Creates or replaces the stewardship policy for one dataset --
    upsert on dataset_safe_name. Validates every field genuinely
    (retention_period_days a positive whole number of days if given,
    review_frequency one of the 4 real cadences, quality_threshold_pct
    between 0 and 100) rather than storing whatever was typed in."""
    dataset_safe_name = payload.get("dataset_name")
    if not dataset_safe_name:
        raise ValueError("dataset_name is required.")
    set_by = payload.get("set_by")
    if not set_by:
        raise ValueError("set_by is required.")

    retention_period_days = payload.get("retention_period_days")
    if retention_period_days is not None and retention_period_days != "":
        try:
            retention_period_days = int(retention_period_days)
        except (TypeError, ValueError):
            raise ValueError("retention_period_days must be a whole number of days.")
        if retention_period_days <= 0:
            raise ValueError("retention_period_days must be a positive number of days.")
    else:
        retention_period_days = None

    review_frequency = payload.get("review_frequency") or None
    if review_frequency is not None and review_frequency not in POLICY_REVIEW_FREQUENCIES:
        raise ValueError(f"review_frequency must be one of {', '.join(POLICY_REVIEW_FREQUENCIES)}.")

    quality_threshold_pct = payload.get("quality_threshold_pct")
    if quality_threshold_pct is not None and quality_threshold_pct != "":
        try:
            quality_threshold_pct = float(quality_threshold_pct)
        except (TypeError, ValueError):
            raise ValueError("quality_threshold_pct must be a number.")
        if not (0 <= quality_threshold_pct <= 100):
            raise ValueError("quality_threshold_pct must be between 0 and 100.")
    else:
        quality_threshold_pct = None

    escalation_contact = (payload.get("escalation_contact") or "").strip() or None
    notes = (payload.get("notes") or "").strip() or None

    with get_conn() as conn:
        dataset_row = conn.execute(
            "SELECT id FROM datasets WHERE safe_name = ?", (dataset_safe_name,)
        ).fetchone()
        if dataset_row is None:
            raise ValueError(f"No dataset found matching '{dataset_safe_name}'.")

    _ensure_policy_schema()
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT id FROM stewardship_policies WHERE dataset_safe_name = ?", (dataset_safe_name,)
        ).fetchone()
        if existing:
            conn.execute(
                """UPDATE stewardship_policies
                   SET retention_period_days = ?, review_frequency = ?, quality_threshold_pct = ?,
                       escalation_contact = ?, notes = ?, set_by = ?, updated_at = ?
                   WHERE dataset_safe_name = ?""",
                (
                    retention_period_days, review_frequency, quality_threshold_pct,
                    escalation_contact, notes, set_by, now, dataset_safe_name,
                ),
            )
        else:
            conn.execute(
                """INSERT INTO stewardship_policies
                   (dataset_safe_name, retention_period_days, review_frequency, quality_threshold_pct,
                    escalation_contact, notes, set_by, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    dataset_safe_name, retention_period_days, review_frequency, quality_threshold_pct,
                    escalation_contact, notes, set_by, now,
                ),
            )
        conn.commit()

    return get_policy(dataset_safe_name)


# ---------------------------------------------------------------------
# Data Stewardship Task Assignment (2026-08-20) -- closes the last
# gap named against this page: "task assignment" (see thread 08's
# scoping note, confirmed at build time: "Data Stewardship is a
# genuinely fresh build (policy wizard, task assignment, review
# ownership)"). Distinct from BOTH things already on this page:
# - Role assignment above answers WHO holds each of the 5 standing
#   governance roles for a dataset (long-lived, one person per role).
# - The Policy Wizard answers WHAT the accountability rules are
#   (retention, cadence, quality bar) -- policy, not action items.
# - Tasks here answer WHAT NEEDS DOING, RIGHT NOW, BY WHOM, BY WHEN --
#   short-lived, many-per-dataset, explicitly not tied to a role slot
#   (the assignee is a free-text name/email, same as role assignment,
#   not required to be whoever currently holds a role -- a task can be
#   handed to anyone).
#
# Deliberately NOT built, same scope line the module docstring above
# already draws for role assignment: no approval workflows, no
# notifications, no due-date reminders or escalation logic. A task is
# a plain record with a status a human moves themselves (open ->
# in_progress -> done, or -> cancelled) -- there is no automation
# behind that transition and nothing fires when it changes. This
# mirrors "never fabricate a workflow that isn't real" the same way
# Field-Level Lineage and Data Stewardship's role state do.
TASK_STATUSES = ["open", "in_progress", "done", "cancelled"]

TASK_STATUS_LABELS = {
    "open": "Open",
    "in_progress": "In Progress",
    "done": "Done",
    "cancelled": "Cancelled",
}


def _validate_task_status(status: str) -> None:
    if status not in TASK_STATUSES:
        raise ValueError(f"Invalid status '{status}' -- must be one of {', '.join(TASK_STATUSES)}.")


def _ensure_task_schema():
    pk = "SERIAL PRIMARY KEY" if db_module._is_postgres() else "INTEGER PRIMARY KEY AUTOINCREMENT"
    with get_conn() as conn:
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS stewardship_tasks (
                id {pk},
                dataset_safe_name TEXT NOT NULL,
                title TEXT NOT NULL,
                assignee_name TEXT NOT NULL,
                assignee_email TEXT,
                due_date TEXT,
                status TEXT NOT NULL DEFAULT 'open',
                notes TEXT,
                created_by TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.commit()


def list_tasks(dataset_safe_name: str) -> dict:
    """Every task ever created for one dataset, newest first --
    open/in-progress tasks surfaced ahead of done/cancelled ones so
    the page leads with what still needs attention, matching the same
    "what's outstanding first" ordering the Duplicate Queue and Audit
    Log pages already use. Validates the dataset exists first, same
    existence-check pattern get_assignments/get_policy above already
    follow -- a bogus dataset_name gets a real 404, not a fake empty
    list."""
    if not dataset_safe_name:
        raise ValueError("dataset_name is required.")
    with get_conn() as conn:
        dataset_row = conn.execute(
            "SELECT id FROM datasets WHERE safe_name = ?", (dataset_safe_name,)
        ).fetchone()
        if dataset_row is None:
            raise ValueError(f"No dataset found matching '{dataset_safe_name}'.")

    _ensure_task_schema()
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT id, title, assignee_name, assignee_email, due_date, status, notes,
                      created_by, created_at, updated_at
               FROM stewardship_tasks WHERE dataset_safe_name = ?
               ORDER BY (status IN ('done', 'cancelled')) ASC, id DESC""",
            (dataset_safe_name,),
        ).fetchall()

    tasks = [
        {
            "id": r["id"],
            "title": r["title"],
            "assignee_name": r["assignee_name"],
            "assignee_email": r["assignee_email"],
            "due_date": r["due_date"],
            "status": r["status"],
            "status_label": TASK_STATUS_LABELS.get(r["status"], r["status"]),
            "notes": r["notes"],
            "created_by": r["created_by"],
            "created_at": r["created_at"],
            "updated_at": r["updated_at"],
        }
        for r in rows
    ]
    open_count = sum(1 for t in tasks if t["status"] in ("open", "in_progress"))

    return {
        "dataset_name": dataset_safe_name,
        "tasks": tasks,
        "tasks_total": len(tasks),
        "tasks_open": open_count,
    }


def create_task(payload: dict) -> dict:
    """Creates a new task for one dataset. title and assignee_name are
    the only required fields -- due_date and notes are optional, same
    "don't force structure that isn't there yet" posture as the Policy
    Wizard's optional fields."""
    dataset_safe_name = payload.get("dataset_name")
    title = (payload.get("title") or "").strip()
    assignee_name = (payload.get("assignee_name") or "").strip()
    assignee_email = (payload.get("assignee_email") or "").strip() or None
    due_date = (payload.get("due_date") or "").strip() or None
    notes = (payload.get("notes") or "").strip() or None
    created_by = payload.get("created_by")

    if not dataset_safe_name:
        raise ValueError("dataset_name is required.")
    if not title:
        raise ValueError("title is required.")
    if not assignee_name:
        raise ValueError("assignee_name is required.")
    if not created_by:
        raise ValueError("created_by is required.")

    with get_conn() as conn:
        dataset_row = conn.execute(
            "SELECT id FROM datasets WHERE safe_name = ?", (dataset_safe_name,)
        ).fetchone()
        if dataset_row is None:
            raise ValueError(f"No dataset found matching '{dataset_safe_name}'.")

    _ensure_task_schema()
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO stewardship_tasks
               (dataset_safe_name, title, assignee_name, assignee_email, due_date, status,
                notes, created_by, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, 'open', ?, ?, ?, ?)""",
            (dataset_safe_name, title, assignee_name, assignee_email, due_date, notes,
             created_by, now, now),
        )
        conn.commit()

    return list_tasks(dataset_safe_name)


def update_task_status(payload: dict) -> dict:
    """Moves one task to a new status -- the only mutation a task
    supports after creation, deliberately (see module docstring: no
    approval workflow, no editing title/assignee after the fact). A
    human moves the status themselves; nothing here validates a
    transition sequence (open -> done directly is fine, so is
    reopening a cancelled task) since this tracks a real person's own
    judgment call, not an enforced state machine."""
    dataset_safe_name = payload.get("dataset_name")
    task_id = payload.get("task_id")
    status = payload.get("status")

    if not dataset_safe_name:
        raise ValueError("dataset_name is required.")
    if task_id is None:
        raise ValueError("task_id is required.")
    _validate_task_status(status)

    with get_conn() as conn:
        existing = conn.execute(
            "SELECT id FROM stewardship_tasks WHERE id = ? AND dataset_safe_name = ?",
            (task_id, dataset_safe_name),
        ).fetchone()
        if existing is None:
            raise ValueError(f"No task {task_id} found for dataset '{dataset_safe_name}'.")

        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "UPDATE stewardship_tasks SET status = ?, updated_at = ? WHERE id = ?",
            (status, now, task_id),
        )
        conn.commit()

    return list_tasks(dataset_safe_name)


def delete_task(payload: dict) -> dict:
    """Deletes one task outright -- for a genuinely mistaken entry,
    not for closing out finished work (that's update_task_status ->
    'done', which keeps the record). Idempotent-ish: deleting an
    already-gone task_id is a no-op rather than an error, same
    posture as unassign_role above, since the end state either way is
    "task not present"."""
    dataset_safe_name = payload.get("dataset_name")
    task_id = payload.get("task_id")

    if not dataset_safe_name:
        raise ValueError("dataset_name is required.")
    if task_id is None:
        raise ValueError("task_id is required.")

    with get_conn() as conn:
        conn.execute(
            "DELETE FROM stewardship_tasks WHERE id = ? AND dataset_safe_name = ?",
            (task_id, dataset_safe_name),
        )
        conn.commit()

    return list_tasks(dataset_safe_name)
