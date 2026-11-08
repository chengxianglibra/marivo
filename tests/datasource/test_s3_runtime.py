"""Private S3 Parquet acceptance against the explicit MinIO environment."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import nullcontext
from pathlib import Path
from uuid import uuid4

import pyarrow as pa
import pyarrow.fs as fs
import pyarrow.parquet as pq
import pytest

import marivo.datasource as md
from marivo.datasource.runtime import load_datasource, open_backend
from tests.datasource.environment.credentials import password

pytestmark = [
    pytest.mark.runtime,
    pytest.mark.skipif(
        os.environ.get("MARIVO_MINIO_TEST") != "1",
        reason="Set MARIVO_MINIO_TEST=1 after starting the minio profile",
    ),
]
ENDPOINT = "127.0.0.1:19000"


@pytest.fixture
def private_bucket() -> Iterator[str]:
    filesystem = fs.S3FileSystem(
        access_key="analysis_admin",
        secret_key=password(),
        region="us-east-1",
        endpoint_override=ENDPOINT,
        scheme="http",
        allow_bucket_creation=True,
        allow_bucket_deletion=True,
    )
    bucket = "marivo-" + uuid4().hex
    filesystem.create_dir(bucket)
    try:
        for key, ids, amounts in (
            ("private/part1.parquet", [1, 2], [10, 20]),
            ("private/part2.parquet", [3], [30]),
            ("outside/part.parquet", [4], [40]),
        ):
            pq.write_table(
                pa.table({"id": ids, "amount": amounts}), f"{bucket}/{key}", filesystem=filesystem
            )
        yield bucket
    finally:
        filesystem.delete_dir(bucket)


@pytest.mark.parametrize("host_resolver", [False, True])
def test_private_parquet_and_glob_with_scoped_credentials(
    private_bucket: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, host_resolver: bool
) -> None:
    scope = f"s3://{private_bucket}/private/"
    values = {"MINIO_KEY": "analysis_admin", "MINIO_SECRET": password()}
    requests: list[md.CredentialRequest] = []

    class Resolver:
        def resolve(self, request: md.CredentialRequest) -> md.SecretValue:
            requests.append(request)
            return md.SecretValue(values[request.reference])

    for name, value in values.items():
        monkeypatch.setenv(name, "wrong-env-value" if host_resolver else value)
    monkeypatch.setenv("MARIVO_PERSIST_CREDENTIALS", "0")
    spec = md.duckdb(
        name="lake",
        secrets=[
            md.s3_secret(
                scope=scope,
                region="us-east-1",
                key_id=md.env("MINIO_KEY"),
                secret=md.env("MINIO_SECRET"),
                endpoint=ENDPOINT,
                url_style="path",
                use_ssl=False,
            )
        ],
    )
    md.register(spec, project_root=tmp_path)
    with md.credential_scope(resolver=Resolver()) if host_resolver else nullcontext():
        for path, expected in (
            (scope + "part1.parquet", [(1, 10), (2, 20)]),
            (scope + "*.parquet", [(1, 10), (2, 20), (3, 30)]),
        ):
            inspection = md.inspect(spec.ref, md.parquet(path), workspace_dir=tmp_path)
            snapshot = inspection.sample(
                scope=md.unpruned(max_rows=10, timeout_seconds=30),
                columns=("id", "amount"),
                persist_values=True,
            )
            assert (
                sorted((row["id"], row["amount"]) for row in snapshot.retained_values) == expected
            )
        # Native endpoint defaults route the out-of-scope request to this MinIO;
        # they supply no credentials. The declared private-scope secret must not match.
        built = open_backend(load_datasource("lake", tmp_path), project_root=tmp_path)
        try:
            built.backend.raw_sql("SET s3_endpoint = ?", parameters=[ENDPOINT])
            built.backend.raw_sql("SET s3_url_style = 'path'")
            built.backend.raw_sql("SET s3_use_ssl = false")
            with pytest.raises(Exception):
                built.backend.read_parquet(f"s3://{private_bucket}/outside/part.parquet").execute()
        finally:
            built.disconnect()
    if host_resolver:
        assert {request.reference for request in requests} == {"MINIO_KEY", "MINIO_SECRET"}
        assert all(
            request.fields == (f"s3@{scope}.key_id",)
            if request.reference == "MINIO_KEY"
            else request.fields == (f"s3@{scope}.secret",)
            for request in requests
        )
