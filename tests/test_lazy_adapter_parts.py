"""Primary/part closure isolation, sampling receipts and combined adapter budgets."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import duckdb
import pyarrow as pa
import pytest

from marivo.analysis.materialization import contracts as c
from marivo.analysis.materialization import reads
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.storage import ReadPolicy
from marivo.analysis.materialization.targets import LocalTarget, ObjectTarget
from marivo.analysis.observation.sampling import engine_sample
from marivo.refs import ref
from tests.lazy_adapter_fixtures import setup_adapter
from tests.lazy_local_fixtures import REVENUE, setup_local

pytestmark = pytest.mark.runtime


def _part_schema(project: Path, receipt: c.StorageReceipt) -> pa.Schema:
    import pyarrow.parquet as pq

    assert isinstance(receipt, c.LocalReceipt)
    return pq.read_schema(project / receipt.project_relative_path / "data.parquet")


@pytest.mark.parametrize("kind", ["engine"])
def test_only_selected_part_is_read_and_missing_required_part_fails(
    tmp_path: Path,
    request: pytest.FixtureRequest,
    kind: Literal["engine", "object"],
) -> None:
    fixture = setup_adapter(tmp_path, kind)
    result = fixture.sources.observe(
        [ref.metric("sales.revenue"), ref.metric("sales.mean_amount")],
        population=fixture.sources.population(ref.entity("sales.customers")),
    ).execute()
    record = fixture.runtime.store.artifact(result.state.artifact_ref.ref)
    assert record is not None and len(record.descriptor.retained_parts) == 2
    selected, unrelated = record.descriptor.retained_parts
    schema = _part_schema(tmp_path, selected.storage_receipt)
    if isinstance(unrelated.storage_receipt, c.LocalReceipt):
        (tmp_path / unrelated.storage_receipt.project_relative_path / "data.parquet").unlink()
    assert len(result.to_pandas()) == 4
    batches = tuple(
        reads.read_part_batches(tmp_path, selected, expected_schema=schema, bindings=())
    )
    assert pa.Table.from_batches(batches).num_rows == 4
    with pytest.raises(MaterializationError):
        tuple(
            reads.payload_batches(
                tmp_path,
                unrelated.storage_receipt,
                policy=ReadPolicy(),
                bindings=(),
            )
        )


@pytest.mark.parametrize("kind", ["engine"])
def test_sampling_state_round_trip_is_atomic_with_primary(
    tmp_path: Path,
    request: pytest.FixtureRequest,
    kind: Literal["engine", "object"],
) -> None:
    fixture = setup_adapter(tmp_path, kind)
    logical = fixture.sources.population(ref.entity("sales.customers")).sample(
        engine_sample(target_rows=2, seed=3)
    )
    result = logical.execute()
    record = fixture.runtime.store.artifact(result.state.artifact_ref.ref)
    assert record is not None and record.descriptor.sampling_execution is not None
    assert record.descriptor.retained_parts[0].role == "population_sampling_state"
    assert len(result.to_pandas()) == 2
    assert fixture.runtime.store.resources(fixture.runtime.session_ref) == ()


def test_source_rank_with_parts_preserves_primary_order(tmp_path: Path) -> None:
    fixture = setup_adapter(tmp_path, "engine")
    logical = fixture.sources.observe(ref.metric("sales.revenue"))
    result = logical.rank(logical.fields.metric(ref.metric("sales.revenue"))).limit(2).execute()
    assert result.to_pandas()["revenue"].tolist() == [100, 30]


@pytest.mark.parametrize("kind", ["engine"])
def test_complete_large_retained_collection_and_offline_continuation(
    tmp_path: Path,
    request: pytest.FixtureRequest,
    kind: str,
) -> None:
    runtime, sources, database = setup_local(tmp_path)
    runtime.target = LocalTarget() if kind == "engine" else ObjectTarget("fixture")
    runtime.object_bindings = ()
    with duckdb.connect(str(database)) as db:
        db.execute("INSERT INTO orders (id, amount) SELECT i + 1000, 1.0 FROM range(100001) t(i)")
    result = sources.observe(REVENUE).execute()
    assert result.state.realized_row_count > 100000
    database.rename(tmp_path / "warehouse.offline")
    assert len(result.to_pandas()) == result.state.realized_row_count
    narrowed = result.rank(result.fields.metric(REVENUE)).limit(1)
    if kind == "engine":
        output = narrowed.execute()
        assert len(output.to_pandas()) == 1
        assert runtime.statistics.transferred_rows == 1
        assert runtime.statistics.events.get("local_execution_started", 0) == 0
    else:
        with pytest.raises(MaterializationError):
            narrowed.execute()
        assert runtime.statistics.local_handoffs == ()
    assert runtime.store.resources(runtime.session_ref) == ()
