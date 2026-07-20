from __future__ import annotations

import importlib
import json

import pytest

import marivo.analysis as mv
import marivo.analysis.session as session_attach
import marivo.semantic as ms
from marivo.analysis.errors import FrameMetaInvalidError, SemanticKindMismatchError
from marivo.analysis.intents._replay import recover_observe_replay
from marivo.analysis.intents.observe_errors import ObservePlanningError
from marivo.semantic.metric_graph import RuntimeExpressionIdentity
from marivo.semantic.unit_algebra import UnknownUnitV2
from tests.conftest import bootstrap_sales_project
from tests.shared_fixtures import connect_sales_orders, sales_backends


@pytest.fixture(autouse=True)
def _runtime_session_state(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TZ", "UTC")
    session_attach._reset_process_state()


@pytest.fixture
def runtime_session(tmp_path):
    bootstrap_sales_project(tmp_path)
    semantic_file = tmp_path / "models" / "semantic" / "sales" / "datasets.py"
    semantic_file.write_text(
        semantic_file.read_text()
        + "\n@ms.measure(entity=orders, additivity='additive', unit='USD')\n"
        + "def amount_measure(orders):\n"
        + "    return orders.amount\n"
        + "\n@ms.measure(entity=orders, additivity='additive', unit='USD/(request)')\n"
        + "def opaque_amount_measure(orders):\n"
        + "    return orders.amount\n"
        + "\n@ms.measure(entity=orders, additivity='non_additive', unit='USD')\n"
        + "def unit_price_measure(orders):\n"
        + "    return orders.amount\n"
        + "\n@ms.measure(entity=orders, additivity='non_additive', unit='USD')\n"
        + "def runtime_only_value_measure(orders):\n"
        + "    return orders.amount\n"
        + "\n@ms.measure(entity=orders, additivity='non_additive')\n"
        + "def user_value_measure(orders):\n"
        + "    return orders.user_id.cast('float64')\n"
        + "\n@ms.measure(entity=orders, additivity='additive', unit='request')\n"
        + "def request_weight_measure(orders):\n"
        + "    return orders.order_id.cast('float64')\n"
        + "\n@ms.measure(entity=orders, additivity='non_additive', unit='request')\n"
        + "def invalid_weight_measure(orders):\n"
        + "    return orders.order_id.cast('float64')\n"
        + "\nother_orders = ms.entity(\n"
        + "    name='other_orders', datasource=warehouse, source=md.table('orders')\n"
        + ")\n"
        + "\n@ms.measure(entity=other_orders, additivity='additive', unit='request')\n"
        + "def other_weight_measure(other_orders):\n"
        + "    return other_orders.order_id.cast('float64')\n"
        + "\nmeasure_revenue = ms.aggregate(\n"
        + "    name='measure_revenue', measure=amount_measure, agg='sum'\n"
        + ")\n"
        + "\nmeasure_count = ms.aggregate(\n"
        + "    name='measure_count', measure=amount_measure, agg='count'\n"
        + ")\n"
        + "\nmeasure_average = ms.ratio(\n"
        + "    name='measure_average', numerator=measure_revenue, denominator=measure_count\n"
        + ")\n"
        + "\ncatalog_weighted_mean = ms.weighted_mean(\n"
        + "    name='catalog_weighted_mean',\n"
        + "    value=unit_price_measure,\n"
        + "    weight=request_weight_measure,\n"
        + ")\n"
        + "\nfiltered_catalog_weighted_mean = ms.weighted_mean(\n"
        + "    name='filtered_catalog_weighted_mean',\n"
        + "    value=user_value_measure,\n"
        + "    weight=amount_measure,\n"
        + "    filter=ms.where(region='north'),\n"
        + ")\n"
    )
    connection = connect_sales_orders()
    return session_attach.get_or_create(
        name="runtime-metrics",
        backends=sales_backends(connection),
    )


def _measure_ref(session):
    measure_id = next(iter(session.catalog._require_index().registry.measures))
    return session.catalog.require(ms.Ref.measure(measure_id)).ref


def _named_measure_ref(session, name: str):
    measure_id = next(
        semantic_id
        for semantic_id, measure in session.catalog._require_index().registry.measures.items()
        if measure.name == name
    )
    return session.catalog.require(ms.Ref.measure(measure_id)).ref


def _region_ref(session):
    region_id = next(
        semantic_id
        for semantic_id, dimension in session.catalog._require_index().registry.dimensions.items()
        if dimension.name == "region"
    )
    return session.catalog.require(ms.Ref.dimension(region_id)).ref


def _persistence_state(session) -> tuple[set[str], set[str], set[str], set[str], set[str]]:
    return (
        {path.name for path in session._layout.frames_dir.iterdir()},
        {path.name for path in session._layout.jobs_dir.glob("*.json")},
        {row["artifact_id"] for row in session._store.list_artifacts(session.id)},
        {row["job_id"] for row in session._store.list_jobs(session.id)},
        {
            row[0]
            for row in session._evidence_store()
            .read()
            .execute("SELECT artifact_id FROM artifacts")
            .fetchall()
        },
    )


def test_observe_runtime_aggregate_materializes_typed_artifact(runtime_session) -> None:
    amount = _measure_ref(runtime_session)
    expression = mv.runtime_metric.aggregate(amount, agg="sum", label="Runtime revenue")

    frame = runtime_session.observe(expression)

    assert frame.to_pandas()[frame.value_columns[0]].iloc[0] == pytest.approx(100.0)
    assert isinstance(frame.meta.metric_identity, RuntimeExpressionIdentity)
    assert frame.meta.expression_fingerprint == frame.meta.metric_identity.expression_fingerprint
    assert frame.meta.presentation is not None
    assert frame.meta.presentation.labels[0].label == "Runtime revenue"
    assert frame.meta.unit == "USD"
    assert frame.meta.metric_id == f"runtime:{frame.meta.expression_fingerprint}"
    assert not frame.meta.metric_id.startswith("sales.")
    assert frame.meta.component_graph_ref is not None
    component_graph = frame.components().meta.component_graph
    assert component_graph is not None
    assert component_graph["root_node_ids"] == [frame.meta.expression_fingerprint]
    assert component_graph["nodes"][0]["evaluator_contract"] == "aggregate-evaluation/v1"
    assert component_graph["nodes"][0]["governed_leaf_lineage"]


def test_observe_runtime_weighted_mean_uses_exact_paired_components(runtime_session) -> None:
    value = _named_measure_ref(runtime_session, "runtime_only_value_measure")
    weight = _named_measure_ref(runtime_session, "request_weight_measure")
    expression = mv.runtime_metric.weighted_mean(
        value,
        weight,
        label="Runtime weighted mean",
    )

    frame = runtime_session.observe(expression)

    assert frame.to_pandas()[frame.value_columns[0]].iloc[0] == pytest.approx(30.0)
    assert frame.meta.unit == "USD"
    assert frame.meta.additivity == "non_additive"
    assert frame.meta.composition is not None
    assert frame.meta.composition["kind"] == "weighted_mean"
    assert frame.meta.zero_denominator_rows == 0
    components = frame.components().to_pandas()
    assert components["__weighted_mean_numerator"].iloc[0] == pytest.approx(300.0)
    assert components["__weighted_mean_weight"].iloc[0] == pytest.approx(10.0)


def test_catalog_weighted_mean_filter_uses_authored_physical_column(runtime_session) -> None:
    metric = runtime_session.catalog.require(
        ms.Ref.metric("sales.filtered_catalog_weighted_mean")
    ).ref

    frame = runtime_session.observe(metric)

    assert frame.to_pandas()[frame.value_columns[0]].iloc[0] == pytest.approx(15000.0 / 70.0)


def test_runtime_weighted_mean_pairs_nulls_and_reports_zero_weight(runtime_session) -> None:
    value = _named_measure_ref(runtime_session, "runtime_only_value_measure")
    weight = _named_measure_ref(runtime_session, "request_weight_measure")
    expression = mv.runtime_metric.weighted_mean(value, weight)
    backend = runtime_session._connection_runtime.get_or_create("warehouse")
    backend.raw_sql("UPDATE orders SET amount = NULL WHERE order_id = 4")

    paired = runtime_session.observe(expression)

    assert paired.to_pandas()[paired.value_columns[0]].iloc[0] == pytest.approx(140.0 / 6.0)
    paired_components = paired.components().to_pandas()
    assert paired_components["__weighted_mean_numerator"].iloc[0] == pytest.approx(140.0)
    assert paired_components["__weighted_mean_weight"].iloc[0] == pytest.approx(6.0)

    backend.raw_sql(
        "UPDATE orders SET amount = order_id * 10, "
        "order_id = CASE order_id WHEN 1 THEN 1 WHEN 2 THEN -1 WHEN 3 THEN 2 ELSE -2 END"
    )
    zero = runtime_session.observe(expression)

    assert zero.to_pandas()[zero.value_columns[0]].isna().all()
    assert zero.meta.zero_denominator_rows == 1


def test_runtime_weighted_mean_rejects_non_additive_weight(runtime_session) -> None:
    value = _named_measure_ref(runtime_session, "unit_price_measure")
    weight = _named_measure_ref(runtime_session, "invalid_weight_measure")

    with pytest.raises(ObservePlanningError, match=r"weight must be additive") as exc_info:
        runtime_session.observe(mv.runtime_metric.weighted_mean(value, weight))

    assert exc_info.value._context["code"] == "runtime-weighted-mean-weight-non-additive"
    assert exc_info.value._context["candidates"]["additive_weight_refs"]
    assert exc_info.value._context["repair"][0]["arg"] == "weight"


def test_runtime_weighted_mean_rejects_cross_entity_inputs(runtime_session) -> None:
    value = _named_measure_ref(runtime_session, "unit_price_measure")
    weight = _named_measure_ref(runtime_session, "other_weight_measure")

    with pytest.raises(
        ObservePlanningError, match="same entity and physical row grain"
    ) as exc_info:
        runtime_session.observe(mv.runtime_metric.weighted_mean(value, weight))

    assert exc_info.value._context["code"] == "runtime-weighted-mean-grain-mismatch"
    assert exc_info.value._context["candidates"]["additive_weight_refs"]
    assert exc_info.value._context["repair"][0]["arg"] == "weight"


def test_runtime_weighted_mean_rejects_missing_measure_with_structured_repair(
    runtime_session,
) -> None:
    weight = _named_measure_ref(runtime_session, "request_weight_measure")

    with pytest.raises(ObservePlanningError, match="is not loaded") as exc_info:
        runtime_session.observe(
            mv.runtime_metric.weighted_mean(
                ms.Ref.measure("sales.orders.runtime_only_value_measur"),
                weight,
            )
        )

    assert exc_info.value._context["code"] == "runtime-weighted-mean-measure-missing"
    assert exc_info.value._context["candidates"]["role"] == "value"
    assert exc_info.value._context["repair"][0] == {
        "action": "replace_measure_ref",
        "target": "runtime_metric.weighted_mean",
        "arg": "value",
        "value": "sales.orders.runtime_only_value_measure",
        "safety": "modeling_decision",
        "why": "closest loaded measure ref to 'sales.orders.runtime_only_value_measur'",
    }


def test_compare_catalog_and_equivalent_runtime_weighted_mean(runtime_session) -> None:
    value = _named_measure_ref(runtime_session, "unit_price_measure")
    weight = _named_measure_ref(runtime_session, "request_weight_measure")
    catalog_metric = runtime_session.catalog.require(
        ms.Ref.metric("sales.catalog_weighted_mean")
    ).ref

    catalog_frame = runtime_session.observe(catalog_metric)
    runtime_frame = runtime_session.observe(mv.runtime_metric.weighted_mean(value, weight))
    delta = runtime_session.compare(catalog_frame, runtime_frame)

    assert delta.to_pandas()["delta"].iloc[0] == pytest.approx(0.0)


def test_runtime_weighted_mean_can_feed_runtime_ratio(runtime_session) -> None:
    value = _named_measure_ref(runtime_session, "unit_price_measure")
    weight = _named_measure_ref(runtime_session, "request_weight_measure")
    catalog_metric = runtime_session.catalog.require(
        ms.Ref.metric("sales.catalog_weighted_mean")
    ).ref
    weighted = mv.runtime_metric.weighted_mean(value, weight)

    frame = runtime_session.observe(mv.runtime_metric.ratio(weighted, catalog_metric))

    assert frame.to_pandas()[frame.value_columns[0]].iloc[0] == pytest.approx(1.0)


def test_observe_reexecutes_before_reusing_a_materialized_snapshot(runtime_session) -> None:
    amount = _measure_ref(runtime_session)
    expression = mv.runtime_metric.aggregate(amount, agg="sum")

    first = runtime_session.observe(expression)
    backend = runtime_session._connection_runtime.get_or_create("warehouse")
    backend.raw_sql("UPDATE orders SET amount = amount + 100")
    second = runtime_session.observe(expression)

    assert first.to_pandas()[first.value_columns[0]].iloc[0] == pytest.approx(100.0)
    assert second.to_pandas()[second.value_columns[0]].iloc[0] == pytest.approx(500.0)
    assert second.ref != first.ref
    assert second.meta.artifact_identity is not None
    assert first.meta.artifact_identity is not None
    assert (
        second.meta.artifact_identity.snapshot_fingerprint
        != first.meta.artifact_identity.snapshot_fingerprint
    )
    repeated = runtime_session.observe(expression)
    assert repeated.ref == second.ref
    assert repeated.meta.execution_stats is not None
    assert repeated.meta.execution_stats.cache_hit is False
    assert repeated.meta.execution_stats.artifact_deduplicated is True
    assert repeated.meta.execution_stats.root_origins == ("runtime",)


def test_observe_reuses_snapshot_verified_artifact_without_backend_execution(
    runtime_session, monkeypatch
) -> None:
    amount = _measure_ref(runtime_session)
    expression = mv.runtime_metric.aggregate(amount, agg="sum")
    backend = runtime_session._connection_runtime.get_or_create("warehouse")
    snapshot = {"token": "orders-v1"}
    monkeypatch.setattr(
        backend,
        "marivo_snapshot_token",
        lambda: snapshot["token"],
        raising=False,
    )

    first = runtime_session.observe(expression)
    execute_calls = 0
    original_execute = backend.execute

    def counted_execute(*args, **kwargs):
        nonlocal execute_calls
        execute_calls += 1
        return original_execute(*args, **kwargs)

    monkeypatch.setattr(backend, "execute", counted_execute)
    second = runtime_session.observe(expression)

    assert second.ref == first.ref
    assert execute_calls == 0
    assert second.meta.execution_stats is not None
    assert second.meta.execution_stats.cache_hit is True
    assert second.meta.execution_stats.artifact_deduplicated is False
    assert second.meta.execution_stats.physical_execution_count == 0

    snapshot["token"] = "orders-v2"
    backend.raw_sql("UPDATE orders SET amount = amount + 100")
    changed = runtime_session.observe(expression)

    assert execute_calls > 0
    assert changed.ref != first.ref
    assert changed.to_pandas()[changed.value_columns[0]].iloc[0] == pytest.approx(500.0)


def test_observe_runtime_conditional_ratio_pushes_branch_slices(runtime_session) -> None:
    amount = _measure_ref(runtime_session)
    region = _region_ref(runtime_session)
    north = mv.runtime_metric.aggregate(
        amount,
        agg="sum",
        slice_by={region: "NORTH"},
    )
    total = mv.runtime_metric.aggregate(amount, agg="sum")

    frame = runtime_session.observe(mv.runtime_metric.ratio(north, total))

    assert frame.to_pandas()["runtime_metric"].iloc[0] == pytest.approx(0.7)
    assert frame.meta.zero_denominator_rows == 0
    assert frame.meta.component_ref is not None

    component_graph = frame.components().meta.component_graph
    assert component_graph is not None
    sliced_leaf = next(node for node in component_graph["nodes"] if node["node_kind"] == "slice")
    aggregate_child_id = sliced_leaf["ordered_children"][0]["node_id"]
    aggregate_child = next(
        node for node in component_graph["nodes"] if node["node_id"] == aggregate_child_id
    )
    assert aggregate_child["value_semantics"]["unit"] == "USD"
    assert aggregate_child["value_semantics"]["unit_state"]["schema"] == ("metric-unit-algebra/v2")
    assert aggregate_child["quality"] is not None
    assert aggregate_child["governed_leaf_lineage"]


def test_observe_runtime_ratio_can_mix_catalog_child(runtime_session) -> None:
    amount = _measure_ref(runtime_session)
    revenue = runtime_session.catalog.require(ms.Ref.metric("sales.measure_revenue")).ref
    runtime_total = mv.runtime_metric.aggregate(amount, agg="sum")

    frame = runtime_session.observe(mv.runtime_metric.ratio(runtime_total, revenue))

    assert frame.to_pandas()["runtime_metric"].iloc[0] == pytest.approx(1.0)
    params = frame.meta.lineage.steps[0].params
    assert params is not None
    assert len(params["lineage_metadata"]["physical_leaves"]) == 1


def test_compare_catalog_and_equivalent_runtime_aggregate(runtime_session) -> None:
    amount = _measure_ref(runtime_session)
    catalog_metric = runtime_session.catalog.require(ms.Ref.metric("sales.measure_revenue")).ref
    runtime_expression = mv.runtime_metric.aggregate(amount, agg="sum")

    current = runtime_session.observe(catalog_metric)
    baseline = runtime_session.observe(runtime_expression)
    delta = runtime_session.compare(current, baseline)

    assert delta.to_pandas()["delta"].iloc[0] == pytest.approx(0.0)
    assert delta.meta.metric_identity == current.meta.metric_identity
    assert delta.meta.baseline_metric_identity == baseline.meta.metric_identity
    assert delta.meta.comparison_identity is not None
    assert delta.meta.comparison_identity.current == current.meta.metric_identity
    assert delta.meta.comparison_identity.baseline == baseline.meta.metric_identity


@pytest.mark.parametrize("catalog_is_current", [True, False])
def test_compare_catalog_and_equivalent_runtime_ratio_uses_graph_identity(
    runtime_session,
    catalog_is_current: bool,
) -> None:
    amount = _measure_ref(runtime_session)
    catalog_metric = runtime_session.catalog.require(ms.Ref.metric("sales.measure_average")).ref
    runtime_expression = mv.runtime_metric.ratio(
        mv.runtime_metric.aggregate(amount, agg="sum"),
        mv.runtime_metric.aggregate(amount, agg="count"),
        label="Runtime average",
    )
    catalog_frame = runtime_session.observe(catalog_metric)
    runtime_frame = runtime_session.observe(runtime_expression)
    current, baseline = (
        (catalog_frame, runtime_frame) if catalog_is_current else (runtime_frame, catalog_frame)
    )

    delta = runtime_session.compare(current, baseline)

    assert delta.to_pandas()["delta"].iloc[0] == pytest.approx(0.0)
    assert delta.meta.metric_identity == current.meta.metric_identity
    assert delta.meta.baseline_metric_identity == baseline.meta.metric_identity
    assert delta.meta.component_ref is not None
    assert delta.components().meta.parent_ref == delta.ref


def test_compare_equal_runtime_ratio_ignores_presentation_labels(runtime_session) -> None:
    amount = _measure_ref(runtime_session)
    current = runtime_session.observe(
        mv.runtime_metric.ratio(
            mv.runtime_metric.aggregate(amount, agg="sum", label="Current total"),
            mv.runtime_metric.aggregate(amount, agg="count", label="Current count"),
            label="Current average",
        )
    )
    baseline = runtime_session.observe(
        mv.runtime_metric.ratio(
            mv.runtime_metric.aggregate(amount, agg="sum", label="Baseline total"),
            mv.runtime_metric.aggregate(amount, agg="count", label="Baseline count"),
            label="Baseline average",
        )
    )

    delta = runtime_session.compare(current, baseline)

    assert delta.to_pandas()["delta"].iloc[0] == pytest.approx(0.0)
    component_columns = delta.components().to_pandas().columns
    assert "current_Current average" in component_columns
    assert "baseline_Current average" in component_columns


def test_failed_component_alignment_leaves_no_delta_artifact_or_job(
    runtime_session,
    monkeypatch,
) -> None:
    amount = _measure_ref(runtime_session)
    expression = mv.runtime_metric.ratio(
        mv.runtime_metric.aggregate(amount, agg="sum"),
        mv.runtime_metric.aggregate(amount, agg="count"),
    )
    current = runtime_session.observe(expression)
    baseline = runtime_session.observe(expression)
    persistence_before = _persistence_state(runtime_session)
    compare_module = importlib.import_module("marivo.analysis.intents.compare")

    def fail_component_alignment(*args, **kwargs):
        raise RuntimeError("component alignment failed")

    monkeypatch.setattr(compare_module, "_align_component_frames", fail_component_alignment)

    with pytest.raises(RuntimeError, match="component alignment failed"):
        runtime_session.compare(current, baseline)

    assert _persistence_state(runtime_session) == persistence_before


def test_failed_compare_commit_rolls_back_delta_component_evidence_and_job(
    runtime_session,
    monkeypatch,
) -> None:
    amount = _measure_ref(runtime_session)
    expression = mv.runtime_metric.ratio(
        mv.runtime_metric.aggregate(amount, agg="sum"),
        mv.runtime_metric.aggregate(amount, agg="count"),
    )
    current = runtime_session.observe(expression)
    baseline = runtime_session.observe(expression)
    persistence_before = _persistence_state(runtime_session)
    compare_module = importlib.import_module("marivo.analysis.intents.compare")

    def fail_job_persistence(*args, **kwargs):
        raise RuntimeError("job persistence failed")

    monkeypatch.setattr(compare_module, "persist_job_record", fail_job_persistence)

    with pytest.raises(RuntimeError, match="job persistence failed"):
        runtime_session.compare(current, baseline)

    assert _persistence_state(runtime_session) == persistence_before


def test_runtime_expression_replay_uses_persisted_typed_descriptor(runtime_session) -> None:
    amount = _measure_ref(runtime_session)
    expression = mv.runtime_metric.ratio(
        mv.runtime_metric.aggregate(amount, agg="sum"),
        mv.runtime_metric.aggregate(amount, agg="count"),
    )
    source = runtime_session.observe(expression)

    replay = recover_observe_replay(source, session=runtime_session)
    repeated = replay.call_observe(runtime_session)

    assert repeated.meta.metric_identity == source.meta.metric_identity
    assert repeated.meta.expression_graph == source.meta.expression_graph
    assert repeated.to_pandas()[repeated.value_columns[0]].iloc[0] == pytest.approx(25.0)


def test_multi_root_replay_recovers_the_ordered_forest(runtime_session) -> None:
    amount = _measure_ref(runtime_session)
    catalog_metric = runtime_session.catalog.require(ms.Ref.metric("sales.measure_revenue")).ref
    source = runtime_session.observe(
        [mv.runtime_metric.aggregate(amount, agg="sum"), catalog_metric]
    )

    replay = recover_observe_replay(source, session=runtime_session)
    repeated = replay.call_observe(runtime_session)

    assert source.meta.execution_stats is not None
    assert source.meta.execution_stats.root_origins == ("runtime", "catalog")
    assert repeated.meta.metric_identities == source.meta.metric_identities
    assert repeated.meta.expression_graph == source.meta.expression_graph
    assert repeated.to_pandas().equals(source.to_pandas())
    assert repeated.meta.execution_stats is not None
    assert repeated.meta.execution_stats.replay_used is True


def test_unknown_runtime_unit_is_explicit_and_emits_capability_issue(runtime_session) -> None:
    opaque = _named_measure_ref(runtime_session, "opaque_amount_measure")
    amount = _measure_ref(runtime_session)
    frame = runtime_session.observe(
        mv.runtime_metric.ratio(
            mv.runtime_metric.aggregate(opaque, agg="sum"),
            mv.runtime_metric.aggregate(amount, agg="sum"),
        )
    )

    assert frame.meta.unit is None
    assert frame.meta.unit_state == UnknownUnitV2(schema="metric-unit-unknown/v2")
    assert [issue.kind for issue in frame.meta.issues] == ["unit_capability_unknown"]
    root = next(
        node
        for node in frame.components().meta.component_graph["nodes"]
        if node["node_id"] == frame.meta.expression_graph.roots[0]
    )
    assert root["value_semantics"]["unit_state"] == {"schema": "metric-unit-unknown/v2"}
    assert root["value_semantics"]["unit_capability_issue"] == "unit_algebra_unsupported"


def test_public_observe_and_projection_keep_only_selected_runtime_root(runtime_session) -> None:
    amount = _measure_ref(runtime_session)
    aggregate = mv.runtime_metric.aggregate(amount, agg="sum", label="Total")
    ratio = mv.runtime_metric.ratio(
        aggregate,
        mv.runtime_metric.aggregate(amount, agg="count"),
        label="Average",
    )

    forest = runtime_session.observe([aggregate, ratio])
    assert forest.meta.execution_stats is not None
    assert forest.meta.execution_stats.root_origins == ("runtime", "runtime")
    forest_component_graph = forest.components().meta.component_graph
    assert forest.meta.component_ref is None
    assert forest.meta.component_graph_ref is not None
    assert forest_component_graph is not None
    assert len(forest_component_graph["root_node_ids"]) == 2
    ratio_record = next(
        node for node in forest_component_graph["nodes"] if node["node_kind"] == "ratio"
    )
    assert ratio_record["evaluator_contract"] == "ratio-evaluation/v1"
    assert [child["role"] for child in ratio_record["ordered_children"]] == [
        "numerator",
        "denominator",
    ]
    assert "coverage_ref" in ratio_record
    assert ratio_record["governed_leaf_lineage"]
    projected = forest.metric(forest.metrics[0])
    direct = runtime_session.observe(aggregate)

    assert projected.meta.expression_graph is not None
    assert len(projected.meta.expression_graph.roots) == 1
    assert projected.meta.expression_graph == direct.meta.expression_graph
    assert projected.meta.semantic_dependency_digest == direct.meta.semantic_dependency_digest
    assert projected.meta.comparable_value_semantics is not None
    assert direct.meta.comparable_value_semantics is not None
    assert (
        projected.meta.comparable_value_semantics.evaluator_contracts
        == direct.meta.comparable_value_semantics.evaluator_contracts
        == ("aggregate-evaluation/v1",)
    )
    assert runtime_session.compare(projected, direct).to_pandas()["delta"].iloc[0] == 0
    projected_components = projected.components()
    assert projected_components.meta.parent_ref == projected.ref
    projected_graph = projected_components.meta.component_graph
    assert projected_graph is not None
    assert len(projected_graph["root_node_ids"]) == 1


def test_multi_root_evidence_preserves_each_metric_unit(runtime_session) -> None:
    amount = _measure_ref(runtime_session)
    catalog_metric = runtime_session.catalog.require(ms.Ref.metric("sales.measure_revenue")).ref
    frame = runtime_session.observe(
        [mv.runtime_metric.aggregate(amount, agg="sum"), catalog_metric]
    )

    findings = runtime_session.evidence.findings(
        artifact_ref=frame.ref,
        kind="metric_value",
        limit=10,
    ).items
    assert len(findings) == 2
    assert [finding.value.unit for finding in findings] == ["USD", "USD"]


def test_callback_transforms_are_incomparable(runtime_session) -> None:
    amount = _measure_ref(runtime_session)
    source = runtime_session.observe(mv.runtime_metric.aggregate(amount, agg="sum"))

    first = source.transform.filter(predicate=lambda data: data["value"] >= 0)
    second = source.transform.filter(predicate=lambda data: data["value"] >= 80)

    assert first.meta.comparable_value_semantics is None
    assert second.meta.comparable_value_semantics is None
    assert first.meta.artifact_id != second.meta.artifact_id
    with pytest.raises(SemanticKindMismatchError, match="comparable"):
        runtime_session.compare(first, second)


def test_current_metric_frame_rejects_omitted_graph_identity_state(runtime_session) -> None:
    amount = _measure_ref(runtime_session)
    frame = runtime_session.observe(mv.runtime_metric.aggregate(amount, agg="sum"))
    meta_path = runtime_session._layout.frames_dir / frame.ref / "meta.json"
    payload = json.loads(meta_path.read_text())
    removed = {
        "metric_identity",
        "metric_identities",
        "expression_graph",
        "expression_fingerprint",
        "semantic_dependency_digest",
        "artifact_identity",
        "key_schema",
        "source_compatibility_domain",
        "component_graph_ref",
        "comparable_value_semantics",
    }
    for field in removed:
        payload.pop(field)
    meta_path.write_text(json.dumps(payload))

    with pytest.raises(FrameMetaInvalidError, match="corrupt current-schema") as exc_info:
        runtime_session.get_frame(frame.ref)
    assert removed <= set(exc_info.value._context["missing_fields"])


def test_current_metric_frame_rejects_corrupt_expression_graph(runtime_session) -> None:
    amount = _measure_ref(runtime_session)
    frame = runtime_session.observe(
        mv.runtime_metric.ratio(
            mv.runtime_metric.aggregate(amount, agg="sum"),
            mv.runtime_metric.aggregate(amount, agg="count"),
        )
    )
    meta_path = runtime_session._layout.frames_dir / frame.ref / "meta.json"
    payload = json.loads(meta_path.read_text())
    root_id = payload["expression_graph"]["roots"][0]
    root = next(
        record for record in payload["expression_graph"]["nodes"] if record["node_id"] == root_id
    )
    root["node"]["numerator_id"] = "missing-node"
    meta_path.write_text(json.dumps(payload))

    with pytest.raises(FrameMetaInvalidError, match="expression_graph") as exc_info:
        runtime_session.get_frame(frame.ref)
    assert exc_info.value._context["path"] == "expression_graph"
    assert "node id mismatch" in exc_info.value._context["reason"]


def test_current_metric_frame_rejects_mismatched_root_fingerprint(runtime_session) -> None:
    amount = _measure_ref(runtime_session)
    frame = runtime_session.observe(mv.runtime_metric.aggregate(amount, agg="sum"))
    meta_path = runtime_session._layout.frames_dir / frame.ref / "meta.json"
    payload = json.loads(meta_path.read_text())
    payload["expression_fingerprint"] = "wrong-root-fingerprint"
    meta_path.write_text(json.dumps(payload))

    with pytest.raises(FrameMetaInvalidError, match="expression_fingerprint") as exc_info:
        runtime_session.get_frame(frame.ref)
    assert exc_info.value._context["path"] == "expression_fingerprint"


def test_current_metric_frame_rejects_missing_typed_replay(runtime_session) -> None:
    amount = _measure_ref(runtime_session)
    frame = runtime_session.observe(mv.runtime_metric.aggregate(amount, agg="sum"))
    meta_path = runtime_session._layout.frames_dir / frame.ref / "meta.json"
    payload = json.loads(meta_path.read_text())
    observe_step = next(step for step in payload["lineage"]["steps"] if step["intent"] == "observe")
    observe_step["params"].pop("replay_expression")
    meta_path.write_text(json.dumps(payload))

    with pytest.raises(FrameMetaInvalidError, match="replay_expression") as exc_info:
        runtime_session.get_frame(frame.ref)
    assert exc_info.value._context["path"] == "lineage.observe.params.replay_expression"


def test_current_component_graph_rejects_missing_child_reference(runtime_session) -> None:
    amount = _measure_ref(runtime_session)
    frame = runtime_session.observe(
        mv.runtime_metric.ratio(
            mv.runtime_metric.aggregate(amount, agg="sum"),
            mv.runtime_metric.aggregate(amount, agg="count"),
        )
    )
    component = frame.components()
    meta_path = runtime_session._layout.frames_dir / component.ref / "meta.json"
    payload = json.loads(meta_path.read_text())
    root = next(
        node
        for node in payload["component_graph"]["nodes"]
        if node["node_id"] in payload["component_graph"]["root_node_ids"]
    )
    root["ordered_children"][0]["node_id"] = "missing-node"
    meta_path.write_text(json.dumps(payload))

    with pytest.raises(FrameMetaInvalidError, match="corrupt current-schema") as exc_info:
        runtime_session.get_frame(component.ref)
    assert "missing-node" in str(exc_info.value._context["validation_errors"])


def test_current_delta_rejects_omitted_comparison_identity(runtime_session) -> None:
    amount = _measure_ref(runtime_session)
    current = runtime_session.observe(mv.runtime_metric.aggregate(amount, agg="sum"))
    baseline = runtime_session.observe(
        runtime_session.catalog.require(ms.Ref.metric("sales.measure_revenue")).ref
    )
    delta = runtime_session.compare(current, baseline)
    meta_path = runtime_session._layout.frames_dir / delta.ref / "meta.json"
    payload = json.loads(meta_path.read_text())
    payload.pop("comparison_identity")
    meta_path.write_text(json.dumps(payload))

    with pytest.raises(FrameMetaInvalidError, match="delta identity") as exc_info:
        runtime_session.get_frame(delta.ref)
    assert exc_info.value._context["missing_state"] == ["comparison_identity"]
