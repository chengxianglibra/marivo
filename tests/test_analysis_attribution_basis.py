from __future__ import annotations

import itertools
import sys

import ibis
import ibis.expr.operations as ops
import pytest

import marivo.analysis as mv
from marivo.analysis import attribution_contract
from marivo.analysis.attribution_contract import build_attribution_basis
from marivo.analysis.intents._nonadditive_attribution import (
    trino_native_percentile_coalitions_expression,
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
    admission = delta.contract().attribute_admission
    assert admission.status == "supported"
    assert admission.mode.multiple_axes == ("joint", "hierarchy")
    assert admission.mode.multiple_axes_default == "joint"
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
    jobs_before = len(session.runs(limit=100).items)
    region = session.catalog.require(make_ref("sales.orders.region", SemanticKind.DIMENSION)).ref
    with pytest.raises(mv.errors.AttributionMaterializationError) as mismatch:
        session.attribute(delta, axes=[region])
    assert mismatch.value._context["recoverability_status"] == "basis_source_graph_mismatch"
    assert len(session.runs(limit=100).items) == jobs_before + 1
    failed = session.runs(status="failed", capability_id="attribute").items
    assert failed[0].failure.error_type == "AttributionMaterializationError"
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


def test_quantile_basis_admits_trino_native_percentile_and_blocks_clickhouse_reservoir() -> None:
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
    assert trino.reproduction.source_method == "approx_percentile"
    assert trino.reproduction.distribution_representation == "native_percentile_replay"
    assert trino.reproduction.source_dtype == "float64"
    assert (
        attribution_contract.required_attribute_method(trino)
        == "quantile_trino_approx_percentile/v1"
    )
    assert clickhouse is not None and clickhouse.kind == "quantile"
    assert clickhouse.reproduction.status == "blocked"
    assert clickhouse.reproduction.blocker == "non_mergeable_sample"


@pytest.mark.parametrize("value_dtype", ["float64", "int64", "float32"])
def test_trino_native_percentile_adapter_compiles_batched_union_all_replay(
    value_dtype: str,
) -> None:
    current_table = ibis.table({"region": "string", "value": value_dtype}, name="current_values")
    baseline_table = ibis.table({"region": "string", "value": value_dtype}, name="baseline_values")
    current = current_table.filter(current_table.value.notnull())
    baseline = baseline_table.filter(baseline_table.value.notnull())
    expression = trino_native_percentile_coalitions_expression(
        current,
        baseline,
        coalitions=(frozenset({0}), frozenset({1})),
        partitions=(("CN",), (None,)),
        partition_members=None,
        prefix_axes=("region",),
        value_column="value",
        q=0.95,
    )

    sql = ibis.to_sql(expression, dialect="trino").upper()
    assert sql.count("APPROX_PERCENTILE") == 2
    assert sql.count("FILTER(WHERE") == 2
    assert sql.count("UNION ALL") == 1
    assert "IS NOT NULL" in sql
    assert "IS NULL" in sql
    assert "QDIGEST_AGG" not in sql
    assert "TDIGEST_AGG" not in sql
    assert "MERGE(" not in sql


def test_trino_native_percentile_reuses_regex_derived_top_k_predicates() -> None:
    query_info = ibis.table(
        {
            "user": "string",
            "client_tags": "string",
            "elapsed_time": "float64",
            "create_time": "timestamp",
        },
        name="query_info",
    )

    def period_values(start: str) -> ibis.Table:
        business_tag = ibis.cases(
            (
                query_info.user == "sys_oneservice",
                query_info.client_tags.re_extract(r"api_id=([^,]+)", 1),
            ),
        )
        prepared = query_info.filter(query_info.create_time >= ibis.timestamp(start)).mutate(
            business_tag=business_tag
        )
        return prepared.select("business_tag", "elapsed_time")

    partitions = tuple((f"api_{index}",) for index in range(4))
    partition_members = {
        partitions[0]: (("api_0",),),
        partitions[1]: (("api_1",),),
        partitions[2]: (("api_2",),),
        partitions[3]: tuple((f"api_{index}",) for index in range(3, 28)),
    }
    coalitions = tuple(
        frozenset(selected)
        for size in range(1, len(partitions))
        for selected in itertools.combinations(range(len(partitions)), size)
    )
    expression = trino_native_percentile_coalitions_expression(
        period_values("2026-08-20"),
        period_values("2026-08-13"),
        coalitions=coalitions,
        partitions=partitions,
        partition_members=partition_members,
        prefix_axes=("business_tag",),
        value_column="elapsed_time",
        q=0.90,
    )

    recursion_limit = sys.getrecursionlimit()
    try:
        sys.setrecursionlimit(60)
        expression.op().find(ops.InMemoryTable)
    finally:
        sys.setrecursionlimit(recursion_limit)

    sql = ibis.to_sql(expression, dialect="trino").upper()
    assert sql.count("APPROX_PERCENTILE") == len(coalitions)
    assert sql.count("FILTER(WHERE") == len(coalitions)
    assert sql.count("UNION ALL") == 1
    assert "REGEXP_EXTRACT" in sql


def test_trino_native_percentile_basis_blocks_unsupported_unsigned_source_type() -> None:
    basis = build_attribution_basis(
        _percentile_graph(),
        source_dtype="uint64",
        engine_profile=ENGINE_PROFILES["trino"],
    )

    assert basis is not None and basis.kind == "quantile"
    assert basis.reproduction.status == "blocked"
    assert basis.reproduction.blocker == "matching_evaluator_unavailable"


def test_legacy_qdigest_distribution_representation_is_not_accepted() -> None:
    with pytest.raises(ValueError):
        attribution_contract.ReproducibleQuantileAttributionV1.model_validate(
            {
                "source_mode": "approximate",
                "source_method": "qdigest",
                "source_dtype": "float64",
                "distribution_representation": "mergeable_sketch",
            }
        )
