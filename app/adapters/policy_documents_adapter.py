# Policy Document Upload -- closes the "no 'uploaded policy documents'
# feature anywhere on the page" gap flagged in the 2026-08-19 gap
# analysis for the Classification & PDPL page.
#
# SCOPE: global, not per-dataset. A data classification policy or a
# PDPL compliance policy is an organization-level artifact -- it
# governs how EVERY dataset is classified, it isn't itself scoped to
# one dataset. Documents uploaded here appear on the Classification &
# PDPL page regardless of which dataset is currently selected in that
# page's own picker.
#
# Real file bytes, stored via app/object_storage.py's put_bytes()/
# get_bytes() (added alongside this module -- the first binary-file
# storage need in this codebase; every prior use of object storage was
# text/CSV content). Metadata lives in Postgres/SQLite, same
# CREATE-TABLE-IF-NOT-EXISTS-at-top-of-every-call pattern as
# ndi_history.py / sama_history.py, for the same test-fixture-
# monkeypatching reason documented there.

import os

from app import db, object_storage

# 10 MB cap -- policy documents are text-based PDFs/Word docs, not
# datasets; generous enough for a real multi-page policy, small enough
# that a mistaken upload of the wrong file can't silently eat storage.
MAX_SIZE_BYTES = 10 * 1024 * 1024

ALLOWED_EXTENSIONS = {".pdf", ".doc", ".docx"}


def _ensure_schema():
    """See ndi_history.py's _ensure_schema() docstring for why this
    isn't a cached module-level flag."""
    pk = "SERIAL PRIMARY KEY" if db._is_postgres() else "INTEGER PRIMARY KEY AUTOINCREMENT"
    with db.get_conn() as conn:
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS policy_documents (
                id {pk},
                filename TEXT NOT NULL,
                content_type TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                storage_uri TEXT NOT NULL,
                uploaded_by TEXT NOT NULL,
                uploaded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                note TEXT
            )
            """
        )
        conn.commit()


def _row_to_dict(row) -> dict:
    return {
        "id": row["id"],
        "filename": row["filename"],
        "content_type": row["content_type"],
        "size_bytes": row["size_bytes"],
        "uploaded_by": row["uploaded_by"],
        "uploaded_at": row["uploaded_at"],
        "note": row["note"],
    }


def upload_policy_document(filename: str, content: bytes, content_type: str, uploaded_by: str, note: str | None = None) -> dict:
    """Stores one real uploaded policy document -- both the actual
    file bytes (object storage) and its metadata (DB row). Rejects
    obviously-wrong uploads (empty file, oversized, wrong extension)
    with a plain-English ValueError rather than silently accepting
    anything -- same validation posture as dataset_adapter's own
    extract_csv_content()."""
    filename = (filename or "").strip()
    if not filename:
        raise ValueError("A filename is required.")
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError(
            f"'{ext or '(no extension)'}' isn't a supported policy document type -- "
            f"upload a PDF or Word document ({', '.join(sorted(ALLOWED_EXTENSIONS))})."
        )
    if not content:
        raise ValueError("The uploaded file is empty.")
    if len(content) > MAX_SIZE_BYTES:
        raise ValueError(
            f"File is {len(content) / (1024 * 1024):.1f} MB -- policy documents are capped at "
            f"{MAX_SIZE_BYTES // (1024 * 1024)} MB."
        )
    uploaded_by = (uploaded_by or "").strip()
    if not uploaded_by:
        raise ValueError(
            "A name is required to upload a policy document -- an audit record with no named "
            "uploader isn't an audit record."
        )
    note = (note or "").strip() or None

    import time

    safe_name = "".join(c if c.isalnum() or c in "._-" else "_" for c in filename)
    key = f"policy-documents/{int(time.time() * 1000)}_{safe_name}"
    storage_uri = object_storage.put_bytes(key, content, content_type=content_type, local_root="/tmp/dataos-local-storage")

    _ensure_schema()
    with db.get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO policy_documents (filename, content_type, size_bytes, storage_uri, uploaded_by, note)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (filename, content_type, len(content), storage_uri, uploaded_by, note),
        )
        doc_id = cur.lastrowid
        conn.commit()

    return get_policy_document(doc_id)


def get_policy_document(doc_id: int) -> dict:
    _ensure_schema()
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM policy_documents WHERE id = ?", (doc_id,)).fetchone()
    if not row:
        raise ValueError(f"No policy document found with id {doc_id}.")
    return _row_to_dict(row)


def list_policy_documents() -> dict:
    """Every uploaded policy document, newest first."""
    _ensure_schema()
    with db.get_conn() as conn:
        rows = conn.execute("SELECT * FROM policy_documents ORDER BY uploaded_at DESC, id DESC").fetchall()
    return {"documents": [_row_to_dict(r) for r in rows]}


def get_policy_document_content(doc_id: int) -> tuple[bytes, str, str]:
    """Returns (content_bytes, filename, content_type) for download --
    reads the real file back from object storage."""
    doc = get_policy_document(doc_id)
    _ensure_schema()
    with db.get_conn() as conn:
        row = conn.execute("SELECT storage_uri FROM policy_documents WHERE id = ?", (doc_id,)).fetchone()
    content = object_storage.get_bytes(row["storage_uri"])
    return content, doc["filename"], doc["content_type"]


def delete_policy_document(doc_id: int) -> dict:
    """Removes a policy document's metadata row. Deliberately does NOT
    also delete the underlying object storage file -- a real,
    regulator-facing document trail is safer erring toward keeping the
    file in storage over losing it to a wrong click; a stale object
    with no DB row pointing at it is inert and harmless."""
    doc = get_policy_document(doc_id)  # raises ValueError if missing
    _ensure_schema()
    with db.get_conn() as conn:
        conn.execute("DELETE FROM policy_documents WHERE id = ?", (doc_id,))
        conn.commit()
    return {"deleted": True, "id": doc_id, "filename": doc["filename"]}
