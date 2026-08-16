# DataOS 3.0 spike -- Airflow orchestrator with the Lakehouse stack baked in.
#
# Root Directory on Render is set to `spike/`, so all paths below (dags/,
# etc.) are relative to this file's own folder in the repo, not the repo root.
FROM apache/airflow:2.10.4-python3.12

USER airflow

# pyiceberg[pyarrow]: real Iceberg tables (ACID, schema-tracked) + the S3
#   file-IO backend needed to talk to SeaweedFS
# duckdb: the spike's processing engine (fast enough at this data volume,
#   see Item 1's handoff notes for why not Spark yet)
# boto3: the S3 client used to read/write Bronze objects in SeaweedFS
# pandas/openpyxl: reading the real .xlsx demo file's sheets
# scikit-learn/rapidfuzz: dependencies of the already-signed-off
#   banking_adapter.py code this DAG reuses as-is
RUN pip install --no-cache-dir --user \
    "pyiceberg[pyarrow]==0.11.1" \
    "duckdb==1.1.3" \
    "boto3>=1.35" \
    "pandas" \
    "openpyxl" \
    "scikit-learn" \
    "rapidfuzz"

# Deliberately NOT under /opt/airflow -- the persistent disk mounted there
# (for Airflow's own metadata DB / run history / login) would silently hide
# anything baked into the image at that same path on every container start.
# Keeping code here means every `git push` always ships the latest DAG,
# with no risk of a stale disk copy shadowing it.
COPY dags/ /opt/spike-dags/
ENV AIRFLOW__CORE__DAGS_FOLDER=/opt/spike-dags

CMD ["airflow", "standalone"]
