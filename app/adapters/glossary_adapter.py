# Business Glossary (2026-08-20) -- closes the last of the Directive's
# four named Data Catalog requirements: "Browsable dataset list,
# schema, business glossary, lineage graph" (quoted directly from
# DataOS2_Master_Requirements_and_Architecture_Directive.pdf, page 3,
# Section 04, Data Catalog row -- data source named there is
# "OpenMetadata + OpenLineage/Marquez"). The other three shipped
# 2026-08-19/20; this is the remaining gap.
#
# WHY NOT OPENMETADATA: same resource-wall reasoning that ruled it out
# for the rest of Data Catalog (see marquez_client.py's module
# docstring) -- confirmed again directly against a real OpenMetadata
# docker-compose from a reference platform: MySQL(1GB) +
# Elasticsearch(2GB) + Server(2GB) + Ingestion/Airflow(2GB) = 7GB of
# memory limits across 4 separate containers, just for the glossary
# feature alone. Not proportionate to what this needs.
#
# DESIGN, informed by that same reference platform's own glossary
# implementation (which also skips OpenMetadata entirely): a domain
# category plus a free-form tags list, where the first tag mirrors the
# domain and additional tags are soft, searchable associations -- a
# real dataset/column name used as a tag is a convention, not an
# enforced foreign key. This lands deliberately between "flat
# dictionary" (too little) and "full OpenMetadata entity-tagging
# graph" (the resource-wall problem all over again): close to a real
# metadata tool's *feel* without the infrastructure it demands.
#
# SCOPE: global (org-level), not per-dataset -- a business term like
# "NDI_SCORE" means the same thing regardless of which dataset is
# selected elsewhere on the page, same "org-level artifact" reasoning
# policy_documents_adapter.py already applies to policy documents.
#
# tags stored as JSON text, not a native Postgres array -- this
# codebase runs on SQLite in tests/CI and Postgres in production (see
# db.py's _is_postgres()), and SQLite has no array type. Every other
# multi-value field in this codebase (dataset_adapter.py's columns/
# null_counts) uses the same json.dumps/json.loads-on-TEXT pattern for
# exactly this reason -- kept consistent here rather than reaching for
# a Postgres-only type that would break the SQLite fallback.

import json
from datetime import datetime, timezone

from app import db as db_module
from app.db import get_conn

DOMAINS = ["MDM", "Catalog", "Governance", "Security", "Banking", "Other"]


def _validate_domain(domain: str) -> None:
    if domain not in DOMAINS:
        raise ValueError(f"Invalid domain '{domain}' -- must be one of {', '.join(DOMAINS)}.")


def _ensure_schema():
    pk = "SERIAL PRIMARY KEY" if db_module._is_postgres() else "INTEGER PRIMARY KEY AUTOINCREMENT"
    with get_conn() as conn:
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS glossary_terms (
                id {pk},
                term TEXT NOT NULL UNIQUE,
                definition TEXT NOT NULL,
                domain TEXT NOT NULL,
                tags_json TEXT NOT NULL DEFAULT '[]',
                created_by TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.commit()


def _row_to_dict(row) -> dict:
    return {
        "id": row["id"],
        "term": row["term"],
        "definition": row["definition"],
        "domain": row["domain"],
        "tags": json.loads(row["tags_json"]),
        "created_by": row["created_by"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def list_terms() -> dict:
    """Every glossary term, alphabetical by term -- a glossary is
    browsed as a whole, not paginated or dataset-scoped, matching the
    reference platform's own "the whole list, client-side search"
    approach for what's realistically a few dozen terms at most."""
    _ensure_schema()
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM glossary_terms ORDER BY term COLLATE NOCASE"
        ).fetchall()

    terms = [_row_to_dict(r) for r in rows]
    domains_used = sorted({t["domain"] for t in terms})
    return {"terms": terms, "terms_total": len(terms), "domains_used": domains_used}


def create_term(payload: dict) -> dict:
    """Creates a new glossary term. term must be unique (a glossary
    with two definitions for the same word isn't a glossary) --
    enforced by the UNIQUE constraint, surfaced as a clean ValueError
    rather than a raw integrity-error 500."""
    term = (payload.get("term") or "").strip()
    definition = (payload.get("definition") or "").strip()
    domain = payload.get("domain") or "Other"
    tags = payload.get("tags") or []
    created_by = payload.get("created_by")

    if not term:
        raise ValueError("term is required.")
    if not definition:
        raise ValueError("definition is required.")
    _validate_domain(domain)
    if not isinstance(tags, list):
        raise ValueError("tags must be a list.")
    tags = [str(t).strip() for t in tags if str(t).strip()]
    if not created_by:
        raise ValueError("created_by is required.")

    _ensure_schema()
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT id FROM glossary_terms WHERE term = ?", (term,)
        ).fetchone()
        if existing:
            raise ValueError(f"A glossary term named '{term}' already exists.")

        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """INSERT INTO glossary_terms
               (term, definition, domain, tags_json, created_by, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (term, definition, domain, json.dumps(tags), created_by, now, now),
        )
        conn.commit()

    return list_terms()


def update_term(payload: dict) -> dict:
    """Updates an existing term's definition/domain/tags in place --
    the term name itself is not renameable here (a rename is really a
    delete-and-recreate, since anything that soft-referenced the old
    name via a tag would silently orphan; simpler to keep this as a
    content edit only, matching the "no editing title after creation"
    posture stewardship_adapter.py's tasks already apply)."""
    term_id = payload.get("id")
    definition = (payload.get("definition") or "").strip()
    domain = payload.get("domain") or "Other"
    tags = payload.get("tags") or []

    if term_id is None:
        raise ValueError("id is required.")
    if not definition:
        raise ValueError("definition is required.")
    _validate_domain(domain)
    if not isinstance(tags, list):
        raise ValueError("tags must be a list.")
    tags = [str(t).strip() for t in tags if str(t).strip()]

    with get_conn() as conn:
        existing = conn.execute(
            "SELECT id FROM glossary_terms WHERE id = ?", (term_id,)
        ).fetchone()
        if existing is None:
            raise ValueError(f"No glossary term {term_id} found.")

        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "UPDATE glossary_terms SET definition = ?, domain = ?, tags_json = ?, updated_at = ? WHERE id = ?",
            (definition, domain, json.dumps(tags), now, term_id),
        )
        conn.commit()

    return list_terms()


def delete_term(payload: dict) -> dict:
    """Deletes one glossary term outright. Idempotent -- deleting an
    already-gone id is a no-op, same posture as every other delete in
    this codebase's governance adapters."""
    term_id = payload.get("id")
    if term_id is None:
        raise ValueError("id is required.")

    with get_conn() as conn:
        conn.execute("DELETE FROM glossary_terms WHERE id = ?", (term_id,))
        conn.commit()

    return list_terms()
