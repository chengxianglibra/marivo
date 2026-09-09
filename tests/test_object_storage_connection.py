"""The only live S3 gate verifies the connector and exact version round-trip."""

from __future__ import annotations

import pytest

from marivo.analysis.materialization.object_storage import client, validate_target
from marivo.analysis.materialization.targets import S3Access

pytestmark = pytest.mark.object_connection


def test_versioned_object_storage_connection(object_connection_access: S3Access) -> None:
    access = object_connection_access
    validate_target(access)
    with client(access) as s3:
        first = s3.put_object(Bucket=access.bucket, Key="connection.bin", Body=b"first")
        second = s3.put_object(Bucket=access.bucket, Key="connection.bin", Body=b"second")
        version = first["VersionId"]
        assert version and version != "null" and version != second["VersionId"]
        response = s3.get_object(Bucket=access.bucket, Key="connection.bin", VersionId=version)
        try:
            assert response["Body"].read() == b"first"
            assert response["VersionId"] == version
        finally:
            response["Body"].close()
        for value in (first, second):
            s3.delete_object(
                Bucket=access.bucket, Key="connection.bin", VersionId=value["VersionId"]
            )
        assert not s3.list_object_versions(Bucket=access.bucket).get("Versions")
