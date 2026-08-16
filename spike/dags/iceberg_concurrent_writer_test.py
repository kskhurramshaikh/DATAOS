"""
DataOS 3.0 -- Item 1 spike: concurrent-writer ACID proof for Iceberg-on-SeaweedFS.

Dr. Saber's added requirement (Master Requirements & Architecture Directive,
Section 03): prove Iceberg can commit safely under concurrent writers against
SeaweedFS specifically -- not just a single read/write round-trip, which is
all banking_demo_lakehouse_spike's sequential Bronze->Silver->Gold run has
proven so far. This DAG is that separate, dedicated proof.

Test design:
  - N workers (default 8) each try to append their OWN row to the SAME
    Iceberg table at (as close to) the same moment -- launched via real OS
    threads started in a tight loop, not sequential-but-fast calls, so the
    commit attempts genuinely overlap in time.
  - Each worker gets its own RestCatalog client instance (not a shared one)
    -- avoids any client-side session-sharing bug masquerading as a
    server-side concurrency bug, so a failure here can only mean SeaweedFS's
    catalog itself didn't serialize the commits safely.
  - Iceberg's optimistic concurrency model means a commit against a
    snapshot that's no longer current must be REJECTED, not silently
    accepted or silently dropped. A rejected worker reloads the table
    (to see the latest snapshot) and retries its own append, up to a
    bounded number of retries -- this is the expected, correct behavior
    under contention, not a bug.
  - PASS requires BOTH:
      1. final row count == N -- every worker's row actually landed,
         nothing silently lost.
      2. final snapshot count == N -- N distinct, real commits happened
         (rules out one write silently overwriting another's data instead
         of both being properly serialized).
  - The test table is dropped and recreated fresh on every run, in its own
    namespace -- never touches the real silver.*/gold.* demo tables.
"""
from __future__ import annotations

import sys
import threading
import time
from datetime import datetime

from airflow.decorators import dag, task

SPIKE_DAGS_ROOT = "/opt/spike-dags"
NAMESPACE = "concurrency_test"
TABLE_NAME = "writer_race"
TABLE_ID = f"{NAMESPACE}.{TABLE_NAME}"
NUM_WORKERS = 8
MAX_RETRIES_PER_WORKER = 10


def _do_one_write(worker_id: int, results: dict, results_lock: threading.Lock, start_barrier: threading.Barrier) -> None:
    """One worker's attempt to land its own row, retrying on commit conflict.
    Runs in its own thread with its own catalog client -- see module
    docstring for why."""
    sys.path.insert(0, SPIKE_DAGS_ROOT)
    from banking_demo_lakehouse_spike import _iceberg_catalog
    import pyarrow as pa

    catalog = _iceberg_catalog()  # this worker's own client instance
    last_error = None

    # Every thread waits here until all threads are ready, then all release
    # at once -- maximizes actual time-overlap of the commit attempts
    # instead of threads trickling in one after another.
    start_barrier.wait()

    for attempt in range(1, MAX_RETRIES_PER_WORKER + 1):
        try:
            table = catalog.load_table(TABLE_ID)  # must see the current snapshot to commit against it
            row = pa.Table.from_pydict({
                "worker_id": [worker_id],
                "attempt": [attempt],
                "written_at": [datetime.utcnow().isoformat()],
            })
            table.append(row)
            with results_lock:
                results[worker_id] = {"succeeded": True, "attempts": attempt}
            return
        except Exception as e:
            # Expected under real contention: a commit against a
            # since-superseded snapshot gets rejected. Reload and retry --
            # this is Iceberg's optimistic concurrency control working
            # correctly, not a failure by itself. A short, attempt-scaled
            # backoff avoids every rejected worker retrying in lockstep.
            last_error = f"{type(e).__name__}: {e}"
            time.sleep(0.05 * attempt)
            continue

    with results_lock:
        results[worker_id] = {"succeeded": False, "attempts": MAX_RETRIES_PER_WORKER, "error": last_error}


@dag(
    dag_id="iceberg_concurrent_writer_test",
    description="DataOS 3.0 Item 1 -- proves Iceberg commits safely under concurrent writers against SeaweedFS (Master Directive Section 03 requirement)",
    schedule=None,  # triggered manually, same as the main spike DAG
    start_date=datetime(2026, 8, 1),
    catchup=False,
    tags=["dataos-3.0", "spike", "acid-test"],
)
def iceberg_concurrent_writer_test():

    @task
    def run_concurrent_writers() -> dict:
        sys.path.insert(0, SPIKE_DAGS_ROOT)
        from banking_demo_lakehouse_spike import _iceberg_catalog
        import pyarrow as pa

        setup_catalog = _iceberg_catalog()
        setup_catalog.create_namespace_if_not_exists(NAMESPACE)

        # Fresh table every run -- isolated from the real demo data.
        if setup_catalog.table_exists(TABLE_ID):
            setup_catalog.drop_table(TABLE_ID)
        schema = pa.schema([
            ("worker_id", pa.int64()),
            ("attempt", pa.int64()),
            ("written_at", pa.string()),
        ])
        setup_catalog.create_table(TABLE_ID, schema=schema)

        results: dict = {}
        results_lock = threading.Lock()
        start_barrier = threading.Barrier(NUM_WORKERS)

        threads = [
            threading.Thread(target=_do_one_write, args=(i, results, results_lock, start_barrier))
            for i in range(NUM_WORKERS)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        final_table = setup_catalog.load_table(TABLE_ID)
        row_count = len(final_table.scan().to_pandas())
        snapshot_count = len(list(final_table.history()))

        succeeded = sum(1 for r in results.values() if r["succeeded"])
        failed_workers = {wid: r for wid, r in results.items() if not r["succeeded"]}
        max_attempts_any_worker = max((r["attempts"] for r in results.values()), default=0)

        summary = {
            "workers": NUM_WORKERS,
            "workers_succeeded": succeeded,
            "workers_failed": list(failed_workers.keys()),
            "final_row_count": row_count,
            "final_snapshot_count": snapshot_count,
            "max_retries_needed_by_any_worker": max_attempts_any_worker,
            "per_worker_results": results,
            "passed": (succeeded == NUM_WORKERS and row_count == NUM_WORKERS and snapshot_count == NUM_WORKERS),
        }

        print(f"CONCURRENT WRITER ACID TEST RESULT: {summary}")

        if not summary["passed"]:
            raise AssertionError(
                f"Concurrent-writer ACID test FAILED: expected {NUM_WORKERS} rows and "
                f"{NUM_WORKERS} snapshots after {NUM_WORKERS} concurrent commit attempts, "
                f"got {row_count} rows / {snapshot_count} snapshots. "
                f"Workers that never succeeded after {MAX_RETRIES_PER_WORKER} retries each: "
                f"{failed_workers}"
            )

        return summary

    run_concurrent_writers()


iceberg_concurrent_writer_test()
