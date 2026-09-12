"""Parquet preserves exact private state, source-free continuation and failure isolation."""

from pathlib import Path
from typing import Literal

import pandas as pd
import pytest

from marivo.analysis.datasets.base import MaterializedDataset
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.contracts import LocalReceipt
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.operators.delta import LogicalDeltaDataset, MaterializedDeltaDataset
from marivo.semantic._quantile import QuantileMethod
from tests.lazy_distinct_fixtures import CHANNEL
from tests.lazy_parquet_fixtures import operands

pytestmark = pytest.mark.runtime


@pytest.mark.parametrize(
    "kind,method",
    [
        ("distinct", "linear_interpolation@v1"),
        ("distribution", "linear_interpolation@v1"),
        ("distribution", "duckdb_tdigest@v1"),
    ],
)
@pytest.mark.parametrize("checkpoint", ["operands", "delta"])
def test_private_parquet_cold_continuation_preserves_native_result(
    tmp_path: Path,
    kind: Literal["distinct", "distribution"],
    method: QuantileMethod,
    checkpoint: str,
) -> None:
    runtime, current, baseline, database = operands(tmp_path, kind, method)
    expected = current.compare(baseline).attribute(axes=(CHANNEL,)).execute().to_pandas()
    assert float(expected.contribution.sum()) == pytest.approx(1.0)
    saved: tuple[MaterializedDataset, ...]
    delta: LogicalDeltaDataset | MaterializedDeltaDataset
    if checkpoint == "operands":
        saved = (current.execute(), baseline.execute())
    else:
        saved = (current.compare(baseline).execute(),)
    for value in saved:
        record = runtime.store.artifact(value.state.artifact_ref.ref)
        assert record is not None
        assert isinstance(record.descriptor.storage_receipt, LocalReceipt)
        private = tuple(
            part
            for part in record.descriptor.retained_parts
            if "membership" in part.role or "distribution" in part.role
        )
        assert private and all(isinstance(part.storage_receipt, LocalReceipt) for part in private)
        assert all(part.storage_receipt.realized_row_count > 0 for part in private)
    database.rename(tmp_path / "source.offline")
    cold = DatasetRuntime.open(tmp_path, runtime.session_ref)
    recovered = tuple(cold.artifact(value.state.artifact_ref) for value in saved)
    for recovered_value in recovered:
        assert cold.revalidate(recovered_value.state.artifact_ref).storage_authority == "readable"
    if checkpoint == "operands":
        from marivo.analysis.observation.metric import MaterializedMetricDataset

        left, right = recovered
        assert isinstance(left, MaterializedMetricDataset) and isinstance(
            right, MaterializedMetricDataset
        )
        delta = left.compare(right)
    else:
        assert isinstance(recovered[0], MaterializedDeltaDataset)
        delta = recovered[0]
    result = delta.attribute(axes=(CHANNEL,)).execute()
    pd.testing.assert_frame_equal(result.to_pandas(), expected)
    assert cold.statistics.events.get("profile_resolution", 0) == 0
    assert cold.store.resources(cold.session_ref) == ()
    assert not tuple((tmp_path / ".marivo").rglob("*.duckdb"))


@pytest.mark.parametrize("damage", ["missing", "mutated"])
@pytest.mark.parametrize("kind", ["distinct", "distribution"])
def test_private_part_damage_does_not_poison_primary_or_replay_sources(
    tmp_path: Path,
    damage: str,
    kind: Literal["distinct", "distribution"],
) -> None:
    runtime, current, baseline, database = operands(tmp_path, kind, "linear_interpolation@v1")
    result = current.compare(baseline).execute()
    expected = result.to_pandas()
    record = runtime.store.artifact(result.state.artifact_ref.ref)
    assert record is not None
    part = next(
        part
        for part in record.descriptor.retained_parts
        if "membership" in part.role or "distribution" in part.role
    )
    assert isinstance(part.storage_receipt, LocalReceipt)
    path = tmp_path / part.storage_receipt.project_relative_path / "data.parquet"
    if damage == "missing":
        path.unlink()
    else:
        path.chmod(0o600)
        path.write_bytes(b"private-parquet-corruption-canary")
    database.rename(tmp_path / "source.offline")
    cold = DatasetRuntime.open(tmp_path, runtime.session_ref)
    saved = cold.artifact(result.state.artifact_ref)
    assert isinstance(saved, MaterializedDeltaDataset)
    pd.testing.assert_frame_equal(saved.to_pandas(), expected)
    assert cold.revalidate(saved.state.artifact_ref).storage_authority == damage
    with pytest.raises(MaterializationError) as caught:
        saved.attribute(axes=(CHANNEL,)).execute()
    assert "private-parquet-corruption-canary" not in str(caught.value)
    assert cold.last_run_ref is not None
    run = cold.store.run(cold.last_run_ref)
    assert run is not None and run.lifecycle == "failed" and run.output_artifact_ref is None
    assert cold.statistics.events.get("profile_resolution", 0) == 0
    assert cold.store.resources(cold.session_ref) == ()


@pytest.mark.parametrize(
    "kind,method",
    [
        ("distinct", "linear_interpolation@v1"),
        ("distribution", "linear_interpolation@v1"),
        ("distribution", "duckdb_tdigest@v1"),
    ],
)
def test_object_private_parquet_uses_exact_versions_for_source_free_native_scan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: Literal["distinct", "distribution"],
    method: QuantileMethod,
) -> None:
    from marivo.analysis.materialization import inspection
    from marivo.analysis.materialization.contracts import ObjectReceipt
    from marivo.analysis.materialization.storage import ReadPolicy
    from marivo.analysis.materialization.targets import LocalTarget, ObjectTarget, S3Access
    from tests.lazy_candidate_object_fixtures import stub_candidate_objects

    runtime, current, baseline, database = operands(tmp_path, kind, method)
    access = S3Access(
        "archive", "http://127.0.0.1:9", "bucket", "test-key", "private-secret-canary"
    )
    objects = stub_candidate_objects(monkeypatch, access)
    runtime.target, runtime.object_bindings = ObjectTarget("archive"), (access,)
    saved = current.compare(baseline).execute()
    record = runtime.store.artifact(saved.state.artifact_ref.ref)
    assert record is not None and isinstance(record.descriptor.storage_receipt, ObjectReceipt)
    private = tuple(
        part
        for part in record.descriptor.retained_parts
        if "membership" in part.role or "distribution" in part.role
    )
    assert len(private) == 2 and all(
        isinstance(part.storage_receipt, ObjectReceipt) for part in private
    )
    database.rename(tmp_path / "source.offline")
    for part in private:
        inspection._payload_check(tmp_path, record.descriptor, part, (access,), ReadPolicy())
    cold = DatasetRuntime.open(
        tmp_path, runtime.session_ref, target=LocalTarget(), object_bindings=(access,)
    )
    recovered = cold.artifact(saved.state.artifact_ref)
    assert isinstance(recovered, MaterializedDeltaDataset)
    result = recovered.attribute(axes=(CHANNEL,)).execute()
    assert float(result.to_pandas().contribution.sum()) == pytest.approx(1.0)
    assert cold.statistics.events.get("profile_resolution", 0) == 0
    assert cold.store.resources(cold.session_ref) == ()
    assert objects.reads
    with cold.store._read() as connection:
        assert "private-secret-canary" not in "\n".join(connection.iterdump())
