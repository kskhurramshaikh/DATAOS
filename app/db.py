# Storage -- SQLite for now.
#
# Deliberately the simplest thing that works: one file, stdlib only, no
# ORM. This is a demo/testing store, not a production data layer -- on
# Render's free tier the disk is ephemeral, so accounts and chat history
# reset on redeploy. That's an accepted tradeoff for now; swapping this
# for a real Postgres instance later is a storage-layer change only,
# nothing above this file needs to know about it.

import os
import sqlite3
from contextlib import contextmanager

DB_PATH = os.environ.get("DATAOS_DB_PATH", "/tmp/dataos.db")


def init_db():
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
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.commit()


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()
