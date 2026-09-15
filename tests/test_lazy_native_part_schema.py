"""Native scans preserve receipt-bound schemas when Arrow nullability is erased."""

from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Literal

import duckdb
import ibis
import pytest

from marivo.analysis import grain, time_scope
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.errors import IntegrityError
from marivo.analysis.materialization.parquet_scan import attach_parquet_scan
from marivo.analysis.materialization.reads import part_schema
from marivo.analysis.materialization.targets import LocalTarget, ObjectTarget, S3Access
from marivo.analysis.observation.metric import LogicalMetricDataset
from marivo.analysis.operators.delta import MaterializedDeltaDataset
from marivo.refs import ref
from tests.lazy_candidate_object_fixtures import stub_candidate_objects
from tests.lazy_local_fixtures import REVENUE, pandas_methods, setup_local

pytestmark = pytest.mark.runtime


def _pandas_delta(
    project: Path, storage: Literal["local", "object"], monkeypatch: pytest.MonkeyPatch
) -> tuple[DatasetRuntime, MaterializedDeltaDataset, Path]:
    runtime, sources, database = setup_local(project)
    with duckdb.connect(str(database)) as connection:
        connection.execute("DELETE FROM orders")
        connection.executemany(
            "INSERT INTO orders (id, day, amount) VALUES (?, ?, ?)",
            [
                (1, "2026-02-01", 10.0),
                (2, "2026-02-02", 12.0),
                (3, "2026-02-03", 15.0),
                (4, "2026-02-04", 9.0),
            ],
        )
    if storage == "object":
        access = S3Access("archive", "https://objects.invalid", "bucket", "key", "secret")
        stub_candidate_objects(monkeypatch, access)
        runtime.target, runtime.object_bindings = ObjectTarget("archive"), (access,)
    population = sources.population(ref.entity("sales.orders"))

    def series(start: str, end: str) -> LogicalMetricDataset:
        return (
            sources.observe(
                REVENUE, population=population, time_scope=time_scope(start=start, end=end)
            )
            .with_time_axis(ref.time_dimension("sales.orders.order_time"), grain=grain("day"))
            .aggregate()
        )

    current, baseline = series("2026-02-03", "2026-02-05"), series("2026-02-01", "2026-02-03")
    with pandas_methods("metric.compare"):
        delta = current.compare(baseline).execute()
    assert runtime.statistics.worker_pid is not None
    return runtime, delta, database


@pytest.mark.parametrize("storage", ["local", "object"])
def test_pandas_delta_parts_support_native_rank_without_origin(
    tmp_path: Path, storage: Literal["local", "object"], monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, delta, database = _pandas_delta(tmp_path, storage, monkeypatch)
    record = runtime.store.artifact(delta.state.artifact_ref.ref)
    assert record is not None and len(record.descriptor.retained_parts) == 2
    for part in record.descriptor.retained_parts:
        schema = part_schema(tmp_path, part, bindings=runtime.object_bindings)
        assert not schema.field("comparison_ordinal").nullable
        assert not schema.field(schema.names[-1]).nullable
    database.rename(tmp_path / "warehouse.offline")
    cold = DatasetRuntime.open(
        tmp_path, runtime.session_ref, target=LocalTarget(), object_bindings=runtime.object_bindings
    )
    recovered = cold.artifact(delta.state.artifact_ref)
    assert isinstance(recovered, MaterializedDeltaDataset)
    ranked = recovered.rank(recovered.fields.get("delta")).execute()
    frame = ranked.to_pandas()
    assert frame["delta"].tolist() == [5.0, -3.0]
    assert frame["current_value"].tolist() == [15.0, 9.0]
    assert frame["baseline_value"].tolist() == [10.0, 12.0]
    assert frame["comparison_ordinal"].tolist() == [0, 1]
    assert frame["rank"].tolist() == [1, 2]
    assert frame["current_time"].tolist() == [date(2026, 2, 3), date(2026, 2, 4)]
    assert cold.statistics.events.get("profile_resolution", 0) == 0
    assert cold.statistics.primary_queries == 1 and cold.statistics.worker_pid is None
    assert cold.store.resources(cold.session_ref) == ()
    integrity = cold.revalidate(ranked.state.artifact_ref)
    assert (
        integrity.artifact_integrity,
        integrity.storage_authority,
        integrity.evidence_integrity,
    ) == ("valid", "readable", "valid")


@pytest.mark.parametrize("storage", ["local", "object"])
def test_native_part_scan_rejects_wrong_physical_schema_fingerprint(
    tmp_path: Path, storage: Literal["local", "object"], monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, delta, _ = _pandas_delta(tmp_path, storage, monkeypatch)
    record = runtime.store.artifact(delta.state.artifact_ref.ref)
    assert record is not None
    receipt = replace(
        record.descriptor.retained_parts[0].storage_receipt, schema_fingerprint="0" * 64
    )
    backend = ibis.duckdb.connect()
    try:
        with pytest.raises(IntegrityError, match="Parquet part schema differs"):
            attach_parquet_scan(
                backend, tmp_path, receipt, bindings=runtime.object_bindings, verify_schema=True
            )
    finally:
        backend.disconnect()
