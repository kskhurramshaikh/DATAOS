# Storage.
#
# PRODUCTION (Render): reuses the SAME Postgres instance Item 2 already
# provisioned for the Airflow/Iceberg catalog (LAKEHOUSE_DB_URI -- see
# app/lakehouse_client.py's own docstring for why that's the *external*
# Database URL, not the internal one -- this app is cross-region from
# that Postgres). No new Render resource, no new cost: this app's own
# tables (users, conversations, messages, datasets, duplicate_clusters,
# golden_records) live in their own Postgres SCHEMA ("dataos_app") on
# that same database, alongside Airflow's own tables and the Iceberg
# catalog's tables -- kept in a separate schema specifically so a bug in
# this app's migrations can't touch Airflow's or the catalog's schema,
# not because the underlying instance needs to be different.
#
# CI / LOCAL DEV: LAKEHOUSE_DB_URI isn't set in GitHub Actions (no
# secret configured there, deliberately -- the test job has no need to
# reach a real Postgres), so get_conn()/init_db() fall back to the
# original SQLite-on-disk behavior, completely unchanged. This is load-
# bearing for the test suite: tests/test_dedup_adapter.py and others
# directly `monkeypatch.setattr("app.db.DB_PATH", ...)` for per-test
# isolation, so DB_PATH must keep meaning exactly what it always has.
# Same tradeoff note as before applies to this fallback path only: a
# local/CI run's SQLite file is disposable, not meant to persist.
#
# DIAGNOSTIC NOTE (2026-08-17): the first version of this fix (commit
# ac0bed3) silently degrades to the SQLite fallback if LAKEHOUSE_DB_URI
# isn't actually present in the RUNTIME environment on Render for any
# reason (typo'd env var name, not attached to this specific service,
# etc) -- and because that fallback boots and serves requests exactly
# as successfully as the Postgres path, there is NO visible symptom
# distinguishing "using Postgres" from "silently back on ephemeral
# SQLite" from the outside. storage_status() below exists specifically
# to answer that question with certainty instead of guessing across
# another redeploy cycle -- see /api/debug/storage in main.py.
#
# SEPARATE, EQUALLY IMPORTANT CONFUSION SOURCE found the same day:
# Render keeps the OLD container fully serving traffic until the NEW
# one passes its health check, so hitting the live URL while a deploy
# is still building/testing shows the previous version with zero
# visible sign that a newer one exists -- "the app is up" and "the
# latest commit is live" are NOT the same fact. storage_status() now
# also reports RENDER_GIT_COMMIT (a var Render injects into every
# service automatically, no setup needed) specifically so that
# ambiguity has a definitive answer too, not just the backend question.
#
# The Postgres path is a thin sqlite3-compatible WRAPPER (_Connection/
# _Cursor below), not a rewrite of the four files that call get_conn()
# (auth.py, chat_store.py, dataset_adapter.py, dedup_adapter.py). Three
# sqlite3-isms those files rely on are translated transparently:
#   1. '?' placeholders -> '%s' (psycopg2's paramstyle).
#   2. cur.lastrowid -- every table here has an 'id' PK, so a bare
#      INSERT without an explicit RETURNING gets one appended
#      automatically, and the returned id is captured onto .lastrowid.
#   3. bare CURRENT_TIMESTAMP (used both in CREATE TABLE ... DEFAULT
#      clauses and in ad-hoc UPDATE ... SET decided_at = CURRENT_TIMESTAMP
#      statements in dedup_adapter.py) -- valid SQLite, but in Postgres
#      it evaluates to a timestamptz that can't implicitly assign into a
#      TEXT column. Rewritten to a text-producing to_char(...) call that
#      still matches every column's original TEXT type (unchanged, both
#      to preserve the ISO-8601 'T' format the dashboard's JS already
#      expects via .replace("T"," ") and to avoid touching every caller
#      that stores its own datetime.now(...).isoformat() string).
# Row access (row["col"]) matches on both backends: sqlite3.Row and
# psycopg2's RealDictCursor rows both support it.

import os
import re
import sqlite3
from contextlib import contextmanager

DB_PATH = os.environ.get("DATAOS_DB_PATH", "/tmp/dataos.db")

# Prefer an app-specific override if one's ever set; otherwise reuse the
# exact same Postgres Item 2 already wired up. Empty in CI -> SQLite
# fallback below.
PG_DB_URI = os.environ.get("DATAOS_APP_DB_URI") or os.environ.get("LAKEHOUSE_DB_URI", "")
PG_SCHEMA = os.environ.get("DATAOS_APP_SCHEMA", "dataos_app")

_CURRENT_TIMESTAMP_RE = re.compile(r"\bCURRENT_TIMESTAMP\b", re.IGNORECASE)
_CURRENT_TIMESTAMP_PG = "to_char(CURRENT_TIMESTAMP AT TIME ZONE 'utc', 'YYYY-MM-DD\"T\"HH24:MI:SS')"


def _is_postgres() -> bool:
    return bool(PG_DB_URI)


# ---------------------------------------------------------------------
# Postgres path -- thin sqlite3-compatible wrapper, see module docstring.
# ---------------------------------------------------------------------

class _PgCursor:
    __slots__ = ("_cur", "lastrowid")

    def __init__(self, cur):
        self._cur = cur
        self.lastrowid = None

    def execute(self, sql, params=()):
        pg_sql = _CURRENT_TIMESTAMP_RE.sub(_CURRENT_TIMESTAMP_PG, sql)
        pg_sql = pg_sql.replace("?", "%s")

        stripped = pg_sql.strip()
        is_insert = stripped.upper().startswith("INSERT")
        if is_insert and "RETURNING" not in stripped.upper():
            pg_sql = f"{pg_sql.rstrip().rstrip(';')} RETURNING id"

        self._cur.execute(pg_sql, params)

        if is_insert:
            import psycopg2

            try:
                row = self._cur.fetchone()
                self.lastrowid = row["id"] if row else None
            except psycopg2.ProgrammingError:
                self.lastrowid = None

        return self

    def fetchone(self):
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()


class _PgConnection:
    __slots__ = ("_conn",)

    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, params=()):
        import psycopg2.extras

        cur = _PgCursor(self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor))
        return cur.execute(sql, params)

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()


def _pg_init_db():
    with get_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                email TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                salt TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS conversations (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id SERIAL PRIMARY KEY,
                conversation_id INTEGER NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (conversation_id) REFERENCES conversations (id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS datasets (
                id SERIAL PRIMARY KEY,
                safe_name TEXT UNIQUE NOT NULL,
                display_name TEXT NOT NULL,
                uploaded_by INTEGER NOT NULL,
                stage TEXT NOT NULL,
                rows INTEGER NOT NULL,
                columns_json TEXT NOT NULL,
                dropped_columns_json TEXT NOT NULL DEFAULT '[]',
                duplicate_rows_removed INTEGER NOT NULL DEFAULT 0,
                null_counts_json TEXT NOT NULL DEFAULT '{}',
                bronze_path TEXT,
                silver_path TEXT,
                gold_path TEXT,
                duplicate_check_last_run_at TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (uploaded_by) REFERENCES users (id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS duplicate_clusters (
                id SERIAL PRIMARY KEY,
                dataset_safe_name TEXT NOT NULL,
                cluster_index INTEGER NOT NULL,
                member_row_ids_json TEXT NOT NULL,
                member_summary_json TEXT NOT NULL,
                confidence_tier TEXT NOT NULL,
                evidence_json TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                decided_at TEXT,
                decided_by TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        # Golden Record Registry (Item 3, MDM): one row per EXECUTED merge
        # of a confirmed duplicate cluster -- not an estimate. See
        # app/adapters/dedup_adapter.py's _execute_merge() for the
        # survivorship strategy (most-complete-record-wins, gaps filled
        # from other members, every field's source row tracked).
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS golden_records (
                id SERIAL PRIMARY KEY,
                dataset_safe_name TEXT NOT NULL,
                cluster_id INTEGER NOT NULL,
                merged_data_json TEXT NOT NULL,
                field_sources_json TEXT NOT NULL,
                source_row_ids_json TEXT NOT NULL,
                base_row_id TEXT NOT NULL,
                merged_by TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (cluster_id) REFERENCES duplicate_clusters (id)
            )
            """
        )
        conn.commit()


@contextmanager
def _pg_get_conn():
    import psycopg2

    raw = psycopg2.connect(PG_DB_URI)
    try:
        with raw.cursor() as setup_cur:
            setup_cur.execute(f"CREATE SCHEMA IF NOT EXISTS {PG_SCHEMA}")
            setup_cur.execute(f"SET search_path TO {PG_SCHEMA}, public")
        raw.commit()
        yield _PgConnection(raw)
    finally:
        raw.close()


# ---------------------------------------------------------------------
# SQLite path -- unchanged from before (CI/local dev fallback).
# ---------------------------------------------------------------------

def _sqlite_init_db():
    with get_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                salt TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id INTEGER NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (conversation_id) REFERENCES conversations (id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS datasets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                safe_name TEXT UNIQUE NOT NULL,
                display_name TEXT NOT NULL,
                uploaded_by INTEGER NOT NULL,
                stage TEXT NOT NULL,
                rows INTEGER NOT NULL,
                columns_json TEXT NOT NULL,
                dropped_columns_json TEXT NOT NULL DEFAULT '[]',
                duplicate_rows_removed INTEGER NOT NULL DEFAULT 0,
                null_counts_json TEXT NOT NULL DEFAULT '{}',
                bronze_path TEXT,
                silver_path TEXT,
                gold_path TEXT,
                duplicate_check_last_run_at TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (uploaded_by) REFERENCES users (id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS duplicate_clusters (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dataset_safe_name TEXT NOT NULL,
                cluster_index INTEGER NOT NULL,
                member_row_ids_json TEXT NOT NULL,
                member_summary_json TEXT NOT NULL,
                confidence_tier TEXT NOT NULL,
                evidence_json TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                decided_at TEXT,
                decided_by TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS golden_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dataset_safe_name TEXT NOT NULL,
                cluster_id INTEGER NOT NULL,
                merged_data_json TEXT NOT NULL,
                field_sources_json TEXT NOT NULL,
                source_row_ids_json TEXT NOT NULL,
                base_row_id TEXT NOT NULL,
                merged_by TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (cluster_id) REFERENCES duplicate_clusters (id)
            )
            """
        )
        conn.commit()

        # Legacy compat shims (pre-dates the golden_records/decided_by
        # columns being in the CREATE TABLE above from the start) -- kept
        # so an existing local/CI sqlite file created by an older version
        # of this module doesn't break instead of gaining the column.
        try:
            conn.execute("ALTER TABLE duplicate_clusters ADD COLUMN decided_by TEXT")
            conn.commit()
        except sqlite3.OperationalError:
            pass  # column already exists

        try:
            conn.execute("ALTER TABLE datasets ADD COLUMN duplicate_check_last_run_at TEXT")
            conn.commit()
        except sqlite3.OperationalError:
            pass  # column already exists


@contextmanager
def _sqlite_get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


# ---------------------------------------------------------------------
# Public interface -- unchanged names/signatures either backend uses.
# ---------------------------------------------------------------------

def init_db():
    if _is_postgres():
        _pg_init_db()
    else:
        _sqlite_init_db()


@contextmanager
def get_conn():
    if _is_postgres():
        with _pg_get_conn() as conn:
            yield conn
    else:
        with _sqlite_get_conn() as conn:
            yield conn


def storage_status() -> dict:
    """Answers two questions with certainty instead of guesswork, both
    of which caused real confusion on 2026-08-17 -- see the module
    docstring's DIAGNOSTIC NOTE and the note right below it:
      1. Which storage backend is this PROCESS actually running on --
         Postgres, or silently back on ephemeral SQLite?
      2. Is the code THIS RESPONSE came from actually the latest commit
         -- or is Render still serving the previous container while a
         newer deploy builds/tests in the background (which it does
         transparently, with zero visible difference from the outside)?
    Exposed at /api/debug/storage. Masks the DB URI (host only) rather
    than ever returning it whole -- it contains real Postgres
    credentials."""
    postgres = _is_postgres()
    status: dict = {
        "git_commit": os.environ.get("RENDER_GIT_COMMIT", "unknown (not running on Render, or var unset)"),
        "backend": "postgres" if postgres else "sqlite",
        "postgres_configured": postgres,
        "schema": PG_SCHEMA if postgres else None,
        "sqlite_path": None if postgres else DB_PATH,
    }
    if postgres:
        # Host only -- never the full URI, which carries credentials.
        host_part = PG_DB_URI.split("@")[-1].split("/")[0] if "@" in PG_DB_URI else "(unrecognized URI shape)"
        status["postgres_host"] = host_part
        try:
            with get_conn() as conn:
                gr = conn.execute("SELECT COUNT(*) AS c FROM golden_records").fetchone()
                dc = conn.execute("SELECT COUNT(*) AS c FROM duplicate_clusters").fetchone()
                status["golden_records_count"] = gr["c"] if gr else 0
                status["duplicate_clusters_count"] = dc["c"] if dc else 0
                status["reachable"] = True
        except Exception as e:  # noqa: BLE001 -- this IS the diagnostic; surface it, don't hide it
            status["reachable"] = False
            status["error"] = f"{type(e).__name__}: {e}"
    else:
        status["golden_records_count"] = None
        status["duplicate_clusters_count"] = None
        status["note"] = (
            "PG_DB_URI is empty in this process -- neither DATAOS_APP_DB_URI nor "
            "LAKEHOUSE_DB_URI is set in the runtime environment, so this is running "
            "on the ephemeral SQLite fallback. If this is Render (not CI), check "
            "that LAKEHOUSE_DB_URI is actually attached to THIS service's env vars."
        )
    return status
