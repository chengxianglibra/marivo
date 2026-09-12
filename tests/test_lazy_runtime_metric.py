"""Runtime roots use the governed lazy graph and immutable retained state."""

from dataclasses import replace
from pathlib import Path

import pytest

from marivo.analysis import runtime_metric as rm
from marivo.analysis.datasets.descriptors import _RuntimeMetricFieldIdentity
from marivo.analysis.datasets.errors import (
    DatasetConstructionError,
    DatasetFieldSelectionError,
    DatasetOwnershipError,
)
from marivo.analysis.observation.contracts import metric_definition
from marivo.analysis.observation.predicates import gt
from marivo.refs import ref
from tests.lazy_observation_fixtures import make_sources
from tests.lazy_retained_fixtures import setup_retained
from tests.lazy_runtime_metric_fixtures import AMOUNT, CUSTOMERS, REVENUE, expressions


def test_runtime_forest_has_ordered_disjoint_identities_and_complete_components() -> None:
    sources = make_sources()
    roots = expressions()
    dataset = sources.observe([REVENUE, *roots], population=sources.population(CUSTOMERS))
    assert [field.name for field in dataset.schema.columns][1:] == [
        "revenue",
        "total",
        "average",
        "weighted",
        "europe",
        "ratio",
        "share",
        "weighted_europe",
        "linear",
    ]
    for expression, field in zip(roots, dataset.schema.columns[2:], strict=True):
        assert isinstance(field.identity, _RuntimeMetricFieldIdentity)
        assert dataset.fields.metric(expression).field_id == field.field_id
        assert len(dataset.metric(expression).row_contract.family_semantics.metric_bindings) == 1
    assert len(metric_definition(dataset).metrics[-1].components) == 3
    with pytest.raises(DatasetConstructionError, match="duplicate"):
        sources.observe([roots[0], replace(roots[0], label="renamed")])
    renamed = sources.observe(replace(roots[0], label="renamed"))
    original = sources.observe(roots[0])
    assert renamed.schema.columns[-1].field_id == original.schema.columns[-1].field_id
    assert renamed.schema.columns[-1].identity == original.schema.columns[-1].identity


def test_runtime_name_collisions_and_selector_ownership() -> None:
    sources = make_sources()
    first = rm.aggregate(AMOUNT, agg="sum", label="same")
    second = rm.aggregate(AMOUNT, agg="mean", label="same")
    dataset = sources.observe([first, second])
    assert len({field.name for field in dataset.schema.columns}) == 3
    with pytest.raises(DatasetFieldSelectionError):
        dataset.fields.metric(replace(first, label="same"))
    other = make_sources(session_id="other").observe(first)
    with pytest.raises(DatasetOwnershipError):
        other.where(gt(dataset.fields.metric(first), 0))


def test_metric_projection_accepts_only_current_owned_metric_fields() -> None:
    roots = expressions()[:2]
    sources = make_sources()
    dataset = sources.observe(list(roots))
    selected = dataset.fields.get("total")
    projected = dataset.metric(selected)
    assert projected.definition_fingerprint == dataset.metric(roots[0]).definition_fingerprint
    with pytest.raises(DatasetFieldSelectionError):
        dataset.metric(dataset.fields.get("entity_identity"))
    foreign = make_sources(session_id="foreign").observe(roots[0]).fields.get("total")
    with pytest.raises(DatasetOwnershipError):
        dataset.metric(foreign)
    renamed = sources.observe(replace(roots[0], label="renamed"))
    with pytest.raises(DatasetFieldSelectionError):
        renamed.metric(selected)


@pytest.mark.parametrize("label", ["\n", "a" * 161, "bad\x00name"])
def test_invalid_runtime_labels_fail_without_execution(label: str) -> None:
    with pytest.raises((ValueError, DatasetConstructionError)):
        make_sources().observe(rm.aggregate(AMOUNT, agg="sum", label=label))


@pytest.mark.runtime
def test_runtime_graph_executes_and_folds_selected_rows_without_source(tmp_path: Path) -> None:
    fixture = setup_retained(tmp_path)
    roots = expressions()
    source = fixture.sources.observe(list(roots), population=fixture.sources.population(CUSTOMERS))
    output = source.execute()
    rows = output.to_pandas()
    assert rows["total"].fillna(-1).tolist() == [40, 100, 7, -1]
    assert rows["average"].fillna(-1).tolist() == [20, 100, 3.5, -1]
    assert rows["europe"].fillna(-1).tolist() == [40, -1, 7, -1]
    assert rows["weighted"].fillna(-1).tolist() == [25, 100, -1, -1]
    assert rows["ratio"].fillna(-1).tolist() == [2, 1, 2, -1]
    assert rows["share"].fillna(-1).tolist() == [1, -1, 1, -1]
    assert rows["weighted_europe"].fillna(-1).tolist() == [25, -1, -1, -1]
    assert rows["linear"].fillna(-1).tolist() == [60, 100, 10.5, -1]
    fixture.database.rename(tmp_path / "offline.duckdb")
    selected = output.where(gt(output.fields.metric(roots[0]), 10))
    folded = selected.aggregate().execute()
    result = folded.to_pandas()
    assert result["total"].tolist() == [140]
    assert result["average"].tolist() == pytest.approx([140 / 3])
    assert result["weighted"].tolist() == pytest.approx([50])
    assert result["ratio"].tolist() == pytest.approx([3])
    assert result["share"].tolist() == pytest.approx([40 / 140])
    assert result["weighted_europe"].tolist() == [25]
    assert result["linear"].tolist() == pytest.approx([280 - 140 / 3])
    projected = folded.metric(roots[-1]).execute()
    assert projected.to_pandas()["linear"].tolist() == result["linear"].tolist()
    delta = projected.compare(projected).execute()
    assert delta.to_pandas()["delta"].tolist() == [0]
    finding = delta.findings().items[0]
    assert finding.subject.kind == "metric"
    assert isinstance(finding.subject.metric, _RuntimeMetricFieldIdentity)
    assert selected.aggregate().execute().state.artifact_ref == folded.state.artifact_ref
    assert fixture.runtime.statistics.primary_queries == 0
    assert fixture.runtime.store.resources(fixture.runtime.session_ref) == ()


@pytest.mark.runtime
def test_runtime_forest_three_process_source_offline_and_cold_binding(tmp_path: Path) -> None:
    import json
    import os
    import subprocess
    import sys

    def run(mode: str, session: str = "", artifact: str = "") -> dict[str, object]:
        result = subprocess.run(
            [
                sys.executable,
                "-B",
                "-m",
                "tests.lazy_runtime_metric_worker",
                mode,
                str(tmp_path),
                session,
                artifact,
            ],
            env={**os.environ, "MARIVO_TELEMETRY": "off"},
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        value: object = json.loads(result.stdout)
        assert isinstance(value, dict)
        return value

    produced = run("produce")
    continued = run("continue", str(produced["session"]), str(produced["artifact"]))
    cold = run("cold", str(produced["session"]), str(produced["artifact"]))
    assert len({produced["pid"], continued["pid"], cold["pid"]}) == 3
    assert continued["artifact"] == cold["artifact"]
    assert continued["after"] == cold["before"] == cold["after"]
    assert continued["before"] == produced["after"]
    for result in (continued, cold):
        assert result["total"] == [140]
        assert result["average"] == pytest.approx([140 / 3])
        assert result["weighted"] == [50]
        assert result["linear"] == pytest.approx([280 - 140 / 3])
        assert result["projected_linear"] == pytest.approx([280 - 140 / 3])
        assert result["projected_delta"] == [0]
        assert result["source_fences"] == 0
    assert cold["queries"] == 0


def test_runtime_bounds_governed_leaves_and_temporal_fold_fail_before_execution() -> None:
    sources = make_sources()
    total = expressions()[0]
    with pytest.raises(DatasetConstructionError, match="not loaded"):
        sources.observe(
            rm.aggregate(ref.measure("sales.orders.absent"), agg="sum", label="missing")
        )
    with pytest.raises(DatasetConstructionError, match="fold"):
        sources.observe(rm.aggregate(AMOUNT, agg="sum", fold="last", label="invalid_fold"))
    deep = total
    for _ in range(10):
        deep = rm.ratio(deep, total, label="deep")
    with pytest.raises(DatasetConstructionError, match="depth"):
        sources.observe(deep)
    wide = rm.linear(add=tuple(total for _ in range(128)), label="wide")
    with pytest.raises(DatasetConstructionError, match="occurrence"):
        sources.observe([wide, rm.ratio(wide, total, label="wider")])
    with pytest.raises(DatasetConstructionError, match="labels"):
        sources.observe(rm.ratio(replace(total, label="bad\x00nested"), total, label="valid"))
    with pytest.raises(DatasetConstructionError):
        sources.observe([total] * 17)


@pytest.mark.runtime
@pytest.mark.parametrize("method", ["linear_interpolation@v1", "duckdb_tdigest@v1"])
def test_runtime_private_membership_and_distribution_recover_exactly(
    tmp_path: Path, method: str
) -> None:
    from marivo.semantic._quantile import quantile_metric

    fixture = setup_retained(tmp_path)
    distinct = rm.aggregate(AMOUNT, agg="count_distinct", label="distinct")
    median = rm.aggregate(AMOUNT, agg="median", label="median")
    checkpoint = (
        fixture.sources.observe(
            [distinct, quantile_metric(median, method=method)],
            population=fixture.sources.population(CUSTOMERS),
        )
        .with_dimensions(ref.dimension("sales.orders.channel"))
        .aggregate()
        .execute()
    )
    fixture.database.rename(tmp_path / "offline.duckdb")
    output = checkpoint.where(gt(checkpoint.fields.get("distinct"), 0)).execute()
    rows = output.to_pandas()
    assert rows["distinct"].tolist() == [5]
    assert rows["median"].tolist() == [10]
    assert output.fields.metric(median).field_id == checkpoint.fields.metric(median).field_id
    record = fixture.runtime.store.artifact(checkpoint.state.artifact_ref.ref)
    assert record is not None and len(record.descriptor.retained_parts) == 4
    assert {part.contract_id for part in record.descriptor.retained_parts} == {
        "metric.sufficient_components",
        "metric.distinct_membership",
        "metric.distribution",
    }
    receipt = next(
        part.storage_receipt
        for part in record.descriptor.retained_parts
        if part.contract_id == "metric.distribution"
    )
    assert receipt.kind == "local"
    path = tmp_path / receipt.project_relative_path / "data.parquet"
    if method == "linear_interpolation@v1":
        path.unlink()
    else:
        import pyarrow as pa
        import pyarrow.compute as pc
        import pyarrow.parquet as pq

        path.chmod(0o600)
        table = pq.read_table(path)
        name = "__mv_distribution_frequency"
        changed = table.set_column(
            table.schema.get_field_index(name), name, pc.add(table[name], pa.scalar(1))
        )
        pq.write_table(changed, path)
    assert checkpoint.to_pandas()["median"].tolist() == [10]
    from marivo.analysis.materialization.errors import MaterializationError

    with pytest.raises(MaterializationError):
        checkpoint.where(gt(checkpoint.fields.get("distinct"), 1)).execute()


@pytest.mark.runtime
def test_runtime_rollup_and_empty_scalar_preserve_fold_semantics(tmp_path: Path) -> None:
    from marivo.analysis import grain, time_scope

    fixture = setup_retained(tmp_path)
    total, mean = expressions()[:2]
    checkpoint = (
        fixture.sources.observe(
            [total, mean], time_scope=time_scope(start="2026-02-01", end="2026-02-05")
        )
        .with_time_axis(ref.time_dimension("sales.orders.order_time"), grain=grain("day"))
        .aggregate()
        .execute()
    )
    fixture.database.rename(tmp_path / "offline.duckdb")
    output = checkpoint.rollup(drop_time=True).execute()
    rows = output.to_pandas()
    assert rows["total"].tolist() == [140]
    assert rows["average"].tolist() == [35]
    empty = (
        checkpoint.where(gt(checkpoint.fields.get("total"), 1000))
        .rollup(drop_time=True)
        .execute()
        .to_pandas()
    )
    assert len(empty) == 1 and empty[["total", "average"]].isna().all().all()


@pytest.mark.parametrize("violation", ["unit", "weight", "domain"])
def test_runtime_governance_rejects_incompatible_forest(violation: str) -> None:
    from marivo.analysis.session._lazy_sources import make_lazy_sources
    from tests.lazy_observation_fixtures import NoIoActionPort, make_semantic_registry
    from tests.lazy_runtime_metric_fixtures import WEIGHT

    registry, sidecar = make_semantic_registry()
    registry = replace(registry, measures=dict(registry.measures), entities=dict(registry.entities))
    if violation == "unit":
        registry.measures[WEIGHT.path] = replace(registry.measures[WEIGHT.path], unit="seconds")
        expression = rm.linear(
            add=(expressions()[0], rm.aggregate(WEIGHT, agg="sum", label="duration")),
            label="invalid",
        )
    elif violation == "weight":
        registry.measures[WEIGHT.path] = replace(
            registry.measures[WEIGHT.path], additivity="non_additive"
        )
        expression = rm.weighted_mean(AMOUNT, WEIGHT, label="invalid")
    else:
        registry.entities["sales.lines"] = replace(
            registry.entities["sales.lines"], datasource="other"
        )
        expression = rm.ratio(
            expressions()[0],
            rm.aggregate(ref.measure("sales.lines.amount"), agg="sum", label="other"),
            label="invalid",
        )
    registry.freeze()
    sources = make_lazy_sources(
        semantic_registry=registry,
        sidecar=sidecar,
        action_port=NoIoActionPort(),
        session_id="governance",
        store_id="governance",
    )
    with pytest.raises(DatasetConstructionError):
        sources.observe(expression)


def test_normalized_root_is_independent_of_forest_position_and_keeps_dependencies() -> None:
    from marivo.semantic.metric_graph_canonical import validate_graph
    from marivo.semantic.metric_graph_lowering import normalize_target_metric_inputs
    from tests.lazy_observation_fixtures import make_semantic_registry

    registry, sidecar = make_semantic_registry()
    root = expressions()[0]
    alone = normalize_target_metric_inputs(registry, (root,), sidecar=sidecar)[0]
    mixed = normalize_target_metric_inputs(registry, (REVENUE, root), sidecar=sidecar)[1]
    assert alone == mixed
    validate_graph(mixed.graph)
    registry = replace(
        registry,
        measures={
            **registry.measures,
            AMOUNT.path: replace(registry.measures[AMOUNT.path], unit="EUR"),
        },
    )
    changed = normalize_target_metric_inputs(registry, (root,), sidecar=sidecar)[0]
    assert changed.dependency_fingerprint != alone.dependency_fingerprint


@pytest.mark.runtime
@pytest.mark.parametrize("zero_division", ["null", "error"])
def test_runtime_zero_denominator_policy_and_cleanup(tmp_path: Path, zero_division: str) -> None:
    from marivo.analysis.materialization.errors import MaterializationError
    from tests.lazy_runtime_metric_fixtures import WEIGHT

    fixture = setup_retained(tmp_path)
    denominator = rm.aggregate(WEIGHT, agg="sum", label="weights")
    expression = rm.ratio(expressions()[0], denominator, zero_division=zero_division, label="rate")
    logical = fixture.sources.observe(expression, population=fixture.sources.population(CUSTOMERS))
    if zero_division == "error":
        with pytest.raises(MaterializationError):
            logical.execute()
    else:
        rows = logical.execute().to_pandas()
        assert rows["rate"].fillna(-1).tolist() == pytest.approx([10, 100 / 6, -1, -1])
    assert fixture.runtime.store.resources(fixture.runtime.session_ref) == ()


@pytest.mark.runtime
def test_runtime_closed_slice_predicates_have_independent_numeric_results(tmp_path: Path) -> None:
    fixture = setup_retained(tmp_path)
    day = ref.time_dimension("sales.orders.order_time")
    predicates = (
        {"op": "==", "value": "2026-02-02"},
        {"op": "!=", "value": "2026-02-02"},
        {"op": ">", "value": "2026-02-03"},
        {"op": ">=", "value": "2026-02-03"},
        {"op": "<", "value": "2026-02-03"},
        {"op": "<=", "value": "2026-02-02"},
        {"op": "in", "value": ["2026-02-03", "2026-02-04"]},
        {"op": "between", "value": ["2026-02-02", "2026-02-03"]},
    )
    roots = [
        rm.slice(expressions()[0], by={day: predicate}, label=f"slice_{index}")
        for index, predicate in enumerate(predicates)
    ]
    output = fixture.sources.observe(roots).aggregate().execute()
    assert output.to_pandas().iloc[0].tolist() == [110, 30, 0, 30, 110, 110, 30, 140]


@pytest.mark.parametrize(
    "predicate",
    [{"op": "in", "value": []}, {"op": "between", "value": ["EU"]}, {"op": "==", "value": ["EU"]}],
)
def test_invalid_slice_shapes_fail_at_construction(predicate: dict[str, object]) -> None:
    expression = rm.slice(
        expressions()[0], by={ref.dimension("sales.customers.region"): predicate}, label="invalid"
    )
    with pytest.raises(DatasetConstructionError, match="Slice"):
        make_sources().observe(expression)


@pytest.mark.parametrize(
    ("dimension", "predicate"),
    [
        ("channel", {"op": ">", "value": 1}),
        ("channel", {"op": "in", "value": ["web", 1]}),
        ("order_time", {"op": "between", "value": ["2026-02-01", 2]}),
        ("order_time", {"op": "between", "value": [None, "2026-02-01"]}),
        ("order_time", {"op": "==", "value": "not-a-date"}),
    ],
)
def test_slice_literals_are_checked_against_declared_dimension_types(
    dimension: str, predicate: dict[str, object]
) -> None:
    from marivo.semantic.errors import SemanticLoadError

    axis = (
        ref.dimension("sales.orders.channel")
        if dimension == "channel"
        else ref.time_dimension("sales.orders.order_time")
    )
    expression = rm.slice(expressions()[0], by={axis: predicate}, label="invalid")
    with pytest.raises(SemanticLoadError) as error:
        make_sources().observe(expression)
    assert axis.path in str(error.value)
    assert "Replace the slice value" in str(error.value)


@pytest.mark.runtime
def test_invalid_slice_type_does_not_admit_a_run(tmp_path: Path) -> None:
    from marivo.semantic.errors import SemanticLoadError
    from tests.lazy_adapter_runtime_worker import snapshot

    fixture = setup_retained(tmp_path)
    before = snapshot(fixture.runtime)
    expression = rm.slice(
        expressions()[0],
        by={ref.dimension("sales.orders.channel"): {"op": ">", "value": 1}},
        label="invalid",
    )
    with pytest.raises(SemanticLoadError, match="channel"):
        fixture.sources.observe(expression)
    assert snapshot(fixture.runtime) == before
    assert fixture.runtime.statistics.statements == []


def test_runtime_identity_reaches_existing_operator_constructors() -> None:
    from marivo.analysis import grain, time_scope
    from marivo.analysis.operators.forecast_contracts import periods

    sources = make_sources()
    roots = expressions()
    metrics = sources.observe(list(roots[:2]))
    one = metrics.metric(roots[0])
    assert one.compare(one).kind == "delta"
    assert metrics.correlate().kind == "association"
    assert one.discover.entity_outliers().kind == "candidate"
    history = (
        sources.observe(roots[0], time_scope=time_scope(start="2026-02-01", end="2026-03-01"))
        .with_time_axis(ref.time_dimension("sales.orders.order_time"), grain=grain("day"))
        .aggregate()
    )
    assert history.forecast(horizon=periods(2)).kind == "forecast"


@pytest.mark.runtime
def test_runtime_slice_preserves_catalog_cumulative_semantics(tmp_path: Path) -> None:
    from marivo.analysis import time_scope
    from marivo.semantic.ir import CumulativeComposition
    from tests.lazy_execution_fixtures import make_execution_registry

    fixture = setup_retained(tmp_path)
    registry, sidecar = make_execution_registry(fixture.database)
    registry = replace(
        registry,
        metrics={
            **registry.metrics,
            "sales.running": replace(
                registry.metrics["sales.conversion_rate"],
                semantic_id="sales.running",
                name="running",
                composition=CumulativeComposition(
                    REVENUE.path, "sales.orders.order_time", "all_history"
                ),
            ),
        },
    )
    registry.freeze()
    sources = fixture.runtime.sources(semantic_registry=registry, sidecar=sidecar)
    expression = rm.slice(
        ref.metric("sales.running"),
        by={ref.dimension("sales.customers.region"): "EU"},
        label="running_europe",
    )
    logical = sources.observe(
        expression, time_scope=time_scope(start="2026-02-01", end="2026-02-05")
    ).aggregate()
    assert logical.execute().to_pandas()["running_europe"].tolist() == [40]
