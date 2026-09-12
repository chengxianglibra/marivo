"""Real Association publication and guarded source/private pair execution."""

from pathlib import Path

import pytest

from marivo.analysis.materialization.targets import LocalTarget
from marivo.analysis.observation.metric import LogicalMetricDataset, MaterializedMetricDataset
from marivo.analysis.operators.association_contracts import CorrelationMethod
from marivo.refs import ref
from tests.lazy_local_fixtures import pandas_methods, setup_local

pytestmark = pytest.mark.runtime


@pytest.mark.parametrize("method", ["pearson", "spearman", "kendall"])
@pytest.mark.parametrize("retained", [False, True])
def test_real_association(tmp_path: Path, method: CorrelationMethod, retained: bool) -> None:
    runtime, sources, database = setup_local(tmp_path)
    source: LogicalMetricDataset | MaterializedMetricDataset = sources.observe(
        [ref.metric("sales.revenue"), ref.metric("sales.mean_amount")]
    )
    if retained:
        runtime.target = LocalTarget()
        assert isinstance(source, LogicalMetricDataset)
        source = source.execute()
        database.rename(tmp_path / "source.offline")
        runtime.target = LocalTarget()
    logical = source.correlate(method=method)
    result = logical.execute()
    assert result.to_pandas().iloc[0].coefficient == pytest.approx(1.0)
    assert result.evidence_digest.finding_count == 1
    assert len(result.findings().items) == 1
    continued = result.rank(result.fields.get("coefficient")).limit(1).execute()
    assert len(continued.to_pandas()) == 1
    assert logical.execute().state.artifact_ref == result.state.artifact_ref


@pytest.mark.parametrize("method", ["pearson", "spearman", "kendall"])
def test_authored_pair_order_private_transfers_and_direct_handoffs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, method: CorrelationMethod
) -> None:
    from collections.abc import Iterator

    import ibis.expr.types as ir
    import pyarrow as pa
    from ibis.backends import BaseBackend

    from marivo.analysis.evidence._dataset_types import AssociationFindingSubjectV1
    from marivo.analysis.materialization.admission import DatasetRuntime

    runtime, sources, _ = setup_local(tmp_path)
    observed: list[tuple[str, ...]] = []
    original = DatasetRuntime._batches

    def inspect(
        self: DatasetRuntime, backend: BaseBackend, expression: ir.Table, batch_rows: int
    ) -> Iterator[pa.RecordBatch]:
        for batch in original(self, backend, expression, batch_rows):
            observed.append(tuple(batch.schema.names))
            assert "entity_identity" not in batch.schema.names
            assert not any(pa.types.is_struct(f.type) for f in batch.schema)
            yield batch

    monkeypatch.setattr(DatasetRuntime, "_batches", inspect)
    keys = ("sales.revenue", "sales.weighted_amount", "sales.mean_amount")
    association = sources.observe([ref.metric(key) for key in keys]).correlate(method=method)
    complete = association.execute()
    expected = [("metric:" + keys[a], "metric:" + keys[b]) for a, b in ((0, 1), (0, 2), (1, 2))]
    frame = complete.to_pandas()
    assert list(zip(frame.metric_key_a, frame.metric_key_b, strict=True)) == expected
    subjects = [f.subject for f in complete.findings().items]
    assert all(isinstance(s, AssociationFindingSubjectV1) for s in subjects)
    assert len(set(subjects)) == 3
    selected = association.limit(2).execute()
    selected_frame = selected.to_pandas()
    assert (
        list(zip(selected_frame.metric_key_a, selected_frame.metric_key_b, strict=True))
        == expected[:2]
    )
    assert observed
    if method == "kendall":
        links = runtime.statistics.local_handoffs
        assert len(links) == 2 and links[0][1] == links[1][0]


@pytest.mark.parametrize("method", ["pearson", "spearman", "kendall"])
@pytest.mark.parametrize("kind", ["local", "object"])
def test_nonidentity_checkpoint_uses_local_exact_method(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, method: CorrelationMethod, kind: str
) -> None:
    import duckdb

    from marivo.analysis.materialization.targets import ObjectTarget, S3Access

    runtime, sources, database = setup_local(tmp_path)
    with duckdb.connect(str(database), config={"threads": 1}) as connection:
        connection.execute("UPDATE orders SET channel = CAST(id AS VARCHAR)")
    if kind == "object":
        from tests.lazy_candidate_object_fixtures import stub_candidate_objects

        access = S3Access(
            "fixture", "http://127.0.0.1:9", "bucket", "private-key", "private-secret"
        )
        stub_candidate_objects(monkeypatch, access)
        runtime.object_bindings = (access,)
        runtime.target = ObjectTarget(access.object_store_ref)
    metric = (
        sources.observe([ref.metric("sales.revenue"), ref.metric("sales.mean_amount")])
        .with_dimensions(ref.dimension("sales.orders.channel"))
        .aggregate()
        .execute()
    )
    database.rename(tmp_path / "source.offline")
    runtime.target = LocalTarget()
    with pandas_methods("metric.correlate"):
        result = metric.correlate(method=method).execute()
    assert abs(result.to_pandas().coefficient.iloc[0]) == pytest.approx(1.0)
    assert runtime.statistics.worker_pid is not None
    assert runtime.statistics.primary_queries == 0


@pytest.mark.parametrize("method", ["pearson", "spearman", "kendall"])
def test_lag_filter_preserves_original_search_and_selection(
    tmp_path: Path, method: CorrelationMethod
) -> None:
    from marivo._temporal import builtin_grain
    from marivo.analysis import time_scope
    from marivo.analysis.observation.predicates import eq

    runtime, sources, _ = setup_local(tmp_path)
    metric = (
        sources.observe(
            [ref.metric("sales.revenue"), ref.metric("sales.mean_amount")],
            time_scope=time_scope(start="2026-02-01", end="2026-02-05"),
        )
        .with_time_axis(ref.time_dimension("sales.orders.order_time"), grain=builtin_grain("day"))
        .aggregate()
    )
    association = metric.correlate(method=method, lag_range=range(-2, 3))
    original = association.execute()
    filtered = (
        original.where(eq(original.fields.get("selected_for_pair"), False)).limit(1).execute()
    )
    frame = filtered.to_pandas()
    assert not frame.selected_for_pair.any()
    a = runtime.store.artifact(original.state.artifact_ref.ref)
    b = runtime.store.artifact(filtered.state.artifact_ref.ref)
    assert a is not None and b is not None
    before, after = a.descriptor.association_evidence, b.descriptor.association_evidence
    assert before is not None and after is not None
    assert before.original_candidate_count == after.original_candidate_count == 5
    assert before.complete_pair_range == after.complete_pair_range
    assert before.null_pair_range == after.null_pair_range


def test_finding_cap_is_disclosed_and_cold_pagination_is_complete(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import duckdb

    from marivo._temporal import builtin_grain
    from marivo.analysis import time_scope
    from marivo.analysis.materialization.admission import DatasetRuntime
    from marivo.analysis.operators.association import MaterializedAssociationDataset

    runtime, sources, database = setup_local(tmp_path)
    with duckdb.connect(str(database), config={"threads": 1}) as connection:
        connection.execute("DELETE FROM orders")
        connection.execute("""
            INSERT INTO orders (id, amount, weight, day, channel)
            SELECT i * 2 + j, j + 1, 1, DATE '2026-02-01' + CAST(j AS INTEGER),
                   'c' || lpad(CAST(i AS VARCHAR), 4, '0')
            FROM range(1001) AS series(i), range(2) AS observations(j)
        """)
    result = (
        sources.observe(
            [ref.metric("sales.revenue"), ref.metric("sales.mean_amount")],
            time_scope=time_scope(start="2026-02-01", end="2026-02-03"),
        )
        .with_dimensions(ref.dimension("sales.orders.channel"))
        .with_time_axis(
            ref.time_dimension("sales.orders.order_time"),
            grain=builtin_grain("day"),
        )
        .aggregate()
        .correlate()
        .execute()
    )
    record = runtime.store.artifact(result.state.artifact_ref.ref)
    assert record is not None and record.descriptor.association_evidence is not None
    summary = record.descriptor.association_evidence
    assert (
        summary.eligible_finding_count,
        summary.emitted_finding_count,
        summary.finding_truncated,
    ) == (1001, 1000, True)
    assert record.descriptor.storage_receipt.realized_row_count == 1001
    result.show()
    assert "eligible=1001; emitted=1000; truncated=True" in capsys.readouterr().out
    cold = DatasetRuntime.open(tmp_path, runtime.session_ref).artifact(
        result.state.artifact_ref.ref
    )
    assert isinstance(cold, MaterializedAssociationDataset)
    cursor = None
    identities: list[str] = []
    channels: list[object] = []
    while True:
        page = cold.findings(limit=100, cursor=cursor)
        identities.extend(finding.finding_id for finding in page.items)
        channels.extend(finding.coordinates[0].value for finding in page.items)
        if not page.has_more:
            break
        cursor = page.next_cursor
    assert len(set(identities)) == 1000
    assert channels == [f"c{i:04d}" for i in range(1000)]
    assert cold.finding(identities[-1]).finding_id == identities[-1]
