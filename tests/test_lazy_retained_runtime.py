"""Independent numeric and immutable-input references for retained Runtime paths."""

from dataclasses import replace
from pathlib import Path
from typing import Literal

import duckdb
import pytest

from marivo.analysis import grain, time_scope
from marivo.analysis.datasets.base import LogicalDataset
from marivo.analysis.materialization.contracts import EngineReceipt, LocalReceipt
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.observation.predicates import gt
from marivo.analysis.observation.sampling import engine_sample
from marivo.analysis.operators import registry as implementations
from marivo.refs import ref
from tests.lazy_retained_fixtures import setup_retained

pytestmark = pytest.mark.runtime

REVENUE = ref.metric("sales.revenue")
MEAN = ref.metric("sales.mean_amount")
WEIGHTED = ref.metric("sales.weighted_amount")
CUSTOMERS = ref.entity("sales.customers")


@pytest.mark.parametrize("kind", ["local", "engine"])
def test_retained_components_select_current_rows_and_fold_without_origin(
    tmp_path: Path, kind: Literal["local", "engine"]
) -> None:
    fixture = setup_retained(tmp_path, kind)
    source = fixture.sources.observe(
        [REVENUE, MEAN], population=fixture.sources.population(CUSTOMERS)
    )
    checkpoint = source.execute()
    fixture.database.rename(tmp_path / "offline.duckdb")
    selected = checkpoint.where(gt(REVENUE, 10))
    output = selected.aggregate().execute()
    rows = output.to_pandas()
    assert rows["revenue"].tolist() == [140]
    assert rows["mean_amount"].tolist() == pytest.approx([140 / 3])
    record = fixture.runtime.store.artifact(output.state.artifact_ref.ref)
    assert record is not None and len(record.descriptor.retained_parts) == 2
    assert all(
        part.storage_receipt.realized_row_count == 1 for part in record.descriptor.retained_parts
    )
    assert fixture.runtime.statistics.events.get("profile_resolution", 0) == 0
    assert fixture.runtime.statistics.events.get("credential_resolution", 0) == 0
    assert fixture.runtime.store.resources(fixture.runtime.session_ref) == ()
    assert selected.aggregate().execute().state.artifact_ref == output.state.artifact_ref
    assert fixture.runtime.statistics.primary_queries == 0
    assert fixture.runtime.statistics.worker_pid is None


@pytest.mark.parametrize("kind", ["local", "engine"])
def test_retained_rank_limit_filters_component_keys_and_keeps_foldable_checkpoint(
    tmp_path: Path, kind: Literal["local", "engine"]
) -> None:
    fixture = setup_retained(tmp_path, kind)
    checkpoint = fixture.sources.observe(
        [REVENUE, MEAN], population=fixture.sources.population(CUSTOMERS)
    ).execute()
    ranked = checkpoint.rank(checkpoint.fields.metric(REVENUE)).limit(2).execute()
    fixture.database.rename(tmp_path / "offline.duckdb")
    output = ranked.aggregate().execute()
    assert output.to_pandas()["mean_amount"].tolist() == pytest.approx([140 / 3])
    assert "rank" not in [field.name for field in output.schema.columns]
    assert fixture.runtime.store.resources(fixture.runtime.session_ref) == ()


@pytest.mark.parametrize("kind", ["local", "engine"])
def test_projection_does_not_read_unrelated_component_part(
    tmp_path: Path, kind: Literal["local", "engine"]
) -> None:
    fixture = setup_retained(tmp_path, kind)
    checkpoint = fixture.sources.observe([REVENUE, MEAN]).execute()
    record = fixture.runtime.store.artifact(checkpoint.state.artifact_ref.ref)
    assert record is not None
    unwanted = record.descriptor.retained_parts[1].storage_receipt
    if isinstance(unwanted, LocalReceipt):
        (
            tmp_path / unwanted.project_relative_path / unwanted.file_manifest[0].relative_path
        ).unlink()
    else:
        assert isinstance(unwanted, EngineReceipt)
        (tmp_path / unwanted.qualified_relation_ref).unlink()
    fixture.database.rename(tmp_path / "offline.duckdb")
    assert checkpoint.metric(REVENUE).execute().to_pandas()["revenue"].fillna(-1).tolist() == [
        10,
        30,
        100,
        -1,
        0,
        7,
    ]
    with pytest.raises(MaterializationError):
        checkpoint.metric(MEAN).aggregate().execute()
    assert fixture.runtime.store.resources(fixture.runtime.session_ref) == ()


def test_sampled_engine_checkpoint_keeps_exact_realization_in_two_branches(tmp_path: Path) -> None:
    fixture = setup_retained(tmp_path, "engine")
    checkpoint = (
        fixture.sources.population(CUSTOMERS).sample(engine_sample(target_rows=2, seed=3)).execute()
    )
    original = fixture.runtime.store.artifact(checkpoint.state.artifact_ref.ref)
    assert original is not None and original.descriptor.sampling_execution is not None
    identities = checkpoint.to_pandas()["entity_identity"].tolist()
    with duckdb.connect(str(fixture.database)) as backend:
        backend.execute("DROP TABLE customers")
    for metric in (REVENUE, MEAN):
        output = fixture.sources.observe(metric, population=checkpoint).execute()
        record = fixture.runtime.store.artifact(output.state.artifact_ref.ref)
        assert record is not None
        assert record.descriptor.sampling_execution == original.descriptor.sampling_execution
        assert output.to_pandas()["entity_identity"].tolist() == identities
        assert fixture.runtime.statistics.sampling_fences == 0
        assert not any('"customers"' in sql for _, sql in fixture.runtime.statistics.statements)
    assert fixture.runtime.store.resources(fixture.runtime.session_ref) == ()


def test_engine_metric_identity_projection_uses_exact_checkpoint(tmp_path: Path) -> None:
    fixture = setup_retained(tmp_path, "engine")
    metric = fixture.sources.observe(REVENUE, population=fixture.sources.population(CUSTOMERS))
    checkpoint = metric.where(gt(REVENUE, 10)).execute()
    with duckdb.connect(str(fixture.database)) as backend:
        backend.execute("DROP TABLE customers")
    output = fixture.sources.observe(MEAN, population=checkpoint).execute()
    assert output.to_pandas()["entity_identity"].tolist() == [(1,), (2,)]
    assert output.to_pandas()["mean_amount"].tolist() == [20, 100]
    assert not any('"customers"' in sql for _, sql in fixture.runtime.statistics.statements)
    assert fixture.runtime.store.resources(fixture.runtime.session_ref) == ()


def test_source_prefix_transfers_all_parts_once_to_local_fold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = setup_retained(tmp_path)
    registered = implementations.implementation

    def supported(dataset: LogicalDataset) -> implementations.ImplementationRegistration:
        registration = registered(dataset)
        return (
            replace(registration, source_adapter=None)
            if registration.operator_id == "metric.where"
            else registration
        )

    monkeypatch.setattr(implementations, "implementation", supported)
    daily = (
        fixture.sources.observe(
            (REVENUE, MEAN), time_scope=time_scope(start="2026-02-01", end="2026-02-05")
        )
        .with_time_axis(ref.time_dimension("sales.orders.order_time"), grain=grain("day"))
        .aggregate()
    )
    result = daily.where(gt(REVENUE, 0)).rollup(drop_time=True).execute()
    assert result.to_pandas()["mean_amount"].tolist() == pytest.approx([140 / 3])
    assert fixture.runtime.statistics.primary_queries == 1
    assert fixture.runtime.statistics.worker_pid is not None
    assert len(fixture.runtime.statistics.local_handoffs) == 2
    assert (
        fixture.runtime.statistics.local_handoffs[0][1]
        == fixture.runtime.statistics.local_handoffs[1][0]
    )
    assert fixture.runtime.store.resources(fixture.runtime.session_ref) == ()
