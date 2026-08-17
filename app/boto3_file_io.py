"""
Boto3FileIO -- a pyiceberg FileIO implementation backed by boto3 instead
of pyarrow's S3FileSystem.

Why this exists: confirmed directly (2026-08-17, via
lakehouse_client.debug_metadata_read(), which fetches the identical file
both ways for comparison) that pyiceberg's default PyArrowFileIO returns
a genuinely EMPTY body -- 0 bytes, no exception raised -- when reading
Iceberg metadata/data files over this app's SeaweedFS connection
(dataos-2-0-pipeline in Oregon, talking to SeaweedFS's public HTTPS
endpoint in Singapore). Plain boto3 GetObject against the exact same
bucket/key, same endpoint, returns the real file correctly (5085 bytes,
valid JSON). This is a real bug isolated to pyarrow's S3 client on this
specific connection path -- not a config issue, not something the
existing s3.region/s3.path-style-access properties fix.

Scope: read-only. This app only ever READS Iceberg tables (for
dashboard display) -- it never writes them; writes happen exclusively
in the spike DAG via spike/dags/pg_iceberg_catalog.py, which talks to
SeaweedFS over the INTERNAL network (same region) where pyarrow works
fine -- this bug is specific to the cross-region public path this app
alone needs. new_output()/OutputFile.create() are implemented for
interface completeness (FileIO is an ABC with all three methods
abstract) but are never actually exercised by anything this app does.

Buffers each file fully into memory rather than true streaming -- fine
for this use case (small demo-scale Parquet/JSON files), not something
to reuse as-is for large production tables without revisiting.
"""
from __future__ import annotations

import io as _io

import boto3

from pyiceberg.io import FileIO, InputFile, InputStream, OutputFile
from pyiceberg.typedef import Properties


def _parse_s3_uri(location: str) -> tuple[str, str]:
    without_scheme = location.removeprefix("s3://").removeprefix("s3a://")
    bucket, _, key = without_scheme.partition("/")
    return bucket, key


class Boto3InputFile(InputFile):
    def __init__(self, location: str, s3_client):
        super().__init__(location)
        self._s3 = s3_client
        self._bucket, self._key = _parse_s3_uri(location)
        self._cached_bytes: bytes | None = None

    def _fetch(self) -> bytes:
        if self._cached_bytes is None:
            obj = self._s3.get_object(Bucket=self._bucket, Key=self._key)
            self._cached_bytes = obj["Body"].read()
        return self._cached_bytes

    def __len__(self) -> int:
        return len(self._fetch())

    def exists(self) -> bool:
        try:
            self._s3.head_object(Bucket=self._bucket, Key=self._key)
            return True
        except Exception:  # noqa: BLE001
            return False

    def open(self, seekable: bool = True) -> InputStream:
        # io.BytesIO already satisfies pyiceberg's InputStream protocol
        # (read/seek/tell/close/__enter__/__exit__) natively.
        return _io.BytesIO(self._fetch())


class _Boto3OutputStream:
    """Buffers writes in memory, uploads on close() -- see module
    docstring: not exercised by this app, implemented for completeness
    only since FileIO/OutputFile are abstract base classes."""

    def __init__(self, s3_client, bucket: str, key: str):
        self._s3 = s3_client
        self._bucket = bucket
        self._key = key
        self._buffer = _io.BytesIO()

    def write(self, b: bytes) -> int:
        return self._buffer.write(b)

    def tell(self) -> int:
        return self._buffer.tell()

    def close(self) -> None:
        self._buffer.seek(0)
        self._s3.put_object(Bucket=self._bucket, Key=self._key, Body=self._buffer.read())
        self._buffer.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()


class Boto3OutputFile(OutputFile):
    def __init__(self, location: str, s3_client):
        super().__init__(location)
        self._s3 = s3_client
        self._bucket, self._key = _parse_s3_uri(location)

    def __len__(self) -> int:
        try:
            resp = self._s3.head_object(Bucket=self._bucket, Key=self._key)
            return resp["ContentLength"]
        except Exception:  # noqa: BLE001
            return 0

    def exists(self) -> bool:
        try:
            self._s3.head_object(Bucket=self._bucket, Key=self._key)
            return True
        except Exception:  # noqa: BLE001
            return False

    def to_input_file(self) -> InputFile:
        return Boto3InputFile(self.location, self._s3)

    def create(self, overwrite: bool = False):
        if not overwrite and self.exists():
            raise FileExistsError(f"File already exists: {self.location}")
        return _Boto3OutputStream(self._s3, self._bucket, self._key)


class Boto3FileIO(FileIO):
    """Set via the "py-io-impl" catalog property (pyiceberg's documented
    plug-in point, checked before any scheme-based FileIO inference) --
    see app/lakehouse_client.py's _iceberg_catalog()."""

    def __init__(self, properties: Properties):
        super().__init__(properties)
        self._s3 = boto3.client(
            "s3",
            endpoint_url=properties.get("s3.endpoint"),
            aws_access_key_id=properties.get("s3.access-key-id", "any"),
            aws_secret_access_key=properties.get("s3.secret-access-key", "any"),
        )

    def new_input(self, location: str) -> InputFile:
        return Boto3InputFile(location, self._s3)

    def new_output(self, location: str) -> OutputFile:
        return Boto3OutputFile(location, self._s3)

    def delete(self, location) -> None:
        loc = location.location if hasattr(location, "location") else location
        bucket, key = _parse_s3_uri(loc)
        self._s3.delete_object(Bucket=bucket, Key=key)
