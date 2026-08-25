from __future__ import annotations

import subprocess
import sys
import textwrap
from types import SimpleNamespace

import ibis
import pandas as pd
import pytest

import marivo.analysis as mv
from marivo.analysis.intents import _nonadditive_attribution
from marivo.analysis.intents._nonadditive_attribution import weighted_linear_quantile
from marivo.refs import SemanticKind
from tests.ref_helpers import make_ref
from tests.shared_fixtures import nonadditive_attribution_project_files


def test_median_replacement_shapley_reconciles_independent_endpoint(
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
    backend.raw_sql(
        "INSERT INTO orders VALUES "
        "(DATE '2026-01-01', 'US', 'web', 1, 10),"
        "(DATE '2026-01-02', 'US', 'store', 2, 20),"
        "(DATE '2026-01-03', 'CN', 'web', 3, 30),"
        "(DATE '2025-01-01', 'US', 'web', 1, 5),"
        "(DATE '2025-01-02', 'CN', 'web', 2, 15),"
        "(DATE '2025-01-03', 'CN', 'store', 3, 25)"
    )
    session = mv.session.get_or_create(name="demo", backends={"warehouse": lambda: backend})
    metric = session.catalog.require(make_ref("sales.median_amount", SemanticKind.METRIC)).ref
    region = session.catalog.require(make_ref("sales.orders.region", SemanticKind.DIMENSION)).ref
    current = session.observe(
        metric, time_scope=mv.time_scope(start="2026-01-01", end="2026-02-01")
    )
    baseline = session.observe(
        metric, time_scope=mv.time_scope(start="2025-01-01", end="2025-02-01")
    )
    result = session.attribute(session.compare(current, baseline), axes=[region])

    rows = result.to_pandas().set_index("region")
    assert result.attribution_shape == "quantile_replacement"
    assert rows.loc["US", "contribution"] == pytest.approx(2.5)
    assert rows.loc["CN", "contribution"] == pytest.approx(2.5)
    assert rows["contribution"].sum() == pytest.approx(5.0)
    assert (rows["contribution_std_error"] == 0.0).all()
    assert result.meta.method_evidence is not None
    assert result.meta.method_evidence.kind == "quantile_replacement"
    assert result.meta.method_evidence.q == 0.5


def test_median_and_percentile_p50_have_the_same_replacement_game(
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
    backend.raw_sql(
        "INSERT INTO orders VALUES "
        "(DATE '2026-01-01', 'US', 'web', 1, 10),"
        "(DATE '2026-01-02', 'US', 'store', 2, 20),"
        "(DATE '2026-01-03', 'CN', 'web', 3, 30),"
        "(DATE '2025-01-01', 'US', 'web', 1, 5),"
        "(DATE '2025-01-02', 'CN', 'web', 2, 15),"
        "(DATE '2025-01-03', 'CN', 'store', 3, 25)"
    )
    session = mv.session.get_or_create(name="demo", backends={"warehouse": lambda: backend})
    region = session.catalog.require(make_ref("sales.orders.region", SemanticKind.DIMENSION)).ref
    results = []
    for metric_id in ("sales.median_amount", "sales.p50_amount"):
        metric = session.catalog.require(make_ref(metric_id, SemanticKind.METRIC)).ref
        current = session.observe(
            metric, time_scope=mv.time_scope(start="2026-01-01", end="2026-02-01")
        )
        baseline = session.observe(
            metric, time_scope=mv.time_scope(start="2025-01-01", end="2025-02-01")
        )
        results.append(
            session.attribute(session.compare(current, baseline), axes=[region])
            .to_pandas()
            .sort_values("region")["contribution"]
            .tolist()
        )

    assert results[0] == pytest.approx(results[1])


def test_quantile_partition_limit_precedes_frequency_materialization(
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
    values = ",".join(
        f"(DATE '2026-01-01', 'r{index}', 'web', {index}, {index})" for index in range(65)
    )
    backend.raw_sql(f"INSERT INTO orders VALUES {values}")
    session = mv.session.get_or_create(name="demo", backends={"warehouse": lambda: backend})
    metric = session.catalog.require(make_ref("sales.median_amount", SemanticKind.METRIC)).ref
    region = session.catalog.require(make_ref("sales.orders.region", SemanticKind.DIMENSION)).ref
    current = session.observe(
        metric, time_scope=mv.time_scope(start="2026-01-01", end="2026-02-01")
    )
    baseline = session.observe(
        metric, time_scope=mv.time_scope(start="2026-01-01", end="2026-02-01")
    )

    def _frequency_must_not_run(*args, **kwargs):
        raise AssertionError("frequency evidence materialized before partition admission")

    monkeypatch.setattr(_nonadditive_attribution, "_frequency_frame", _frequency_must_not_run)
    with pytest.raises(mv.errors.AttributionDistributionError) as exc_info:
        session.attribute(session.compare(current, baseline), axes=[region])

    assert exc_info.value.kind == "partition_limit_exceeded"


def test_quantile_top_k_applies_before_partition_admission(
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
    values = ",".join(
        f"(DATE '2026-01-01', 'r{index}', 'web', {index}, {index})" for index in range(65)
    )
    backend.raw_sql(f"INSERT INTO orders VALUES {values}")
    session = mv.session.get_or_create(name="demo", backends={"warehouse": lambda: backend})
    metric = session.catalog.require(make_ref("sales.median_amount", SemanticKind.METRIC)).ref
    region = session.catalog.require(make_ref("sales.orders.region", SemanticKind.DIMENSION)).ref
    current = session.observe(
        metric, time_scope=mv.time_scope(start="2026-01-01", end="2026-02-01")
    )
    baseline = session.observe(
        metric, time_scope=mv.time_scope(start="2026-01-01", end="2026-02-01")
    )

    result = session.attribute(session.compare(current, baseline), axes=[region], top_k=5)

    assert len(result.to_pandas()) == 6
    other = result.to_pandas().query("attribution_other_mask == 1")
    assert len(other) == 1
    assert other["contribution_std_error"].notna().all()
    assert result.meta.top_k_selection is not None
    assert result.meta.top_k_selection.original_partition_count == 65
    assert result.meta.top_k_selection.effective_partition_count == 6
    assert result.meta.method_evidence is not None
    assert result.meta.method_evidence.coalition == "exact_shapley"


def test_permutation_uncertainty_is_separate_from_source_error(
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
    current_values = ",".join(
        f"(DATE '2026-01-01', 'r{index}', 'web', {index}, {index * 2 + 1})" for index in range(9)
    )
    baseline_values = ",".join(
        f"(DATE '2025-01-01', 'r{index}', 'web', {index}, {index + 1})" for index in range(9)
    )
    backend.raw_sql(f"INSERT INTO orders VALUES {current_values},{baseline_values}")
    session = mv.session.get_or_create(name="demo", backends={"warehouse": lambda: backend})
    metric = session.catalog.require(make_ref("sales.median_amount", SemanticKind.METRIC)).ref
    region = session.catalog.require(make_ref("sales.orders.region", SemanticKind.DIMENSION)).ref
    current = session.observe(
        metric, time_scope=mv.time_scope(start="2026-01-01", end="2026-02-01")
    )
    baseline = session.observe(
        metric, time_scope=mv.time_scope(start="2025-01-01", end="2025-02-01")
    )

    result = session.attribute(session.compare(current, baseline), axes=[region])
    evidence = result.meta.method_evidence

    assert evidence is not None and evidence.kind == "quantile_replacement"
    assert evidence.coalition == "permutation_shapley"
    assert evidence.permutation_count == 128
    assert evidence.source_error_bound is None
    assert result.to_pandas()["contribution_std_error"].max() > 0


def test_hierarchy_quantile_preserves_each_scope_execution_method(
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
    current_values = ",".join(
        f"(DATE '2026-01-01', 'all', 'c{index}', {index}, {index * 2 + 1})" for index in range(9)
    )
    baseline_values = ",".join(
        f"(DATE '2025-01-01', 'all', 'c{index}', {index}, {index + 1})" for index in range(9)
    )
    backend.raw_sql(f"INSERT INTO orders VALUES {current_values},{baseline_values}")
    session = mv.session.get_or_create(name="demo", backends={"warehouse": lambda: backend})
    metric = session.catalog.require(make_ref("sales.median_amount", SemanticKind.METRIC)).ref
    region = session.catalog.require(make_ref("sales.orders.region", SemanticKind.DIMENSION)).ref
    channel = session.catalog.require(make_ref("sales.orders.channel", SemanticKind.DIMENSION)).ref
    current = session.observe(
        metric, time_scope=mv.time_scope(start="2026-01-01", end="2026-02-01")
    )
    baseline = session.observe(
        metric, time_scope=mv.time_scope(start="2025-01-01", end="2025-02-01")
    )

    result = session.attribute(
        session.compare(current, baseline),
        axes=[region, channel],
        mode="hierarchy",
    )
    evidence = result.meta.method_evidence

    assert evidence is not None and evidence.kind == "quantile_replacement"
    assert result.meta.resolution_evidence is not None
    assert result.meta.resolution_evidence.resolution_semantics == "independent"
    assert result.meta.resolution_evidence.rollup_safe is False
    assert evidence.coalition == "mixed"
    assert [
        item.quantile_execution.coalition
        for item in evidence.scope_reconciliations
        if item.quantile_execution is not None
    ] == ["exact_shapley", "permutation_shapley"]

    selected = result.at_resolution(axes=[region])
    selected_evidence = selected.meta.method_evidence
    assert selected_evidence is not None
    assert selected_evidence.kind == "quantile_replacement"
    assert selected_evidence.coalition == "exact_shapley"
    assert selected_evidence.permutation_count == 0
    assert len(selected_evidence.scope_reconciliations) == 1


def test_quantile_blocks_an_empty_intermediate_coalition(
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
    backend.raw_sql(
        "INSERT INTO orders VALUES "
        "(DATE '2026-01-01', 'CN', 'web', 1, 30),"
        "(DATE '2025-01-01', 'US', 'web', 2, 10)"
    )
    session = mv.session.get_or_create(name="demo", backends={"warehouse": lambda: backend})
    metric = session.catalog.require(make_ref("sales.median_amount", SemanticKind.METRIC)).ref
    region = session.catalog.require(make_ref("sales.orders.region", SemanticKind.DIMENSION)).ref
    current = session.observe(
        metric, time_scope=mv.time_scope(start="2026-01-01", end="2026-02-01")
    )
    baseline = session.observe(
        metric, time_scope=mv.time_scope(start="2025-01-01", end="2025-02-01")
    )

    with pytest.raises(mv.errors.AttributionDistributionError) as exc_info:
        session.attribute(session.compare(current, baseline), axes=[region])

    assert exc_info.value.kind == "empty_coalition_distribution"


def test_weighted_linear_quantile_handles_ties_and_nulls() -> None:
    values = pd.DataFrame(
        {
            "value": [1.0, 2.0, 2.0, None],
            "frequency": [1, 1, 2, 100],
        }
    )

    assert weighted_linear_quantile(values, q=0.5) == pytest.approx(2.0)


def test_native_percentile_replay_reuses_observed_endpoints_and_reconciles(
    monkeypatch,
) -> None:
    current_values = ibis.table({"region": "string", "value": "float64"}, name="current_values")
    baseline_values = ibis.table({"region": "string", "value": "float64"}, name="baseline_values")
    prepared = _nonadditive_attribution.PreparedEvidenceV1(
        table=current_values,
        value_column="value",
        value_dtype="float64",
        axis_columns=("region",),
        axis_bindings=(),
        bucket_column=None,
        datasource_name="warehouse",
    )
    intermediate_values = iter((1.80, 2.20))
    executed = []

    def _run_intermediate(*args, **kwargs):
        executed.append(args[0])
        return pd.DataFrame({"value": [next(intermediate_values)]})

    monkeypatch.setattr(_nonadditive_attribution, "_run_dataframe", _run_intermediate)
    evaluate = _nonadditive_attribution._native_percentile_coalition_evaluator(
        partitions=(("CN",), ("US",)),
        partition_members=None,
        current_values=current_values,
        baseline_values=baseline_values,
        current_endpoint=1.79,
        baseline_endpoint=2.25,
        prefix_axes=("region",),
        q=0.90,
        prepared=prepared,
        session=object(),
    )

    assert evaluate(frozenset()) == 2.25
    assert evaluate(frozenset({0, 1})) == 1.79
    assert executed == []

    contributions, standard_errors, seed = _nonadditive_attribution._shapley_from_evaluator(
        2,
        evaluate=evaluate,
        seed_material="native-percentile-endpoint-regression",
    )

    target_delta = 1.79 - 2.25
    assert sum(contributions) == pytest.approx(target_delta, abs=1e-12)
    assert standard_errors == [0.0, 0.0]
    assert seed is None
    assert len(executed) == 2


def test_native_percentile_top_k_expands_other_to_raw_partitions(monkeypatch) -> None:
    values = ibis.table({"region": "string", "value": "float64"}, name="values")
    prepared = _nonadditive_attribution.PreparedEvidenceV1(
        table=values,
        value_column="value",
        value_dtype="float64",
        axis_columns=("region",),
        axis_bindings=(),
        bucket_column=None,
        datasource_name="warehouse",
    )
    predicates: list[tuple[tuple[object, ...], ...]] = []

    def record_predicate(table, columns, partitions):
        predicates.append(tuple(partitions))
        return ibis.literal(True)

    monkeypatch.setattr(_nonadditive_attribution, "_or_partition_predicates", record_predicate)
    monkeypatch.setattr(
        _nonadditive_attribution,
        "_run_dataframe",
        lambda *args, **kwargs: pd.DataFrame({"value": [2.0]}),
    )
    evaluate = _nonadditive_attribution._native_percentile_coalition_evaluator(
        partitions=(("US", 0), (None, 1)),
        partition_members={
            ("US", 0): (("US",),),
            (None, 1): (("CN",), ("DE",)),
        },
        current_values=values,
        baseline_values=values,
        current_endpoint=3.0,
        baseline_endpoint=1.0,
        prefix_axes=("region",),
        q=0.90,
        prepared=prepared,
        session=object(),
    )

    assert evaluate(frozenset({1})) == 2.0
    assert predicates == [(("CN",), ("DE",)), (("US",),)]


def test_quantile_endpoint_buckets_preserve_aligned_endpoint_values() -> None:
    endpoint = SimpleNamespace(
        ref="delta:daily-p90",
        meta=SimpleNamespace(alignment={"baseline_bucket_column": "bucket_start_b"}),
        _dataframe_copy=lambda: pd.DataFrame(
            {
                "bucket_start": [pd.Timestamp("2026-08-12")],
                "bucket_start_b": [pd.Timestamp("2026-08-11")],
                "current": [1.79],
                "baseline": [2.25],
                "delta": [-0.46],
            }
        ),
    )

    buckets = _nonadditive_attribution._endpoint_buckets(
        endpoint,
        bucket_column="bucket_start",
    )

    assert buckets == [
        (
            pd.Timestamp("2026-08-12"),
            pd.Timestamp("2026-08-11"),
            1.79,
            2.25,
            -0.46,
        )
    ]


def test_permutation_quantile_is_deterministic_across_subprocesses() -> None:
    code = textwrap.dedent(
        """
        import json
        import pandas as pd
        from marivo.analysis.intents._nonadditive_attribution import _permutation_shapley

        partitions = [(f"p{index}",) for index in range(9)]
        current = {
            partition: pd.DataFrame({"value": [index + 1.0], "frequency": [1]})
            for index, partition in enumerate(partitions)
        }
        baseline = {
            partition: pd.DataFrame({"value": [index * 0.5 + 1.0], "frequency": [1]})
            for index, partition in enumerate(partitions)
        }
        result = _permutation_shapley(
            current,
            baseline,
            partitions,
            q=0.5,
            seed_material="artifact|bucket|resolution|quantile-replacement/v1",
        )
        print(json.dumps(result, sort_keys=True))
        """
    )

    first = subprocess.check_output([sys.executable, "-c", code], text=True)
    second = subprocess.check_output([sys.executable, "-c", code], text=True)

    assert first == second
