"""The only live S3 gate verifies the connector and exact version round-trip."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import pytest

from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.contracts import ObjectReceipt
from marivo.analysis.materialization.object_storage import client, validate_target
from marivo.analysis.materialization.targets import LocalTarget, ObjectTarget, S3Access
from marivo.analysis.operators.association_contracts import CorrelationMethod
from marivo.analysis.operators.delta import MaterializedDeltaDataset
from marivo.semantic._quantile import QuantileMethod
from tests.lazy_distinct_fixtures import CHANNEL
from tests.lazy_parquet_fixtures import operands

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


@pytest.mark.parametrize(
    "kind,method",
    [
        ("distinct", "linear_interpolation@v1"),
        ("distribution", "linear_interpolation@v1"),
        ("distribution", "duckdb_tdigest@v1"),
    ],
)
def test_live_private_bundle_and_source_offline_continuation(
    tmp_path: Path,
    object_connection_access: S3Access,
    kind: Literal["distinct", "distribution"],
    method: QuantileMethod,
) -> None:
    access = object_connection_access
    runtime, current, baseline, database = operands(tmp_path, kind, method)
    runtime.target = ObjectTarget(access.object_store_ref)
    runtime.object_bindings = (access,)
    saved = current.compare(baseline).execute()
    record = runtime.store.artifact(saved.state.artifact_ref.ref)
    assert record is not None
    receipts = (
        record.descriptor.storage_receipt,
        *(part.storage_receipt for part in record.descriptor.retained_parts),
    )
    assert len(receipts) >= 3 and all(isinstance(receipt, ObjectReceipt) for receipt in receipts)
    assert saved.to_pandas().shape[0] > 0
    assert runtime.revalidate(saved.state.artifact_ref).storage_authority == "readable"
    database.rename(tmp_path / "source.offline")
    cold = DatasetRuntime.open(
        tmp_path, runtime.session_ref, target=LocalTarget(), object_bindings=(access,)
    )
    recovered = cold.artifact(saved.state.artifact_ref)
    assert isinstance(recovered, MaterializedDeltaDataset)
    result = recovered.attribute(axes=(CHANNEL,)).execute()
    assert cold.statistics.events.get("profile_resolution", 0) == 0
    assert float(result.to_pandas().contribution.sum()) == pytest.approx(1.0)
    assert cold.revalidate(result.state.artifact_ref).storage_authority == "readable"
    assert cold.store.resources(cold.session_ref) == ()


@pytest.mark.parametrize("method", ["pearson", "spearman", "kendall"])
def test_live_three_process_correlation(
    tmp_path: Path, object_connection_access: S3Access, method: CorrelationMethod
) -> None:
    import os

    from tests.lazy_correlation_acceptance_fixtures import correlation_journey

    access = object_connection_access
    correlation_journey(
        tmp_path,
        method,
        "object",
        {
            **os.environ,
            "MARIVO_TELEMETRY": "off",
            "MARIVO_TEST_S3_ENDPOINT": access.endpoint_url,
            "MARIVO_TEST_S3_BUCKET": access.bucket,
        },
    )
