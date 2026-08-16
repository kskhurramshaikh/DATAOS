# DataOS 3.0 spike -- Airflow orchestrator with the Lakehouse stack baked in.
#
# Root Directory on Render is set to `spike/`, so all paths below (dags/,
# etc.) are relative to this file's own folder in the repo, not the repo root.
FROM apache/airflow:2.10.4-python3.12

USER airflow

# pyiceberg[pyarrow]: real Iceberg tables (ACID, schema-tracked) + the S3
#   file-IO backend needed to talk to SeaweedFS for actual data/metadata
#   file storage.
#
#   IMPORTANT: deliberately NOT using pyiceberg's [sql-postgres] extra here.
#   That extra requires sqlalchemy>=2.0.18, which uses SQLAlchemy 2.0's
#   "Annotated Declarative" ORM mapping style -- confirmed directly
#   incompatible with Airflow 2.10.4's own ORM models in this exact image
#   (installing it broke Airflow's TaskInstance model with
#   `MappedAnnotationError: ... can't be correctly interpreted for
#   Annotated Declarative Table form`). The Iceberg catalog's Postgres
#   backend is instead a small hand-rolled module (pg_iceberg_catalog.py,
#   see dags/) using only psycopg2 -- same atomic-commit semantics as
#   pyiceberg's own SqlCatalog, tested directly against a real concurrent-
#   writer scenario before shipping, without pulling in sqlalchemy at all.
# duckdb: the spike's processing engine (fast enough at this data volume,
#   see Item 1's handoff notes for why not Spark yet)
# boto3: the S3 client used to read/write Bronze objects in SeaweedFS
# pandas/openpyxl: reading the real .xlsx demo file's sheets
# scikit-learn/rapidfuzz: dependencies of the already-signed-off
#   banking_adapter.py code this DAG reuses as-is
# psycopg2-binary: driver for both Airflow's own metadata DB
#   (AIRFLOW__DATABASE__SQL_ALCHEMY_CONN, via Airflow's own SQLAlchemy 1.4)
#   and pg_iceberg_catalog.py's direct connection to the same Postgres
#   instance for the Iceberg catalog -- explicit here rather than assuming
#   the base image's default provider set already has it.
RUN pip install --no-cache-dir \
    "pyiceberg[pyarrow]==0.11.1" \
    "duckdb==1.1.3" \
    "boto3>=1.35" \
    "pandas" \
    "openpyxl" \
    "scikit-learn" \
    "rapidfuzz" \
    "psycopg2-binary"

# Deliberately NOT under /opt/airflow -- the persistent disk mounted there
# (for Airflow's own run history / login / logs) would silently hide
# anything baked into the image at that same path on every container start.
# Keeping code here means every `git push` always ships the latest DAG,
# with no risk of a stale disk copy shadowing it.
COPY dags/ /opt/spike-dags/
ENV AIRFLOW__CORE__DAGS_FOLDER=/opt/spike-dags

CMD ["airflow", "standalone"]
