"""Independent coordinate and current-row reduction acceptance."""

from __future__ import annotations

import duckdb
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from tests.shared_fixtures import DslCaseFactory, export_dsl_parquet_models
from tests.support.paths import PROJECT_ROOT


@pytest.mark.runtime
@pytest.mark.parametrize("method", ["min", "max", "count_defined"])
def test_row_methods_source_and_fixed(
    analysis_dsl_case_factory: DslCaseFactory, method: str
) -> None:
    case = analysis_dsl_case_factory("j1")
    names = case.names
    if method != "count_defined":
        with duckdb.connect(str(case.database_path)) as connection:
            connection.execute(
                f'DELETE FROM "{names.customer}" WHERE "{names.customer_id}" = ?', ["D"]
            )
    members = case.session.members(ms.ref.entity(f"{names.domain}.{names.customer}"))
    observed = members.observe(
        ms.ref.metric(f"{names.domain}.{names.revenue}"),
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=ms.ref.relationship(f"{names.domain}.{names.buyer}"),
        by=(mv.member(),),
    )
    assert isinstance(observed, mv.LogicalNumericRelation)
    descriptor = {"min": mv.min(), "max": mv.max(), "count_defined": mv.count_defined()}[method]
    fixed = observed.execute()
    expected = {"min": 150, "max": 450, "count_defined": 3}[method]
    direct = observed.summarize(descriptor).execute().to_pandas()
    offline = case.database_path.with_suffix(".offline")
    case.database_path.rename(offline)
    try:
        local = fixed.summarize(descriptor).execute().to_pandas()
    finally:
        offline.rename(case.database_path)
    assert direct.iloc[0]["value"] == expected
    assert local.iloc[0]["value"] == expected


@pytest.mark.runtime
@pytest.mark.parametrize("fixed", [False, True])
@pytest.mark.parametrize("parquet", [False, True])
def test_complete_coordinate_tuple_rollup(
    analysis_dsl_case_factory: DslCaseFactory, fixed: bool, parquet: bool
) -> None:
    case = analysis_dsl_case_factory("j1")
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute(
            'UPDATE "order" SET status = ? WHERE channel = ?', ["refunded", "mobile"]
        )
    if parquet:
        export_dsl_parquet_models(case, case.root)
        ms.load(workspace_dir=case.root)
    n = case.names
    channel = ms.ref.dimension(f"{n.domain}.{n.order}.{n.channel}")
    status = ms.ref.dimension(f"{n.domain}.{n.order}.{n.status}")
    observed = case.session.members(ms.ref.entity(f"{n.domain}.{n.customer}")).observe(
        ms.ref.metric(f"{n.domain}.{n.revenue}"),
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=ms.ref.relationship(f"{n.domain}.{n.buyer}"),
        by=(
            mv.member(),
            channel,
            status,
        ),
    )
    assert isinstance(observed, mv.LogicalNumericRelation)
    result = (observed.execute() if fixed else observed).group_by(channel, status).execute()
    frame = result.to_pandas()
    assert frame["value"].tolist() == [150, 850]
    assert len(frame) == 2


@pytest.mark.runtime
@pytest.mark.parametrize("fixed", [False, True])
def test_category_counts_without_numeric_conversion(
    analysis_dsl_case_factory: DslCaseFactory, fixed: bool
) -> None:
    case = analysis_dsl_case_factory("j1")
    n = case.names
    category = case.session.members(ms.ref.entity(f"{n.domain}.{n.customer}")).read(
        ms.ref.dimension(f"{n.domain}.{n.customer}.{n.region}")
    )
    current = category.execute() if fixed else category
    assert current.summarize(mv.count()).execute().to_pandas().iloc[0]["value"] == 4
    assert current.summarize(mv.count_defined()).execute().to_pandas().iloc[0]["value"] == 4


@pytest.mark.runtime
@pytest.mark.parametrize("method", [mv.min(), mv.max(), mv.mean(), mv.sum()])
def test_empty_numeric_row_state(
    analysis_dsl_case_factory: DslCaseFactory, method: mv.RowMethod
) -> None:
    case = analysis_dsl_case_factory("j1")
    n = case.names
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute(f'DELETE FROM "{n.order}"')
        connection.execute(f'DELETE FROM "{n.customer}"')
    observed = case.session.members(ms.ref.entity(f"{n.domain}.{n.customer}")).observe(
        ms.ref.metric(f"{n.domain}.{n.revenue}"),
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=ms.ref.relationship(f"{n.domain}.{n.buyer}"),
        by=(mv.member(),),
    )
    assert isinstance(observed, mv.LogicalNumericRelation)
    for current in (observed, observed.execute()):
        statistic = current.summarize(method).execute()
        row = statistic.to_pandas().iloc[0]
        merged = statistic.rollup().execute().to_pandas().iloc[0]
        assert merged["cell_tag"] == row["cell_tag"]
        assert merged["cell_reason"] == row["cell_reason"]
        assert row["cell_tag"] == ("defined" if method.kind == "sum" else "undefined")
        if method.kind != "sum":
            assert row["cell_reason"] == "empty_" + method.kind


@pytest.mark.runtime
@pytest.mark.parametrize("fixed", [False, True])
def test_explicit_classification_rollup_and_current_rows(
    analysis_dsl_case_factory: DslCaseFactory, fixed: bool
) -> None:
    case = analysis_dsl_case_factory("j1")
    n = case.names
    members = case.session.members(ms.ref.entity(f"{n.domain}.{n.customer}"))
    region = members.read(ms.ref.dimension(f"{n.domain}.{n.customer}.{n.region}"))
    observed = members.observe(
        ms.ref.metric(f"{n.domain}.{n.revenue}"),
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=ms.ref.relationship(f"{n.domain}.{n.buyer}"),
        by=(mv.member(),),
    )
    assert isinstance(observed, mv.LogicalNumericRelation)
    current = observed.execute() if fixed else observed
    category = region.execute() if fixed else region
    grouped = current.group_by(category)
    total = grouped.rollup().execute().to_pandas().set_index("group")
    assert total.loc["east", "value"] == 600
    assert total.loc["south", "value"] == 400
    counts = grouped.summarize(mv.count()).execute().to_pandas().set_index("group")
    assert counts["value"].to_dict() == {"east": 2, "south": 1, "west": 1}


@pytest.mark.runtime
@pytest.mark.parametrize("fixed", [False, True])
def test_selected_row_groups_preserve_count_mean_state(
    analysis_dsl_case_factory: DslCaseFactory, fixed: bool
) -> None:
    case = analysis_dsl_case_factory("j1")
    n = case.names
    members = case.session.members(ms.ref.entity(f"{n.domain}.{n.customer}"))
    region = members.read(ms.ref.dimension(f"{n.domain}.{n.customer}.{n.region}"))
    selected = region.where(region.value.eq("east")).members()
    categories = selected.read(ms.ref.dimension(f"{n.domain}.{n.customer}.{n.region}"))
    observed = selected.observe(
        ms.ref.metric(f"{n.domain}.{n.revenue}"),
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=ms.ref.relationship(f"{n.domain}.{n.buyer}"),
        by=(mv.member(),),
    )
    assert isinstance(observed, mv.LogicalNumericRelation)
    current = observed.execute() if fixed else observed
    category = categories.execute() if fixed else categories
    grouped = current.group_by(category)
    counts = grouped.summarize(mv.count()).execute().to_pandas().set_index("group")
    assert counts["value"].to_dict() == {"east": 2}
    mean = grouped.summarize(mv.mean()).execute()
    frame = mean.to_pandas().set_index("group")
    assert frame.loc["east", "value"] == 300
    state = next(
        p.table.to_pylist() for p in mean._dataset.verified().parts if p.role == "row_state"
    )
    assert state == [
        {"key_0": "east", "row_state__sum": 600, "row_state__count": 2},
    ]
    merged = mean.group_by(ms.ref.dimension("sales.customer.region")).rollup().execute()
    assert merged.to_pandas().equals(mean.to_pandas())
    assert merged._node.root.signature.quantity == mean._node.root.signature.quantity
    assert (
        next(p.table.to_pylist() for p in merged._dataset.verified().parts if p.role == "row_state")
        == state
    )
    assert any(a.call == "relation.rollup()" for a in merged.contract().actions)
    assert mean.rollup().execute().to_pandas().iloc[0]["value"] == 300


@pytest.mark.runtime
def test_original_mean_merges_support_not_finished_values(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j3_weighting")
    model = case.root / "models/semantic/sales/models.py"
    model.write_text(
        model.read_text()
        + "\nmean_amount = ms.aggregate(name='mean_amount', measure=line_amount, agg='mean', time=ordered_at, time_via=(line_order,))\n"
    )
    ms.load(workspace_dir=case.root)
    observed = case.session.members(ms.ref.entity("sales.customer")).observe(
        ms.ref.metric("sales.mean_amount"),
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=mv.path(
            ms.ref.relationship("sales.line_order"), ms.ref.relationship("sales.order_buyer")
        ),
        by=(mv.member(),),
    )
    assert isinstance(observed, mv.LogicalNumericRelation)
    fixed = observed.execute()
    assert fixed.to_pandas()["value"].tolist() == [1, 100]
    assert observed.rollup().execute().to_pandas().iloc[0]["value"] == pytest.approx(200 / 101)
    assert fixed.rollup().execute().to_pandas().iloc[0]["value"] == pytest.approx(200 / 101)
    assert fixed.summarize(mv.mean()).execute().to_pandas().iloc[0]["value"] == 50.5


@pytest.mark.runtime
@pytest.mark.parametrize("fixed", [False, True])
def test_partial_reduction_matches_components_and_preserves_keys(
    analysis_dsl_case_factory: DslCaseFactory, fixed: bool
) -> None:
    case = analysis_dsl_case_factory("j1")
    customer = ms.ref.entity("sales.customer")
    channel = ms.ref.dimension("sales.order.channel")
    status = ms.ref.dimension("sales.order.status")
    observed = case.session.members(customer).observe(
        ms.ref.metric("sales.revenue"),
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=ms.ref.relationship("sales.order_buyer"),
        by=(
            mv.member(),
            channel,
            status,
        ),
    )
    current = observed.execute() if fixed else observed
    direct = current.rollup().execute()
    partial = current.group_by(customer, channel).rollup().execute()
    assert partial.to_pandas()["value"].tolist() == [450, 150, 400]
    hierarchical = partial.group_by(channel).rollup().execute().rollup().execute()
    assert direct.to_pandas()["value"].tolist() == [1000]
    assert hierarchical.to_pandas()["value"].tolist() == [1000]

    def components(result):
        return next(
            p.table.to_pylist()
            for p in result._dataset.verified().parts
            if p.role == "original_state"
        )

    assert (
        components(direct)
        == components(hierarchical)
        == [{"original_state__sum": 1000, "original_state__non_null_count": 3}]
    )
    assert direct._node.root.signature.quantity == hierarchical._node.root.signature.quantity
    assert any(a.call == "relation.rollup()" for a in hierarchical.contract().actions)


@pytest.mark.runtime
@pytest.mark.parametrize(
    "method", [mv.sum(), mv.min(), mv.max(), mv.mean(), mv.count(), mv.count_defined()]
)
def test_row_statistics_grouped_merge_preserves_empty_state_and_identity(
    retained_coordinates_case, method
):
    case = retained_coordinates_case
    members = case.session.members(ms.ref.entity("sales.customer"))
    region = members.read(ms.ref.dimension("sales.customer.region"))
    values = members.observe(
        ms.ref.metric("sales.revenue"),
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=ms.ref.relationship("sales.order_buyer"),
        by=(mv.member(),),
    )
    grouped = values.group_by(region).summarize(method)
    direct = values.summarize(method).execute()
    for current in (grouped, grouped.execute()):
        result = current.rollup().execute()
        assert result.to_pandas()["value"].tolist() == direct.to_pandas()["value"].tolist()
        assert result._node.root.signature.quantity == current._node.root.signature.quantity
        assert next(
            p.table.to_pylist() for p in result._dataset.verified().parts if p.role == "row_state"
        ) == next(
            p.table.to_pylist() for p in direct._dataset.verified().parts if p.role == "row_state"
        )


@pytest.mark.runtime
def test_cold_grouped_statistic_recovers_without_models_source_or_duckdb(retained_coordinates_case):
    import os
    import subprocess
    import sys

    case = retained_coordinates_case
    members = case.session.members(ms.ref.entity("sales.customer"))
    region = members.read(ms.ref.dimension("sales.customer.region"))
    observed = members.observe(
        ms.ref.metric("sales.revenue"),
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=ms.ref.relationship("sales.order_buyer"),
        by=(mv.member(),),
    )
    fixed = observed.group_by(region).summarize(mv.mean()).execute()
    case.database_path.rename(case.database_path.with_suffix(".offline"))
    (case.root / "models").rename(case.root / "models.offline")
    script = """
import sys
import duckdb
import ibis
import marivo.analysis as mv
import marivo.semantic as ms
from marivo.datasource.runtime import DatasourceConnectionService

def forbidden(*args, **kwargs):
    raise AssertionError('cold fixed continuation touched source, models or DuckDB')
ms.load = forbidden
duckdb.connect = forbidden
ibis.duckdb.connect = forbidden
DatasourceConnectionService.use_backend = forbidden
fixed = mv.session.resume(sys.argv[1], by='id').artifact(sys.argv[2])
assert isinstance(fixed, mv.MaterializedStatisticRelation)
assert any(a.call == 'relation.rollup()' for a in fixed.contract().actions)
assert fixed.rollup().execute().to_pandas().iloc[0]['value'] == 49
"""
    completed = subprocess.run(
        [sys.executable, "-c", script, case.session.id, fixed.state.artifact_ref.ref],
        cwd=case.root,
        env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT)},
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


@pytest.mark.runtime
def test_no_key_grouping_is_singleton(retained_coordinates_case):
    case = retained_coordinates_case
    result = (
        case.session.members(ms.ref.entity("sales.customer"))
        .observe(
            ms.ref.metric("sales.revenue"),
            during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
            via=ms.ref.relationship("sales.order_buyer"),
            by=(),
        )
        .rollup()
        .execute()
    )
    assert result.to_pandas()["value"].tolist() == [147]


@pytest.mark.runtime
@pytest.mark.parametrize("fixed", [False, True])
def test_category_count_uses_current_rows(retained_coordinates_case, fixed):
    case = retained_coordinates_case
    category = case.session.members(ms.ref.entity("sales.customer")).read(
        ms.ref.dimension("sales.customer.region")
    )
    current = category.execute() if fixed else category
    result = current.summarize(mv.count()).execute()
    assert result.to_pandas()["value"].tolist() == [3]
    assert result.rollup().execute().to_pandas()["value"].tolist() == [3]


@pytest.mark.runtime
@pytest.mark.parametrize("expression_kind", ["ratio", "linear"])
def test_full_tuple_union_retains_denominator_only_and_cancelled_coordinates(
    analysis_dsl_case_factory: DslCaseFactory, expression_kind: str
):
    case = analysis_dsl_case_factory("j1")
    channel = ms.ref.dimension("sales.order.channel")
    status = ms.ref.dimension("sales.order.status")
    count = ms.ref.metric("sales.order_count")
    web = mv.runtime_metric.slice(count, by={channel: "web"}, label="web")
    expression = (
        mv.runtime_metric.ratio(web, count, label="share")
        if expression_kind == "ratio"
        else mv.runtime_metric.linear(add=[count], subtract=[count], label="cancelled")
    )
    observed = case.session.members(ms.ref.entity("sales.customer")).observe(
        expression,
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=ms.ref.relationship("sales.order_buyer"),
        by=(
            mv.member(),
            channel,
            status,
        ),
    )
    for current in (observed, observed.execute()):
        result = current.group_by(channel, status).rollup().execute()
        rows = result.to_pandas()
        assert len(rows) == 2
        assert rows["value"].tolist() == ([0, 1] if expression_kind == "ratio" else [0, 0])
        assert rows["cell_tag"].tolist() == ["defined", "defined"]
        total = result.rollup().execute()
        assert total.to_pandas()["value"].tolist() == pytest.approx(
            [2 / 3 if expression_kind == "ratio" else 0]
        )


@pytest.mark.runtime
@pytest.mark.parametrize("violation", ["missing", "corrupt"])
def test_required_row_state_damage_revokes_continuation(retained_coordinates_case, violation):
    from marivo.analysis.errors import AnalysisError
    from marivo.analysis.materialization import graph_store

    case = retained_coordinates_case
    statistic = (
        case.session.members(ms.ref.entity("sales.customer"))
        .read(ms.ref.dimension("sales.customer.region"))
        .summarize(mv.count())
        .execute()
    )
    store = case.session._runtime.store
    with store._read() as connection:
        record = graph_store.artifact(store, connection, statistic.state.artifact_ref.ref)
    assert record is not None
    part = next(p for p in record.descriptor.parts if p.role == "row_state")
    path = case.root / part.local.project_relative_path / part.local.file_manifest[0].relative_path
    original = path.read_bytes()
    if violation == "missing":
        path.unlink()
    else:
        path.write_bytes(b"broken retained numerical state")
    try:
        with pytest.raises(AnalysisError):
            statistic.contract()
        with pytest.raises(AnalysisError):
            statistic.rollup().execute()
        with pytest.raises(AnalysisError):
            case.session.artifact(statistic.state.artifact_ref)
    finally:
        path.write_bytes(original)


@pytest.mark.runtime
@pytest.mark.parametrize("fixed", [False, True])
def test_consumed_classification_must_cover_every_complete_key(retained_coordinates_case, fixed):
    from marivo.analysis.errors import AnalysisError

    case = retained_coordinates_case
    members = case.session.members(ms.ref.entity("sales.customer"))
    region = members.read(ms.ref.dimension("sales.customer.region"))
    east = (
        region.where(region.value.eq("east"))
        .members()
        .read(ms.ref.dimension("sales.customer.region"))
    )
    observed = members.observe(
        ms.ref.metric("sales.revenue"),
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=ms.ref.relationship("sales.order_buyer"),
        by=(mv.member(),),
    )
    current = observed.execute() if fixed else observed
    category = east.execute() if fixed else east
    with pytest.raises(AnalysisError):
        current.group_by(category).rollup().execute()


@pytest.mark.runtime
@pytest.mark.parametrize("parquet", [False, True])
def test_weighted_coordinates_merge_paired_components(
    analysis_dsl_case_factory: DslCaseFactory, parquet
):
    case = analysis_dsl_case_factory("j1")
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute('ALTER TABLE "order" ADD COLUMN weight BIGINT DEFAULT 1')
    model = case.root / "models/semantic/sales/models.py"
    model.write_text(
        model.read_text().replace("granularity='second',", "granularity='second', is_default=True,")
        + "\nweight = ms.measure_column(name='weight', entity=orders, column='weight', additivity=ms.additive_all())\nweighted = ms.weighted_mean(name='weighted', value=amount, weight=weight)\n"
    )
    if parquet:
        export_dsl_parquet_models(case, case.root)
    ms.load(workspace_dir=case.root)
    channel = ms.ref.dimension("sales.order.channel")
    observed = case.session.members(ms.ref.entity("sales.customer")).observe(
        ms.ref.metric("sales.weighted"),
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=ms.ref.relationship("sales.order_buyer"),
        by=(
            mv.member(),
            channel,
        ),
    )
    for current in (observed, observed.execute()):
        partial = current.group_by(channel).rollup().execute()
        assert partial.to_pandas()["value"].tolist() == [150, 425]
        total = partial.rollup().execute()
        assert total.to_pandas()["value"].tolist() == pytest.approx([1000 / 3])
        assert next(
            p.table.to_pylist()
            for p in total._dataset.verified().parts
            if p.role == "original_state"
        ) == [
            {
                "original_state__weighted_numerator": 1000,
                "original_state__weight_sum": 3,
                "original_state__non_null_pair_count": 3,
                "original_state__row_count": 3,
            }
        ]


@pytest.mark.runtime
def test_combined_member_classifications_do_not_expand_facts(
    analysis_dsl_case_factory: DslCaseFactory,
):
    case = analysis_dsl_case_factory("j1")
    model = case.root / "models/semantic/sales/models.py"
    model.write_text(
        model.read_text()
        + "\ncohort = ms.dimension_column(name='cohort', entity=customer, column='customer_id')\n"
    )
    ms.load(workspace_dir=case.root)
    members = case.session.members(ms.ref.entity("sales.customer"))
    region = ms.ref.dimension("sales.customer.region")
    cohort = ms.ref.dimension("sales.customer.cohort")
    grouped = members.observe(
        ms.ref.metric("sales.revenue"),
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=ms.ref.relationship("sales.order_buyer"),
        by=(
            region,
            cohort,
        ),
    )
    fixed = grouped.execute()
    assert len(fixed.to_pandas()) == 4
    assert fixed.to_pandas()["value"].dropna().tolist() == [450, 150, 400]
    assert fixed.group_by(region).rollup().execute().rollup().execute().to_pandas()[
        "value"
    ].tolist() == [1000]


@pytest.mark.runtime
def test_numeric_read_grouping_creates_only_row_statistics(retained_coordinates_case):
    case = retained_coordinates_case
    members = case.session.members(ms.ref.entity("sales.order"))
    amount = members.read(ms.ref.measure("sales.order.amount"))
    channel = members.read(ms.ref.dimension("sales.order.channel"))
    for values, categories in ((amount, channel), (amount.execute(), channel.execute())):
        grouped = values.group_by(categories)
        assert [a.call for a in grouped.contract().actions] == ["relation.summarize(method)"]
        result = grouped.summarize(mv.mean()).execute()
        assert result.to_pandas()["value"].tolist() == [13.5, 60]
        assert result.rollup().execute().to_pandas()["value"].tolist() == [36.75]


@pytest.mark.runtime
@pytest.mark.parametrize("violation", ["null", "outside", "duplicate"])
@pytest.mark.parametrize("fixed", [False, True])
def test_invalid_classification_rejects(retained_coordinates_case, violation, fixed):
    from marivo.analysis.errors import AnalysisError

    case = retained_coordinates_case
    if violation == "null":
        with duckdb.connect(str(case.database_path)) as connection:
            connection.execute("UPDATE customer SET region = NULL WHERE customer_id = 'C'")
    members = case.session.members(ms.ref.entity("sales.customer"))
    region = members.read(ms.ref.dimension("sales.customer.region"))
    observed = members.observe(
        ms.ref.metric("sales.revenue"),
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=ms.ref.relationship("sales.order_buyer"),
        by=(mv.member(),),
    )
    if violation == "outside":
        region = region.where(region.value.eq("east"))
    values = observed.execute() if fixed else observed
    category = region.execute() if fixed else region
    with pytest.raises(AnalysisError):
        values.group_by(
            category, *([category] if violation == "duplicate" else [])
        ).rollup().execute()


@pytest.mark.runtime
def test_row_state_version_and_binding_mismatch_reject(retained_coordinates_case):
    from dataclasses import replace

    from marivo.analysis.core.model import RowStatePart
    from marivo.analysis.errors import AnalysisError
    from marivo.analysis.materialization.graph_exchange import from_arrow

    case = retained_coordinates_case
    result = (
        case.session.members(ms.ref.entity("sales.customer"))
        .read(ms.ref.dimension("sales.customer.region"))
        .summarize(mv.count())
        .execute()
    )
    exchange = result._dataset.verified()
    parts = tuple(
        replace(p, version="v2") if isinstance(p, RowStatePart) else p
        for p in exchange.contract.signature.parts
    )
    with pytest.raises(AnalysisError):
        from_arrow(
            exchange.primary,
            replace(exchange.contract, signature=replace(exchange.contract.signature, parts=parts)),
            parts=exchange.parts,
            method_state=exchange.method_state,
            completed_checks=exchange.completed_checks,
        )
    parts = tuple(
        replace(p, input_domain_id="different-current-rows") if isinstance(p, RowStatePart) else p
        for p in result._node.root.signature.parts
    )
    root = replace(result._node.root, signature=replace(result._node.root.signature, parts=parts))
    with pytest.raises(AnalysisError):
        replace(result._node, root=root).rollup_statistic()


@pytest.mark.runtime
@pytest.mark.parametrize("fixed", [False, True])
def test_typed_integer_classification_and_singleton(retained_coordinates_case, fixed):
    case = retained_coordinates_case
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute("ALTER TABLE customer ADD COLUMN band BIGINT DEFAULT 2")
        connection.execute("UPDATE customer SET band = 1 WHERE customer_id = 'A'")
    model = case.root / "models/semantic/sales/models.py"
    model.write_text(
        model.read_text()
        + "\nband = ms.dimension_column(name='band', entity=customer, column='band')\n"
    )
    ms.load(workspace_dir=case.root)
    members = case.session.members(ms.ref.entity("sales.customer"))
    band = members.read(ms.ref.dimension("sales.customer.band"))
    values = members.observe(
        ms.ref.metric("sales.revenue"),
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=ms.ref.relationship("sales.order_buyer"),
        by=(mv.member(),),
    )
    current = values.execute() if fixed else values
    category = band.execute() if fixed else band
    assert current.group_by(category).rollup().execute().to_pandas().set_index("group")[
        "value"
    ].to_dict() == {1: 120, 2: 27}
    assert current.group_by().rollup().execute().to_pandas()["value"].tolist() == [147]


@pytest.mark.runtime
@pytest.mark.parametrize("fixed", [False, True])
def test_selected_entities_keep_complete_subject_mapping(
    analysis_dsl_case_factory: DslCaseFactory, fixed
):
    case = analysis_dsl_case_factory("j1")
    customer = ms.ref.entity("sales.customer")
    members = case.session.members(customer)
    regions = members.read(ms.ref.dimension("sales.customer.region"))
    east = regions.where(regions.value.eq("east")).members()
    observed = east.observe(
        ms.ref.metric("sales.revenue"),
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=ms.ref.relationship("sales.order_buyer"),
        by=(mv.member(),),
    )
    current = observed.execute() if fixed else observed
    result = current.group_by(customer).rollup().execute()
    subject = next(
        p.table.to_pylist() for p in result._dataset.verified().parts if p.role == "subject"
    )
    assert subject == [{"key_0": key, "subject__key_0": key} for key in ("A", "B")]
    assert result.to_pandas()["cell_tag"].tolist() == ["defined", "defined"]
    assert result.rollup().execute().to_pandas()["value"].tolist() == [600]


@pytest.mark.runtime
@pytest.mark.parametrize("fixed", [False, True])
@pytest.mark.parametrize("parquet", [False, True])
def test_review_selected_category_retains_classification(
    analysis_dsl_case_factory: DslCaseFactory, fixed: bool, parquet: bool
) -> None:
    case = analysis_dsl_case_factory("j1")
    if parquet:
        export_dsl_parquet_models(case, case.root)
        ms.load(workspace_dir=case.root)
    members = case.session.members(ms.ref.entity("sales.customer"))
    category = members.read(ms.ref.dimension("sales.customer.region"))
    selected = category.where(category.value.eq("east"))
    assert type(selected) is mv.LogicalCategoryRelation
    current = selected.execute() if fixed else selected
    assert type(current) is (
        mv.MaterializedCategoryRelation if fixed else mv.LogicalCategoryRelation
    )
    statistic = current.summarize(mv.count()).execute()
    assert statistic.to_pandas()["value"].tolist() == [2]
    assert current._node.classification_coordinate().field == "sales.customer.region"
    assert statistic.rollup().execute().to_pandas()["value"].tolist() == [2]
    state = next(
        p.table.to_pylist() for p in statistic._dataset.verified().parts if p.role == "row_state"
    )
    assert state == [
        {"row_state__count": 2},
    ]


@pytest.mark.runtime
@pytest.mark.parametrize("parquet", [False, True])
def test_review_foreign_fact_predicate_transports_source_bindings(
    analysis_dsl_case_factory: DslCaseFactory, parquet: bool
) -> None:
    case = analysis_dsl_case_factory("j3")
    if parquet:
        export_dsl_parquet_models(case, case.root)
        ms.load(workspace_dir=case.root)
    members = case.session.members(ms.ref.entity("sales.customer"))
    window = mv.time_scope(start="2026-08-01", end="2026-09-01")
    revenue = members.observe(
        ms.ref.metric("sales.revenue"),
        during=window,
        via=ms.ref.relationship("sales.order_buyer"),
        by=(mv.member(),),
    )
    lines = members.observe(
        ms.ref.metric("sales.line_revenue"),
        during=window,
        via=(
            mv.path(
                ms.ref.relationship("sales.line_order"), ms.ref.relationship("sales.order_buyer")
            ),
        ),
        by=(mv.member(),),
    )
    assert isinstance(revenue, mv.LogicalNumericRelation)
    assert isinstance(lines, mv.LogicalNumericRelation)
    direct = revenue.where(lines.value.gt(80)).execute()
    fixed_revenue, fixed_lines = revenue.execute(), lines.execute()
    local = fixed_revenue.where(fixed_lines.value.gt(80)).execute()
    expected = [{"member": "A", "value": 0, "cell_tag": "defined", "cell_reason": None}]
    assert direct.to_pandas().to_dict("records") == expected
    assert local.to_pandas().to_dict("records") == expected
    assert direct._node.root.signature.quantity == local._node.root.signature.quantity
    assert direct.rollup().execute().to_pandas()["value"].tolist() == [0]
    assert local.rollup().execute().to_pandas()["value"].tolist() == [0]


@pytest.mark.runtime
def test_review_cold_selected_category_count(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    import os
    import subprocess
    import sys

    case = analysis_dsl_case_factory("j1")
    members = case.session.members(ms.ref.entity("sales.customer"))
    category = members.read(ms.ref.dimension("sales.customer.region"))
    # Use the same exact field binding for selection, then persist its classification identity.
    fixed_category = category.execute()
    selected = fixed_category.where(fixed_category.value.eq("east"))
    fixed_selected = selected.execute()
    case.database_path.rename(case.database_path.with_suffix(".offline"))
    (case.root / "models").rename(case.root / "models.offline")
    script = """
import sys
import duckdb
import ibis
import marivo.analysis as mv
import marivo.semantic as ms
from marivo.datasource.runtime import DatasourceConnectionService

def forbidden(*args, **kwargs):
    raise AssertionError('cold fixed continuation touched source, models or DuckDB')
ms.load = forbidden
duckdb.connect = forbidden
ibis.duckdb.connect = forbidden
DatasourceConnectionService.use_backend = forbidden
session = mv.session.resume(sys.argv[1], by='id')
selected = session.artifact(sys.argv[2])
assert isinstance(selected, mv.MaterializedCategoryRelation)
chained = selected.where(selected.value.eq('east'))
assert type(chained) is mv.LogicalCategoryRelation
selected = chained.execute()
assert type(selected) is mv.MaterializedCategoryRelation
assert selected.members().execute().to_pandas()['member'].tolist() == ['A', 'B']
assert not hasattr(selected, 'group_by')
assert any(a.call == 'relation.summarize(method)' for a in selected.contract().actions)
assert selected._node.classification_coordinate().field == 'sales.customer.region'
result = selected.summarize(mv.count()).execute()
assert result.to_pandas()['value'].tolist() == [2]
assert result.rollup().execute().to_pandas()['value'].tolist() == [2]
"""
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            case.session.id,
            fixed_selected.state.artifact_ref.ref,
        ],
        cwd=case.root,
        env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT)},
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


@pytest.mark.runtime
@pytest.mark.parametrize(
    "method,logical_expected,fixed_expected",
    [
        (mv.mean(), [70, 7], [140, 7]),
        (mv.sum(), [140, 7], [140, 7]),
        (mv.min(), [20, 7], [140, 7]),
        (mv.max(), [120, 7], [140, 7]),
        (mv.count(), [2, 1], [1, 1]),
        (mv.count_defined(), [2, 1], [1, 1]),
    ],
)
def test_review_fixed_group_statistics_keep_axes_and_current_rows(
    retained_coordinates_case,
    method: mv.RowMethod,
    logical_expected: list[int],
    fixed_expected: list[int],
) -> None:
    case = retained_coordinates_case
    members = case.session.members(ms.ref.entity("sales.customer"))
    category = members.read(ms.ref.dimension("sales.customer.region"))
    observed = members.observe(
        ms.ref.metric("sales.revenue"),
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=ms.ref.relationship("sales.order_buyer"),
        by=(mv.member(),),
    )
    assert isinstance(observed, mv.LogicalNumericRelation)
    grouped = observed.group_by(category)
    logical = grouped.summarize(method).execute()
    assert logical.to_pandas()["value"].tolist() == logical_expected
    fixed = grouped.execute()
    assert fixed.to_pandas()["value"].tolist() == [140, 7]
    restored = case.session.artifact(fixed.state.artifact_ref)
    assert isinstance(restored, mv.MaterializedGroupedNumericRelation)
    for current in (fixed, restored):
        statistic = current.summarize(method).execute()
        frame = statistic.to_pandas()
        assert frame["group"].tolist() == ["east", "south"]
        assert frame["value"].tolist() == fixed_expected
        assert (
            statistic._node.root.signature.domain.instance_key
            == fixed._node.root.signature.domain.instance_key
        )
        assert statistic._node.root.signature.quantity != fixed._node.root.signature.quantity
        assert any(a.call == "relation.rollup()" for a in statistic.contract().actions)
    assert fixed.rollup().execute().to_pandas()["value"].tolist() == [147]


@pytest.mark.runtime
@pytest.mark.parametrize("empty", [False, True])
@pytest.mark.parametrize("method", [mv.count(), mv.mean()])
def test_fixed_grouped_statistic_preserves_cell_binding_and_empty_schema(
    analysis_dsl_case_factory: DslCaseFactory, empty: bool, method: mv.RowMethod
) -> None:
    case = analysis_dsl_case_factory("j1")
    customer = ms.ref.entity("sales.customer")
    region = ms.ref.dimension("sales.customer.region")
    fixed = (
        case.session.members(customer)
        .observe(
            ms.ref.metric("sales.revenue"),
            during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
            via=ms.ref.relationship("sales.order_buyer"),
            by=(mv.member(), region),
        )
        .execute()
    )
    if empty or method.kind == "mean":
        fixed = fixed.where(fixed.value.is_defined()).execute()
    if empty:
        fixed = fixed.where(fixed.value.gt(10000)).execute()
    result = fixed.group_by(region).summarize(method).execute()
    frame = result.to_pandas()
    assert list(frame.columns) == ["group", "value", "cell_tag", "cell_reason"]
    expected = (
        {}
        if empty
        else {"east": 300, "south": 400}
        if method.kind == "mean"
        else {"east": 2, "south": 1, "west": 1}
    )
    assert frame.set_index("group").value.to_dict() == expected
    assert set(frame.cell_tag) == (set() if empty else {"defined"})
    assert frame.cell_reason.isna().all()
    restored = case.session.artifact(result.state.artifact_ref)
    assert restored.to_pandas().equals(frame)
