from __future__ import annotations

import ibis
import pytest

import marivo.analysis as mv
from marivo.analysis import attribution_contract
from marivo.analysis.attribution_contract import build_attribution_basis
from marivo.analysis.intents._nonadditive_attribution import (
    _trino_qdigest_agg,
    _trino_qdigest_agg_bigint,
    _trino_qdigest_agg_real,
    trino_qdigest_coalition_expression,
)
from marivo.datasource.engines import ENGINE_PROFILES
from marivo.refs import RefPayloadV1, SemanticKind
from marivo.refs import ref as ref_factory
from marivo.semantic.metric_graph import (
    AggregateNodeV1,
    ExpressionOccurrenceV1,
    MetricExpressionGraphV1,
)
from marivo.semantic.metric_graph_canonical import intern_nodes
from tests.ref_helpers import make_ref
from tests.shared_fixtures import nonadditive_attribution_project_files


def _percentile_graph() -> MetricExpressionGraphV1:
    node = AggregateNodeV1(
        kind="aggregate",
        target_ref=RefPayloadV1.from_ref(ref_factory.measure("sales.orders.amount")),
        dependency_fingerprint="sha256:test",
        agg=("percentile", 0.95),
        fold=None,
    )
    records = intern_nodes((node,))
    return MetricExpressionGraphV1(
        schema="metric-expression/v1",
        roots=(records[0].node_id,),
        nodes=records,
        occurrences=(ExpressionOccurrenceV1(path="root[0]", node_id=records[0].node_id),),
    )


def test_observe_compare_persist_graph_owned_distinct_basis(
    semantic_project_factory,
    monkeypatch,
) -> None:
    project = semantic_project_factory(nonadditive_attribution_project_files())
    monkeypatch.chdir(project.workspace_dir)
    backend = ibis.duckdb.connect(":memory:")
    backend.raw_sql(
        "CREATE TABLE orders (created_at DATE, region VARCHAR, channel VARCHAR, "
        "user_id INTEGER, amount DOUBLE)"
    )
    backend.raw_sql("INSERT INTO orders VALUES (DATE '2026-01-01', 'US', 'web', 1, 10)")
    session = mv.session.get_or_create(name="demo", backends={"warehouse": lambda: backend})
    metric = session.catalog.require(make_ref("sales.unique_users", SemanticKind.METRIC)).ref
    current = session.observe(
        metric, time_scope=mv.time_scope(start="2026-01-01", end="2026-02-01")
    )
    baseline = session.observe(
        metric, time_scope=mv.time_scope(start="2025-01-01", end="2025-02-01")
    )

    assert current.meta.attribution_basis is not None
    assert current.meta.attribution_basis.kind == "count_distinct"
    assert current.meta.attribution_basis.reproduction.status == "reproducible"
    delta = session.compare(current, baseline)
    assert delta.meta.attribution_basis == current.meta.attribution_basis
    assert delta.contract().attribute_admission.status == "supported"
    assert delta.predicted_attribution_shape() == "distinct_membership"

    basis = delta.meta.attribution_basis
    assert basis is not None
    original_meta = delta.meta
    delta.meta = delta.meta.model_copy(
        update={
            "attribution_basis": basis.model_copy(
                update={
                    "authority": basis.authority.model_copy(
                        update={"expression_graph_fingerprint": "sha256:tampered"}
                    )
                }
            )
        }
    )
    jobs_before = len(session.jobs())
    region = session.catalog.require(make_ref("sales.orders.region", SemanticKind.DIMENSION)).ref
    with pytest.raises(mv.errors.AttributionMaterializationError) as mismatch:
        session.attribute(delta, axes=[region])
    assert mismatch.value._context["recoverability_status"] == "basis_source_graph_mismatch"
    assert len(session.jobs()) == jobs_before
    delta.meta = original_meta

    delta.meta = original_meta.model_copy(
        update={
            "additivity": "semi_additive",
            "aggregation": "sum",
            "attribution_basis": None,
            "status_time_dimension": None,
            "status_time_dimension_ref": None,
        }
    )
    incomplete_semi_additive = delta.contract().attribute_admission
    assert incomplete_semi_additive.status == "blocked"
    assert incomplete_semi_additive.blocker == "missing_additivity_metadata"

    delta.meta = original_meta.model_copy(update={"cumulative": {"kind": "all_history"}})
    with pytest.raises(
        ValueError,
        match="cumulative delta metadata requires cumulative-delta/v1",
    ):
        delta.contract()
    delta.meta = original_meta

    monkeypatch.setattr(attribution_contract, "INSTALLED_ATTRIBUTE_METHODS", frozenset())
    blocked = delta.contract().attribute_admission
    assert blocked.status == "blocked"
    assert blocked.blocker == "operator_method_not_installed"
    assert delta.predicted_attribution_shape() == "distinct_membership"
    with pytest.raises(mv.errors.AttributeAdmissionBlockedError):
        session.attribute(delta, axes=[region])


def test_quantile_basis_admits_trino_qdigest_and_blocks_clickhouse_reservoir() -> None:
    graph = _percentile_graph()
    trino = build_attribution_basis(
        graph,
        source_dtype="float64",
        engine_profile=ENGINE_PROFILES["trino"],
    )
    clickhouse = build_attribution_basis(
        graph,
        source_dtype="float64",
        engine_profile=ENGINE_PROFILES["clickhouse"],
    )

    assert trino is not None and trino.kind == "quantile"
    assert trino.reproduction.status == "reproducible"
    assert trino.reproduction.distribution_representation == "mergeable_sketch"
    assert trino.reproduction.source_dtype == "float64"
    assert clickhouse is not None and clickhouse.kind == "quantile"
    assert clickhouse.reproduction.status == "blocked"
    assert clickhouse.reproduction.blocker == "non_mergeable_sample"


def test_trino_qdigest_adapter_compiles_aggregate_merge_and_value_evaluator() -> None:
    table = ibis.table({"value": "float64", "partition": "string"}, name="facts")
    sketches = table.group_by("partition").aggregate(__qdigest=_trino_qdigest_agg(table.value))
    expression = trino_qdigest_coalition_expression(
        sketches,
        sketch_column="__qdigest",
        q=0.95,
    )

    sql = ibis.to_sql(expression, dialect="trino").upper()
    assert "QDIGEST_AGG" in sql
    assert "MERGE" in sql
    assert "VALUE_AT_QUANTILE" in sql

    integer_table = ibis.table({"value": "int64", "partition": "string"}, name="facts")
    integer_sketches = integer_table.group_by("partition").aggregate(
        __qdigest=_trino_qdigest_agg_bigint(integer_table.value)
    )
    integer_expression = trino_qdigest_coalition_expression(
        integer_sketches,
        sketch_column="__qdigest",
        q=0.95,
        value_dtype="int64",
    )
    integer_sql = ibis.to_sql(integer_expression, dialect="trino").upper()
    assert "QDIGEST_AGG" in integer_sql
    assert "VALUE_AT_QUANTILE" in integer_sql
    assert "QDIGEST_AGG(CAST" not in integer_sql

    real_table = ibis.table({"value": "float32", "partition": "string"}, name="facts")
    real_sketches = real_table.group_by("partition").aggregate(
        __qdigest=_trino_qdigest_agg_real(real_table.value)
    )
    real_expression = trino_qdigest_coalition_expression(
        real_sketches,
        sketch_column="__qdigest",
        q=0.95,
        value_dtype="float32",
    )
    real_sql = ibis.to_sql(real_expression, dialect="trino").upper()
    assert real_expression.value.type().is_float32()
    assert "QDIGEST_AGG(CAST" not in real_sql


def test_trino_qdigest_basis_blocks_unsupported_unsigned_source_type() -> None:
    basis = build_attribution_basis(
        _percentile_graph(),
        source_dtype="uint64",
        engine_profile=ENGINE_PROFILES["trino"],
    )

    assert basis is not None and basis.kind == "quantile"
    assert basis.reproduction.status == "blocked"
    assert basis.reproduction.blocker == "matching_evaluator_unavailable"
