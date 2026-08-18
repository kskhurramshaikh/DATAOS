"""
A minimal Iceberg catalog backed directly by Postgres via psycopg2 -- no
SQLAlchemy. Same schema and commit semantics as pyiceberg's own
pyiceberg.catalog.sql.SqlCatalog (the "iceberg_tables" / JDBC-catalog
convention every Iceberg engine, including Trino and Spark, already knows
how to read), just without importing sqlalchemy at all.

NOTE: this is an intentional copy of spike/dags/pg_iceberg_catalog.py, not
a shared import -- this app and the spike orchestrator are two separate
Render services with two separate Docker build contexts, so there's no
single Python path both could import from without a much bigger packaging
change. Keep both copies in sync if this file changes.

Why it exists (same reasoning as the spike copy): pyiceberg's SqlCatalog
requires sqlalchemy>=2.0.18. This app doesn't run Airflow, so there's no
Airflow-vs-SQLAlchemy-2.0 conflict here specifically -- but reusing the
same already-tested module (rather than pyiceberg's own SqlCatalog) avoids
introducing a second, different code path for the same job, and keeps the
dependency footprint smaller (psycopg2 only, no sqlalchemy at all).

Commit protocol (mirrors SqlCatalog's own, verified against its real
source before writing this):
  - One row per table in `iceberg_tables`, primary key
    (catalog_name, table_namespace, table_name).
  - A commit is: `UPDATE iceberg_tables SET metadata_location = new
    WHERE ... AND metadata_location = <what this commit was based on>`.
    If another writer already moved the pointer, this UPDATE affects 0
    rows -- rowcount is checked and CommitFailedException raised.

CONNECTION LEAK FIX (2026-08-18): every method here used to do
`with self._connect() as conn: ...` and stop there. psycopg2 connections
implement `__enter__`/`__exit__` for the TRANSACTION only (commit on
clean exit, rollback on exception) -- exiting that `with` block never
closes the actual socket. Every call to any method on this class opened
a brand new Postgres connection and never released it. Confirmed live:
the Lakehouse Zones page got stuck permanently "pending" after enough
calls accumulated (each get_zone_stats() call alone opens this catalog
once and calls several of its methods -- table_exists/load_table for
Silver and up to two Gold tables), exhausting Render's Postgres
connection limit; once exhausted, new connection attempts just hang
waiting for a slot that never frees. Every method below now explicitly
closes its connection in a `finally` block, keeping the exact same
commit/rollback behavior via the same `with conn:` (transaction) usage
nested inside -- only the missing `.close()` was added.
"""
from __future__ import annotations

import re

import psycopg2
import psycopg2.errors
import psycopg2.extras

from pyiceberg.catalog import (
    LOCATION,
    URI,
    Catalog,
    MetastoreCatalog,
    PropertiesUpdateSummary,
)
from pyiceberg.exceptions import (
    CommitFailedException,
    NamespaceAlreadyExistsError,
    NamespaceNotEmptyError,
    NoSuchNamespaceError,
    NoSuchTableError,
    TableAlreadyExistsError,
)
from pyiceberg.io import load_file_io
from pyiceberg.partitioning import UNPARTITIONED_PARTITION_SPEC, PartitionSpec
from pyiceberg.schema import Schema
from pyiceberg.serializers import FromInputFile
from pyiceberg.table import CommitTableResponse, Table, TableProperties
from pyiceberg.table.locations import load_location_provider
from pyiceberg.table.metadata import new_table_metadata
from pyiceberg.table.sorting import UNSORTED_SORT_ORDER, SortOrder
from pyiceberg.table.update import TableRequirement, TableUpdate
from pyiceberg.typedef import EMPTY_DICT, Identifier, Properties


def _psycopg2_dsn(sqlalchemy_style_uri: str) -> str:
    """SqlCatalog/Airflow-style URIs look like
    'postgresql+psycopg2://user:pass@host/db' -- psycopg2.connect() wants
    the plain libpq form without the '+psycopg2' driver suffix."""
    return re.sub(r"^postgresql\+psycopg2://", "postgresql://", sqlalchemy_style_uri)


class PostgresIcebergCatalog(MetastoreCatalog):
    def __init__(self, name: str, **properties: str):
        super().__init__(name, **properties)
        if not (uri_prop := self.properties.get(URI)):
            raise ValueError("Postgres connection URI is required (property 'uri')")
        self._dsn = _psycopg2_dsn(uri_prop)
        self._init_tables()

    def _connect(self):
        return psycopg2.connect(self._dsn)

    def _init_tables(self) -> None:
        conn = self._connect()
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS iceberg_tables (
                            catalog_name TEXT NOT NULL,
                            table_namespace TEXT NOT NULL,
                            table_name TEXT NOT NULL,
                            metadata_location TEXT,
                            previous_metadata_location TEXT,
                            PRIMARY KEY (catalog_name, table_namespace, table_name)
                        )
                    """)
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS iceberg_namespace_properties (
                            catalog_name TEXT NOT NULL,
                            namespace TEXT NOT NULL,
                            property_key TEXT NOT NULL,
                            property_value TEXT NOT NULL,
                            PRIMARY KEY (catalog_name, namespace, property_key)
                        )
                    """)
        finally:
            conn.close()

    # -- helpers ------------------------------------------------------

    def _convert_row_to_iceberg(self, namespace: str, table_name: str, metadata_location: str) -> Table:
        io = load_file_io(properties=self.properties, location=metadata_location)
        file = io.new_input(metadata_location)
        metadata = FromInputFile.table_metadata(file)
        return Table(
            identifier=Catalog.identifier_to_tuple(namespace) + (table_name,),
            metadata=metadata,
            metadata_location=metadata_location,
            io=self._load_file_io(metadata.properties, metadata_location),
            catalog=self,
        )

    # -- Catalog interface ---------------------------------------------

    def create_table(
        self,
        identifier: str | Identifier,
        schema: Schema,
        location: str | None = None,
        partition_spec: PartitionSpec = UNPARTITIONED_PARTITION_SPEC,
        sort_order: SortOrder = UNSORTED_SORT_ORDER,
        properties: Properties = EMPTY_DICT,
    ) -> Table:
        schema = self._convert_schema_if_needed(
            schema, int(properties.get(TableProperties.FORMAT_VERSION, TableProperties.DEFAULT_FORMAT_VERSION))
        )
        namespace_identifier = Catalog.namespace_from(identifier)
        table_name = Catalog.table_name_from(identifier)
        if not self.namespace_exists(namespace_identifier):
            raise NoSuchNamespaceError(f"Namespace does not exist: {namespace_identifier}")

        namespace = Catalog.namespace_to_string(namespace_identifier)
        resolved_location = self._resolve_table_location(location, namespace, table_name)
        location_provider = load_location_provider(table_location=resolved_location, table_properties=properties)
        metadata_location = location_provider.new_table_metadata_file_location()
        metadata = new_table_metadata(
            location=resolved_location, schema=schema, partition_spec=partition_spec,
            sort_order=sort_order, properties=properties,
        )
        io = load_file_io(properties=self.properties, location=metadata_location)
        self._write_metadata(metadata, io, metadata_location)

        conn = self._connect()
        try:
            with conn:
                with conn.cursor() as cur:
                    try:
                        cur.execute(
                            "INSERT INTO iceberg_tables "
                            "(catalog_name, table_namespace, table_name, metadata_location, previous_metadata_location) "
                            "VALUES (%s, %s, %s, %s, NULL)",
                            (self.name, namespace, table_name, metadata_location),
                        )
                    except psycopg2.errors.UniqueViolation:
                        conn.rollback()
                        raise TableAlreadyExistsError(f"Table {namespace}.{table_name} already exists")
        finally:
            conn.close()

        return self.load_table(identifier=identifier)

    def register_table(self, identifier: str | Identifier, metadata_location: str, overwrite: bool = False) -> Table:
        raise NotImplementedError

    def load_table(self, identifier: str | Identifier) -> Table:
        namespace_tuple = Catalog.namespace_from(identifier)
        namespace = Catalog.namespace_to_string(namespace_tuple)
        table_name = Catalog.table_name_from(identifier)
        conn = self._connect()
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT metadata_location FROM iceberg_tables "
                        "WHERE catalog_name = %s AND table_namespace = %s AND table_name = %s",
                        (self.name, namespace, table_name),
                    )
                    row = cur.fetchone()
        finally:
            conn.close()
        if row and row[0]:
            return self._convert_row_to_iceberg(namespace, table_name, row[0])
        raise NoSuchTableError(f"Table does not exist: {namespace}.{table_name}")

    def drop_table(self, identifier: str | Identifier) -> None:
        namespace_tuple = Catalog.namespace_from(identifier)
        namespace = Catalog.namespace_to_string(namespace_tuple)
        table_name = Catalog.table_name_from(identifier)
        conn = self._connect()
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM iceberg_tables "
                        "WHERE catalog_name = %s AND table_namespace = %s AND table_name = %s",
                        (self.name, namespace, table_name),
                    )
                    deleted = cur.rowcount
        finally:
            conn.close()
        if deleted < 1:
            raise NoSuchTableError(f"Table does not exist: {namespace}.{table_name}")

    def rename_table(self, from_identifier: str | Identifier, to_identifier: str | Identifier) -> Table:
        raise NotImplementedError

    def commit_table(
        self, table: Table, requirements: tuple[TableRequirement, ...], updates: tuple[TableUpdate, ...]
    ) -> CommitTableResponse:
        table_identifier = table.name()
        namespace_tuple = Catalog.namespace_from(table_identifier)
        namespace = Catalog.namespace_to_string(namespace_tuple)
        table_name = Catalog.table_name_from(table_identifier)

        try:
            current_table: Table | None = self.load_table(table_identifier)
        except NoSuchTableError:
            current_table = None

        updated_staged_table = self._update_and_stage_table(current_table, table_identifier, requirements, updates)
        if current_table and updated_staged_table.metadata == current_table.metadata:
            return CommitTableResponse(metadata=current_table.metadata, metadata_location=current_table.metadata_location)

        self._write_metadata(
            metadata=updated_staged_table.metadata,
            io=updated_staged_table.io,
            metadata_path=updated_staged_table.metadata_location,
        )

        conn = self._connect()
        try:
            with conn:
                with conn.cursor() as cur:
                    if current_table:
                        # THE atomic compare-and-swap: only succeeds if metadata_location
                        # still matches what this commit was staged against. This is what
                        # SeaweedFS's built-in catalog got wrong under concurrency.
                        cur.execute(
                            "UPDATE iceberg_tables "
                            "SET metadata_location = %s, previous_metadata_location = %s "
                            "WHERE catalog_name = %s AND table_namespace = %s AND table_name = %s "
                            "AND metadata_location = %s",
                            (
                                updated_staged_table.metadata_location,
                                current_table.metadata_location,
                                self.name, namespace, table_name,
                                current_table.metadata_location,
                            ),
                        )
                        if cur.rowcount < 1:
                            conn.rollback()
                            raise CommitFailedException(f"Table has been updated by another process: {namespace}.{table_name}")
                    else:
                        try:
                            cur.execute(
                                "INSERT INTO iceberg_tables "
                                "(catalog_name, table_namespace, table_name, metadata_location, previous_metadata_location) "
                                "VALUES (%s, %s, %s, %s, NULL)",
                                (self.name, namespace, table_name, updated_staged_table.metadata_location),
                            )
                        except psycopg2.errors.UniqueViolation:
                            conn.rollback()
                            raise TableAlreadyExistsError(f"Table {namespace}.{table_name} already exists")
        finally:
            conn.close()

        return CommitTableResponse(
            metadata=updated_staged_table.metadata, metadata_location=updated_staged_table.metadata_location
        )

    def create_namespace(self, namespace: str | Identifier, properties: Properties = EMPTY_DICT) -> None:
        namespace_str = Catalog.namespace_to_string(namespace, NoSuchNamespaceError)
        if self.namespace_exists(namespace):
            raise NamespaceAlreadyExistsError(f"Namespace {namespace} already exists")
        create_properties = dict(properties) if properties else {"exists": "true"}
        conn = self._connect()
        try:
            with conn:
                with conn.cursor() as cur:
                    for key, value in create_properties.items():
                        cur.execute(
                            "INSERT INTO iceberg_namespace_properties "
                            "(catalog_name, namespace, property_key, property_value) VALUES (%s, %s, %s, %s)",
                            (self.name, namespace_str, key, value),
                        )
        finally:
            conn.close()

    def drop_namespace(self, namespace: str | Identifier) -> None:
        if not self.namespace_exists(namespace):
            raise NoSuchNamespaceError(f"Namespace does not exist: {namespace}")
        namespace_str = Catalog.namespace_to_string(namespace)
        if tables := self.list_tables(namespace):
            raise NamespaceNotEmptyError(f"Namespace {namespace_str} is not empty. {len(tables)} tables exist.")
        conn = self._connect()
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM iceberg_namespace_properties WHERE catalog_name = %s AND namespace = %s",
                        (self.name, namespace_str),
                    )
        finally:
            conn.close()

    def list_tables(self, namespace: str | Identifier) -> list[Identifier]:
        if namespace and not self.namespace_exists(namespace):
            raise NoSuchNamespaceError(f"Namespace does not exist: {namespace}")
        namespace_str = Catalog.namespace_to_string(namespace)
        conn = self._connect()
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT table_namespace, table_name FROM iceberg_tables "
                        "WHERE catalog_name = %s AND table_namespace = %s",
                        (self.name, namespace_str),
                    )
                    rows = cur.fetchall()
        finally:
            conn.close()
        return [(Catalog.identifier_to_tuple(ns) + (tbl,)) for ns, tbl in rows]

    def list_namespaces(self, namespace: str | Identifier = ()) -> list[Identifier]:
        conn = self._connect()
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT DISTINCT namespace FROM iceberg_namespace_properties WHERE catalog_name = %s "
                        "UNION SELECT DISTINCT table_namespace FROM iceberg_tables WHERE catalog_name = %s",
                        (self.name, self.name),
                    )
                    rows = cur.fetchall()
        finally:
            conn.close()
        prefix = Catalog.identifier_to_tuple(namespace)
        results = set()
        for (ns,) in rows:
            ns_tuple = Catalog.identifier_to_tuple(ns)
            if len(ns_tuple) > len(prefix) and ns_tuple[: len(prefix)] == prefix:
                results.add(ns_tuple[: len(prefix) + 1])
        return list(results)

    def load_namespace_properties(self, namespace: str | Identifier) -> Properties:
        namespace_str = Catalog.namespace_to_string(namespace)
        conn = self._connect()
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT property_key, property_value FROM iceberg_namespace_properties "
                        "WHERE catalog_name = %s AND namespace = %s",
                        (self.name, namespace_str),
                    )
                    rows = cur.fetchall()
        finally:
            conn.close()
        if not rows:
            raise NoSuchNamespaceError(f"Namespace {namespace_str} does not exist")
        return dict(rows)

    def update_namespace_properties(
        self, namespace: str | Identifier, removals: set[str] | None = None, updates: Properties = EMPTY_DICT
    ) -> PropertiesUpdateSummary:
        namespace_str = Catalog.namespace_to_string(namespace)
        current_properties = self.load_namespace_properties(namespace)
        summary, updated_properties = self._get_updated_props_and_update_summary(
            current_properties=current_properties, removals=removals, updates=updates
        )
        conn = self._connect()
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM iceberg_namespace_properties WHERE catalog_name = %s AND namespace = %s",
                        (self.name, namespace_str),
                    )
                    for key, value in updated_properties.items():
                        cur.execute(
                            "INSERT INTO iceberg_namespace_properties "
                            "(catalog_name, namespace, property_key, property_value) VALUES (%s, %s, %s, %s)",
                            (self.name, namespace_str, key, value),
                        )
        finally:
            conn.close()
        return summary

    # -- Views: not needed here, matching SqlCatalog's own stance --

    def create_view(self, *args, **kwargs):
        raise NotImplementedError

    def list_views(self, namespace):
        raise NotImplementedError

    def view_exists(self, identifier) -> bool:
        return False

    def register_view(self, identifier, metadata_location):
        raise NotImplementedError

    def drop_view(self, identifier) -> None:
        raise NotImplementedError

    def load_view(self, identifier):
        raise NotImplementedError
