# DataOS 3.0 spike -- Airflow orchestrator with the Lakehouse stack baked in.
#
# Root Directory on Render is set to `spike/`, so all paths below (dags/,
# etc.) are relative to this file's own folder in the repo, not the repo root.
FROM apache/airflow:2.10.4-python3.12

USER airflow

# pyiceberg[pyarrow,sql-postgres]: real Iceberg tables (ACID, schema-tracked).
#   pyarrow extra = the S3 file-IO backend that reads/writes actual Parquet/
#   Avro/metadata-JSON bytes to SeaweedFS. sql-postgres extra = pyiceberg's
#   SqlCatalog (pulls in sqlalchemy + psycopg2-binary) -- this is the
#   CATALOG (the small "which metadata file is current" pointer + its
#   atomic compare-and-swap on commit), now backed by real Postgres instead
#   of SeaweedFS's own built-in Iceberg REST Catalog. SeaweedFS itself is
#   UNCHANGED as the storage layer -- every data file, every metadata file,
#   still lives in the dataos-spike-iceberg S3 bucket. Only the tiny
#   "which version is current" pointer moved, because SeaweedFS's built-in
#   catalog has a real, reproducible concurrent-commit race (confirmed via
#   iceberg_concurrent_writer_test.py failing identically twice, then
#   traced directly into SeaweedFS's own handlers_commit.go source: two
#   concurrent commits can both write the same next metadata-version
#   filename, and the loser's cleanup step deletes the file the winner's
#   pointer now depends on). Postgres's own transactional UPDATE makes that
#   race structurally impossible.
# duckdb: the spike's processing engine (fast enough at this data volume,
#   see Item 1's handoff notes for why not Spark yet)
# boto3: the S3 client used to read/write Bronze objects in SeaweedFS
# pandas/openpyxl: reading the real .xlsx demo file's sheets
# scikit-learn/rapidfuzz: dependencies of the already-signed-off
#   banking_adapter.py code this DAG reuses as-is
RUN pip install --no-cache-dir \
    "pyiceberg[pyarrow,sql-postgres]==0.11.1" \
    "duckdb==1.1.3" \
    "boto3>=1.35" \
    "pandas" \
    "openpyxl" \
    "scikit-learn" \
    "rapidfuzz"

# Deliberately NOT under /opt/airflow -- the persistent disk mounted there
# (for Airflow's own run history / login / logs) would silently hide
# anything baked into the image at that same path on every container start.
# Keeping code here means every `git push` always ships the latest DAG,
# with no risk of a stale disk copy shadowing it.
COPY dags/ /opt/spike-dags/
ENV AIRFLOW__CORE__DAGS_FOLDER=/opt/spike-dags

CMD ["airflow", "standalone"]
