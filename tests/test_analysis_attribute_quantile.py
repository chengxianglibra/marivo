from __future__ import annotations

import subprocess
import sys
import textwrap
from datetime import date
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
    executed = []

    def _run_intermediate(*args, **kwargs):
        executed.append(args[0])
        return pd.DataFrame({"coalition_0": [1.80], "coalition_1": [2.20]})

    monkeypatch.setattr(_nonadditive_attribution, "_run_dataframe", _run_intermediate)
    plan = _nonadditive_attribution._plan_shapley(
        2,
        seed_material="native-percentile-endpoint-regression",
    )
    values = _nonadditive_attribution._native_percentile_coalition_values(
        plan=plan,
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

    assert values[frozenset()] == 2.25
    assert values[frozenset({0, 1})] == 1.79

    contributions, standard_errors, seed = _nonadditive_attribution._shapley_from_values(
        plan,
        coalition_values=values,
    )

    target_delta = 1.79 - 2.25
    assert sum(contributions) == pytest.approx(target_delta, abs=1e-12)
    assert standard_errors == [0.0, 0.0]
    assert seed is None
    assert len(executed) == 1


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

    def record_predicate(table, columns, partitions, *, partition_masks=None):
        predicates.append(tuple(partitions))
        return ibis.literal(True)

    monkeypatch.setattr(_nonadditive_attribution, "_or_partition_predicates", record_predicate)
    monkeypatch.setattr(
        _nonadditive_attribution,
        "_run_dataframe",
        lambda *args, **kwargs: pd.DataFrame({"coalition_0": [2.0]}),
    )
    plan = _nonadditive_attribution.ShapleyPlanV1(
        partition_count=2,
        coalitions=(frozenset({1}),),
        permutation_orders=(),
        seed_fingerprint=None,
    )
    evaluated = _nonadditive_attribution._native_percentile_coalition_values(
        plan=plan,
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

    assert evaluated[frozenset({1})] == 2.0
    assert predicates == [(("CN",), ("DE",)), (("US",),)]


@pytest.mark.parametrize(
    ("partition_count", "expected_batches"),
    [(7, 1), (8, 2), (9, None)],
)
def test_native_percentile_coalitions_execute_in_128_state_batches(
    partition_count: int,
    expected_batches: int | None,
    monkeypatch,
) -> None:
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
    plan = _nonadditive_attribution._plan_shapley(
        partition_count,
        seed_material="exact-batch-count",
    )
    batch_sizes: list[int] = []

    def compile_batch(*args, coalitions, **kwargs):
        batch_size = len(coalitions)
        batch_sizes.append(batch_size)
        return object()

    def run_batch(*args, **kwargs):
        batch_size = batch_sizes[-1]
        return pd.DataFrame({f"coalition_{index}": [float(index)] for index in range(batch_size)})

    monkeypatch.setattr(
        _nonadditive_attribution,
        "trino_native_percentile_coalitions_expression",
        compile_batch,
    )
    monkeypatch.setattr(_nonadditive_attribution, "_run_dataframe", run_batch)
    evaluated = _nonadditive_attribution._native_percentile_coalition_values(
        plan=plan,
        partitions=tuple((f"p{index}",) for index in range(partition_count)),
        partition_members=None,
        current_values=values,
        baseline_values=values,
        current_endpoint=10.0,
        baseline_endpoint=1.0,
        prefix_axes=("region",),
        q=0.90,
        prepared=prepared,
        session=object(),
    )

    if partition_count <= 8:
        assert len(plan.coalitions) == 2**partition_count - 2
        assert len(evaluated) == 2**partition_count
    else:
        expected_batches = (len(plan.coalitions) + 127) // 128
        assert len(evaluated) == len(plan.coalitions) + 2
        assert plan.seed_fingerprint is not None
    assert len(batch_sizes) == expected_batches
    assert all(size <= 128 for size in batch_sizes)


def test_native_percentile_combines_current_and_baseline_partition_counts(
    monkeypatch,
) -> None:
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
    executed = []

    def run_counts(expression, *args, **kwargs):
        executed.append(expression)
        return pd.DataFrame(
            {
                "__marivo_attribution_period": ["current", "current", "baseline"],
                "region": ["CN", None, "US"],
                "__count": [3, 2, 5],
            }
        )

    monkeypatch.setattr(_nonadditive_attribution, "_run_dataframe", run_counts)
    current, baseline = _nonadditive_attribution._native_percentile_partition_counts(
        values,
        values,
        axis_columns=("region",),
        prepared=prepared,
        session=object(),
    )

    assert current == {("CN",): 3, (None,): 2}
    assert baseline == {("US",): 5}
    assert len(executed) == 1
    sql = ibis.to_sql(executed[0], dialect="trino").upper()
    assert sql.count("UNION ALL") == 1
    assert "GROUP BY" in sql


@pytest.mark.parametrize(
    ("returned_value", "expected_kind"),
    [
        (None, "empty_coalition_distribution"),
        (float("nan"), "empty_coalition_distribution"),
        ("not-numeric", "attribution_distribution"),
        (True, "attribution_distribution"),
        (float("inf"), "attribution_distribution"),
    ],
)
def test_native_percentile_batch_rejects_invalid_coalition_values(
    returned_value: object,
    expected_kind: str,
    monkeypatch,
) -> None:
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
    plan = _nonadditive_attribution.ShapleyPlanV1(
        partition_count=2,
        coalitions=(frozenset({0}),),
        permutation_orders=(),
        seed_fingerprint=None,
    )
    monkeypatch.setattr(
        _nonadditive_attribution,
        "_run_dataframe",
        lambda *args, **kwargs: pd.DataFrame({"coalition_0": [returned_value]}),
    )

    with pytest.raises(mv.errors.AttributionDistributionError) as exc_info:
        _nonadditive_attribution._native_percentile_coalition_values(
            plan=plan,
            partitions=(("CN",), ("US",)),
            partition_members=None,
            current_values=values,
            baseline_values=values,
            current_endpoint=1.0,
            baseline_endpoint=0.0,
            prefix_axes=("region",),
            q=0.90,
            prepared=prepared,
            session=object(),
        )

    assert exc_info.value.kind == expected_kind


def test_native_percentile_batch_accepts_zero_values_and_zero_delta(monkeypatch) -> None:
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
    plan = _nonadditive_attribution._plan_shapley(2, seed_material="zero-delta")
    monkeypatch.setattr(
        _nonadditive_attribution,
        "_run_dataframe",
        lambda *args, **kwargs: pd.DataFrame({"coalition_0": [0.0], "coalition_1": [0.0]}),
    )
    evaluated = _nonadditive_attribution._native_percentile_coalition_values(
        plan=plan,
        partitions=(("CN",), ("US",)),
        partition_members=None,
        current_values=values,
        baseline_values=values,
        current_endpoint=0.0,
        baseline_endpoint=0.0,
        prefix_axes=("region",),
        q=0.90,
        prepared=prepared,
        session=object(),
    )
    contributions, errors, _ = _nonadditive_attribution._shapley_from_values(
        plan,
        coalition_values=evaluated,
    )

    assert contributions == [0.0, 0.0]
    assert errors == [0.0, 0.0]


def test_permutation_shapley_plan_deduplicates_requests_without_changing_math() -> None:
    partition_count = 9
    plan = _nonadditive_attribution._plan_shapley(
        partition_count,
        seed_material="permutation-batch-regression",
    )

    def coalition_value(selected: frozenset[int]) -> float:
        return float(sum((index + 1) ** 2 for index in selected) + len(selected) ** 3)

    values = {
        selected: coalition_value(selected)
        for selected in (frozenset(), *plan.coalitions, frozenset(range(partition_count)))
    }
    contributions, standard_errors, seed = _nonadditive_attribution._shapley_from_values(
        plan,
        coalition_values=values,
    )

    scalar_samples: list[list[float]] = [[] for _ in range(partition_count)]
    for order in plan.permutation_orders:
        selected: frozenset[int] = frozenset()
        previous = coalition_value(selected)
        for index in order:
            selected = selected | {index}
            current = coalition_value(selected)
            scalar_samples[index].append(current - previous)
            previous = current
    scalar_means = [sum(samples) / len(samples) for samples in scalar_samples]
    scalar_errors = []
    for samples, mean in zip(scalar_samples, scalar_means, strict=True):
        variance = sum((sample - mean) ** 2 for sample in samples) / (len(samples) - 1)
        scalar_errors.append((variance / len(samples)) ** 0.5)

    requested_path_steps = len(plan.permutation_orders) * (partition_count - 1)
    assert len(plan.coalitions) < requested_path_steps
    assert contributions == scalar_means
    assert standard_errors == scalar_errors
    assert seed is not None and seed.startswith("sha256:")


@pytest.mark.parametrize(
    "endpoint_rows",
    [
        {"current": [1.0], "baseline": [0.0]},
        {"current": [float("nan")], "baseline": [0.0], "delta": [1.0]},
        {"current": [1.0], "baseline": [float("inf")], "delta": [1.0]},
        {"current": [1.0], "baseline": [0.0], "delta": [None]},
    ],
)
def test_quantile_endpoint_buckets_reject_missing_or_non_finite_values(
    endpoint_rows: dict[str, list[object]],
) -> None:
    endpoint = SimpleNamespace(
        ref="delta:invalid-p90",
        _dataframe_copy=lambda: pd.DataFrame(endpoint_rows),
    )

    with pytest.raises(mv.errors.AttributionMaterializationError):
        _nonadditive_attribution._endpoint_buckets(endpoint, bucket_column=None)


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


@pytest.mark.parametrize(
    ("current_bucket", "baseline_bucket"),
    [
        (date(2026, 8, 12), date(2026, 8, 11)),
        (pd.Timestamp("2026-08-12"), pd.Timestamp("2026-08-11")),
    ],
)
def test_native_percentile_scope_normalizes_daily_endpoint_values_to_string_buckets(
    current_bucket: object,
    baseline_bucket: object,
) -> None:
    endpoint = SimpleNamespace(
        ref="delta:daily-p90-string-bucket",
        meta=SimpleNamespace(alignment={"baseline_bucket_column": "bucket_start_b"}),
        _dataframe_copy=lambda: pd.DataFrame(
            {
                "bucket_start": [current_bucket],
                "bucket_start_b": [baseline_bucket],
                "current": [1.79],
                "baseline": [2.25],
                "delta": [-0.46],
            }
        ),
    )
    current_table = ibis.memtable(
        {
            "bucket_start": ["2026-08-12", "2026-08-12", "2026-08-13"],
            "cluster": ["production", "null-value", "other-day"],
            "value": [1.79, None, 9.0],
        }
    )
    baseline_table = ibis.memtable(
        {
            "bucket_start": ["2026-08-11", "2026-08-10"],
            "cluster": ["production", "other-day"],
            "value": [2.25, 8.0],
        }
    )
    current_prepared = _nonadditive_attribution.PreparedEvidenceV1(
        table=current_table,
        value_column="value",
        value_dtype="float64",
        axis_columns=("cluster",),
        axis_bindings=(),
        bucket_column="bucket_start",
        datasource_name="warehouse",
    )
    baseline_prepared = _nonadditive_attribution.PreparedEvidenceV1(
        table=baseline_table,
        value_column="value",
        value_dtype="float64",
        axis_columns=("cluster",),
        axis_bindings=(),
        bucket_column="bucket_start",
        datasource_name="warehouse",
    )
    current_bucket, baseline_bucket, *_ = _nonadditive_attribution._endpoint_buckets(
        endpoint,
        bucket_column="bucket_start",
    )[0]

    current_scope = _nonadditive_attribution._native_percentile_scope(
        current_prepared,
        bucket_value=current_bucket,
    )
    baseline_scope = _nonadditive_attribution._native_percentile_scope(
        baseline_prepared,
        bucket_value=baseline_bucket,
    )

    assert ibis.to_sql(current_scope, dialect="trino")
    assert current_scope.execute()["cluster"].tolist() == ["production"]
    assert baseline_scope.execute()["cluster"].tolist() == ["production"]


def test_native_percentile_scope_casts_timestamp_to_date_bucket() -> None:
    table = ibis.memtable(
        {
            "bucket_start": [date(2026, 8, 12), date(2026, 8, 13)],
            "cluster": ["target", "other-day"],
            "value": [1.0, 2.0],
        },
        schema={"bucket_start": "date", "cluster": "string", "value": "float64"},
    )
    prepared = _nonadditive_attribution.PreparedEvidenceV1(
        table=table,
        value_column="value",
        value_dtype="float64",
        axis_columns=("cluster",),
        axis_bindings=(),
        bucket_column="bucket_start",
        datasource_name="warehouse",
    )

    scope = _nonadditive_attribution._native_percentile_scope(
        prepared,
        bucket_value=pd.Timestamp("2026-08-12"),
    )

    assert scope.execute()["cluster"].tolist() == ["target"]


def test_native_percentile_scope_preserves_null_bucket_filtering() -> None:
    table = ibis.memtable(
        {
            "bucket_start": [None, "2026-08-12"],
            "cluster": ["missing", "dated"],
            "value": [1.0, 2.0],
        },
        schema={"bucket_start": "string", "cluster": "string", "value": "float64"},
    )
    prepared = _nonadditive_attribution.PreparedEvidenceV1(
        table=table,
        value_column="value",
        value_dtype="float64",
        axis_columns=("cluster",),
        axis_bindings=(),
        bucket_column="bucket_start",
        datasource_name="warehouse",
    )

    scope = _nonadditive_attribution._native_percentile_scope(prepared, bucket_value=None)

    assert scope.execute()["cluster"].tolist() == ["missing"]


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
