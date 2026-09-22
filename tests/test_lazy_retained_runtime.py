"""Independent numeric and immutable-input references for retained Runtime paths."""

from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Literal

import duckdb
import pytest

from marivo.analysis import grain, time_scope
from marivo.analysis.datasets.base import LogicalDataset
from marivo.analysis.materialization.contracts import LocalReceipt
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.observation.predicates import gt
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
    assert fixture.runtime.statistics.events.get("local_execution_started", 0) == 0


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


def test_engine_metric_identity_projection_uses_exact_checkpoint(tmp_path: Path) -> None:
    fixture = setup_retained(tmp_path, "engine")
    metric = fixture.sources.observe(REVENUE, population=fixture.sources.population(CUSTOMERS))
    checkpoint = metric.where(gt(REVENUE, 10)).execute()
    retained = fixture.runtime.store.artifact(checkpoint.state.artifact_ref.ref)
    assert retained is not None
    for part in retained.descriptor.retained_parts:
        receipt = part.storage_receipt
        assert isinstance(receipt, LocalReceipt)
        (tmp_path / receipt.project_relative_path / "data.parquet").unlink()
    with duckdb.connect(str(fixture.database)) as backend:
        backend.execute("DROP TABLE customers")
    output = fixture.sources.observe(MEAN, population=checkpoint).execute()
    assert output.to_pandas()["entity_identity"].tolist() == [(1,), (2,)]
    assert output.to_pandas()["mean_amount"].tolist() == [20, 100]
    assert not any('"customers"' in sql for _, sql in fixture.runtime.statistics.statements)
    assert not any(
        name == "engine_check.part_schema" for name, _ in fixture.runtime.statistics.statements
    )
    assert fixture.runtime.store.resources(fixture.runtime.session_ref) == ()


def test_source_prefix_transfers_all_parts_once_to_local_fold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = setup_retained(tmp_path)
    registered = implementations.implementation

    def supported(dataset: LogicalDataset) -> implementations.ImplementationRegistration:
        registration = registered(dataset)
        return (
            replace(registration, backends=())
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
    assert fixture.runtime.statistics.events.get("local_execution_started", 0) > 0
    assert len(fixture.runtime.statistics.local_handoffs) == 2
    assert (
        fixture.runtime.statistics.local_handoffs[0][1]
        == fixture.runtime.statistics.local_handoffs[1][0]
    )
    assert fixture.runtime.store.resources(fixture.runtime.session_ref) == ()


def test_retained_fold_groups_multi_unit_buckets_on_the_source_grid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The local fold oracle must agree with the source grid for count > 1.

    Both the source observation and the retained continuation fold multi-unit
    widths, so reverting either owner changes an observed literal here.  The
    expectations are hand-computed from the declared read authority (DuckDB
    reads the naive source strings as UTC) rendered into the America/New_York
    boundary zone, whose spring-forward lands on 2026-03-08 and fall-back on
    2026-11-01.  One row deliberately sits just inside a 6hour boundary so the
    source offset is observable rather than rounded away:

        09:30Z -> 05:30 local -> 00:00 (6hour), 00:00 (12hour)
        15:00Z -> 11:00 local -> 06:00 (6hour), 00:00 (12hour)
        19:00Z -> 15:00 local -> 12:00 (6hour), 12:00 (12hour)
        15:00Z -> 10:00 local -> 06:00 (6hour), 00:00 (12hour)
        16:00Z -> 11:00 local -> 06:00 (6hour), 00:00 (12hour)
    """
    from marivo.analysis.materialization.admission import DatasetRuntime
    from marivo.analysis.materialization.store import SessionStore
    from marivo.analysis.operators.rollup import bucket_bounds
    from tests.lazy_temporal_fixtures import AXIS as ORDER_TIME
    from tests.lazy_temporal_fixtures import temporal_fixture

    with temporal_fixture(
        tmp_path,
        parse=None,
        report_zone="America/New_York",
        values=(
            "2026-03-08 09:30:00",
            "2026-03-08 15:00:00",
            "2026-03-08 19:00:00",
            "2026-11-01 15:00:00",
            "2026-11-01 16:00:00",
        ),
    ) as fixture:
        registry, sidecar = fixture.registry, fixture.sidecar
    store = SessionStore(tmp_path)
    session = store.create_session("multi-unit", report_timezone_name="America/New_York")
    runtime = DatasetRuntime(store, session.session_ref)
    sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    six_hourly = (
        sources.observe(REVENUE, time_scope=time_scope(start="2026-03-01", end="2026-12-01"))
        .with_time_axis(ref.time_dimension(ORDER_TIME), grain=grain("hour", count=6))
        .aggregate()
    )
    # Hand-computed source-side literals: the compiled civil-midnight expression.
    checkpoint = six_hourly.execute()
    source = checkpoint.to_pandas()
    assert source["order_time"].tolist() == [
        datetime(2026, 3, 8, 0, 0),
        datetime(2026, 3, 8, 6, 0),
        datetime(2026, 3, 8, 12, 0),
        datetime(2026, 11, 1, 6, 0),
    ]
    assert source["revenue"].tolist() == [1.0, 2.0, 3.0, 9.0]

    # The retained continuation folds 6hour coordinates into 12hour buckets, so
    # the pandas oracle is the only code that can produce these literals.
    registered = implementations.implementation

    def supported(dataset: LogicalDataset) -> implementations.ImplementationRegistration:
        registration = registered(dataset)
        return (
            replace(registration, backends=())
            if registration.operator_id == "metric.where"
            else registration
        )

    monkeypatch.setattr(implementations, "implementation", supported)
    retained = (
        checkpoint.where(gt(REVENUE, 0)).rollup(grain=grain("hour", count=12)).execute().to_pandas()
    )
    assert runtime.statistics.events.get("local_execution_started", 0) > 0
    assert retained["order_time"].tolist() == [
        datetime(2026, 3, 8, 0, 0),
        datetime(2026, 3, 8, 12, 0),
        datetime(2026, 11, 1, 0, 0),
    ]
    assert retained["revenue"].tolist() == [3.0, 3.0, 9.0]

    # The fold oracle itself, on the persisted naive coordinate, is the second
    # owner of the same grid; assert it directly so a compensating change inside
    # the source expression cannot hide a divergence here.
    assert bucket_bounds(datetime(2026, 3, 8, 6, 0), grain("hour", count=12), None) == (
        datetime(2026, 3, 8, 0, 0),
        datetime(2026, 3, 8, 12, 0),
    )
    assert bucket_bounds(datetime(2026, 3, 8, 12, 0), grain("hour", count=12), None) == (
        datetime(2026, 3, 8, 12, 0),
        datetime(2026, 3, 9, 0, 0),
    )
    assert bucket_bounds(datetime(2026, 11, 1, 6, 0), grain("hour", count=12), None) == (
        datetime(2026, 11, 1, 0, 0),
        datetime(2026, 11, 1, 12, 0),
    )
    assert runtime.store.resources(runtime.session_ref) == ()
