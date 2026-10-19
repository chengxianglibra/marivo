"""Complete coordinates, current-row statistics and explicit source Metrics."""

from __future__ import annotations

from dataclasses import replace

import duckdb
import pandas as pd
import pytest
from ibis.common.deferred import Deferred

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.core.artifact_data import DataSchema
from marivo.analysis.core.frame_data import StoredFrameBacking
from marivo.analysis.errors import AnalysisError
from tests.analysis.statistics.pandas_workflows import receipt_paths
from tests.shared_fixtures import DslCase, DslCaseFactory, export_dsl_parquet_models
from tests.support.paths import PROJECT_ROOT


def _members(case: DslCase) -> mv.Population:
    return case.session.members(ms.ref.entity("sales.customer"))


def _observe(
    case: DslCase,
    *,
    classified: bool = False,
    grouped: bool = False,
    overall: bool = False,
    members: mv.Population | None = None,
) -> mv.AnalysisFrame:
    population = _members(case) if members is None else members
    region = case.session.catalog.fields.get("sales.customer.region")
    return case.session.observe(
        {"value": case.session.catalog.metrics.get("sales.revenue")},
        during=case.session.time_scope(start="2026-08-01", end="2026-09-01"),
        by=()
        if overall
        else population.classify(by=(region,))
        if grouped
        else (mv.member(), region)
        if classified
        else population,
        members=population,
    )


def _source(
    case: DslCase,
    metric: Deferred,
    *,
    by: tuple[Deferred, ...] = (),
    members: mv.Population | None = None,
) -> mv.AnalysisFrame:
    return case.session.observe(
        {"value": metric},
        during=case.session.time_scope(start="2026-08-01", end="2026-09-01"),
        by=by,
        members=members,
    )


def _selected_members(case: DslCase, frame: mv.AnalysisFrame) -> mv.Population:
    return case.session.population_from(
        frame, on=((frame.axes[0], case.session.catalog.entities.get("sales.customer")),)
    )


def _region(case: DslCase) -> mv.AnalysisFrame:
    return case.session.read(
        _members(case), {"region": case.session.catalog.fields.get("sales.customer.region")}
    )


def _dict(frame: pd.DataFrame, name: str = "value") -> dict[object, object]:
    return dict(zip(frame.iloc[:, 0], frame[("value", name)], strict=True))


def test_row_methods_source_and_fixed(analysis_dsl_case_factory: DslCaseFactory) -> None:
    case = analysis_dsl_case_factory("j1")
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute("DELETE FROM customer WHERE customer_id='D'")
    observed = _observe(case)
    fixed = observed.persist()
    frame = fixed.to_pandas()
    assert frame[("value", "value")].min() == 150
    assert frame[("value", "value")].max() == 450
    offline = case.database_path.with_suffix(".offline")
    case.database_path.rename(offline)
    try:
        values = case.session.persisted_frame(fixed.artifact.id).to_pandas()[("value", "value")]
        assert values.min() == 150 and values.max() == 450
    finally:
        offline.rename(case.database_path)


def test_complete_coordinate_tuple_rollup(analysis_dsl_case_factory: DslCaseFactory) -> None:
    case = analysis_dsl_case_factory("j1")
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute("UPDATE \"order\" SET status='refunded' WHERE channel='mobile'")
    session = case.session
    customer = session.catalog.fields.get("sales.order.customer_id")
    channel = session.catalog.fields.get("sales.order.channel")
    status = session.catalog.fields.get("sales.order.status")
    observed = session.observe(
        {"value": session.catalog.metrics.get("sales.revenue")},
        during=session.time_scope(start="2026-08-01", end="2026-09-01"),
        by=(customer, channel, status),
    )
    result = _source(
        case, session.catalog.metrics.get("sales.revenue"), by=(channel, status)
    ).persist()
    frame = result.to_pandas()
    assert frame[("value", "value")].tolist() == [150, 850]
    assert len(frame) == 2
    assert case.session.persisted_frame(result.artifact.id).to_pandas().equals(frame)


def test_empty_numeric_row_state(analysis_dsl_case_factory: DslCaseFactory) -> None:
    case = analysis_dsl_case_factory("j1")
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute('DELETE FROM "order"')
        connection.execute("DELETE FROM customer")
    observed = _observe(case)
    frame = observed.compute().to_pandas()
    assert (
        frame.empty
        and pd.isna(frame[("value", "value")].min())
        and pd.isna(frame[("value", "value")].max())
    )
    result = observed.reduce(
        {"sum": observed["value"].sum(), "mean": observed["value"].mean(), "count": mv.row_count()}
    ).persist()
    row = result.to_pandas().iloc[0]
    assert pd.isna(row[("value", "sum")]) and row[("value", "count")] == 0
    assert pd.isna(row[("value", "mean")])
    assert case.session.persisted_frame(result.artifact.id).to_pandas().equals(result.to_pandas())


def test_explicit_classification_rollup_and_current_rows(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    observed = _observe(case, classified=True)
    region = case.session.catalog.fields.get("sales.customer.region")
    totals = _observe(case, grouped=True).compute().to_pandas()
    assert _dict(totals) == {"east": 600, "south": 400, "west": 0}
    counts = (
        observed.reduce({"count": mv.row_count()}, by=(observed.axes[1],)).compute().to_pandas()
    )
    assert _dict(counts, "count") == {"east": 2, "south": 1, "west": 1}


def test_selected_row_groups_preserve_count_mean_state(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    regions = _region(case)
    selected_values = regions.where(regions["region"] == "east")
    selected = _selected_members(case, selected_values)
    observed = _observe(case, classified=True, members=selected)
    region = case.session.catalog.fields.get("sales.customer.region")
    stats = observed.reduce(
        {"count": mv.row_count(), "sum": observed["value"].sum(), "mean": observed["value"].mean()},
        by=(observed.axes[1],),
    )
    result = stats.persist()
    frame = result.to_pandas()
    assert _dict(frame, "count") == {"east": 2}
    assert _dict(frame, "sum") == {"east": 600}
    assert _dict(frame, "mean") == {"east": 300}
    assert (frame[("value", "sum")].sum() / frame[("value", "count")].sum()) == 300
    assert case.session.persisted_frame(result.artifact.id).to_pandas().equals(frame)
    assert not hasattr(stats, "rollup")


def test_original_mean_merges_support_not_finished_values(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j3_weighting")
    model = case.root / "models/semantic/sales/models.py"
    model.write_text(
        model.read_text()
        + "\n@ms.metric()\ndef mean_amount():\n    return line_amount_value.mean(time=time_value.via((line_order_value,)))\n"
    )
    ms.load(workspace_dir=case.root)
    session = case.session
    observed = session.observe(
        {"value": session.catalog.metrics.get("sales.mean_amount")},
        during=session.time_scope(start="2026-08-01", end="2026-09-01"),
        by=_members(case),
    )
    fixed = observed.compute()
    assert fixed.to_pandas()[("value", "value")].tolist() == [1, 100]
    assert _source(
        case, session.catalog.metrics.get("sales.mean_amount"), members=_members(case)
    ).compute().to_pandas()[("value", "value")].tolist() == pytest.approx([200 / 101])
    assert fixed.to_pandas()[("value", "value")].mean() == 50.5


def test_partial_reduction_matches_components_and_preserves_keys(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    session = case.session
    customer = session.catalog.fields.get("sales.order.customer_id")
    channel = session.catalog.fields.get("sales.order.channel")
    status = session.catalog.fields.get("sales.order.status")
    observed = session.observe(
        {"value": session.catalog.metrics.get("sales.revenue")},
        during=session.time_scope(start="2026-08-01", end="2026-09-01"),
        by=(customer, channel, status),
    )
    partial = _source(case, session.catalog.metrics.get("sales.revenue"), by=(customer, channel))
    assert partial.compute().to_pandas()[("value", "value")].tolist() == [450, 150, 400]
    direct = _source(case, session.catalog.metrics.get("sales.revenue")).compute()
    assert direct.to_pandas()[("value", "value")].tolist() == [1000]
    assert direct.to_pandas().attrs["frozen_coordinates"] == ()


def test_row_statistics_grouped_merge_preserves_mean_state_and_identity(
    retained_coordinates_case: DslCase,
) -> None:
    case = retained_coordinates_case
    values = _observe(case, classified=True)
    region = case.session.catalog.fields.get("sales.customer.region")
    grouped = (
        values.reduce({"sum": values["value"].sum(), "count": mv.row_count()}, by=(values.axes[1],))
        .compute()
        .to_pandas()
    )
    assert _dict(grouped, "sum") == {"east": 140, "south": 7}
    assert _dict(grouped, "count") == {"east": 2, "south": 1}
    merged = grouped[("value", "sum")].sum() / grouped[("value", "count")].sum()
    assert merged == 49
    assert values.reduce({"mean": values["value"].mean()}).compute().to_pandas()[
        ("value", "mean")
    ].tolist() == [49]


@pytest.mark.slow
def test_cold_grouped_statistic_recovers_without_models_source_or_duckdb(
    retained_coordinates_case: DslCase,
) -> None:
    import os
    import subprocess
    import sys

    case = retained_coordinates_case
    observed = _observe(case, classified=True)
    region = case.session.catalog.fields.get("sales.customer.region")
    saved = observed.reduce(
        {"sum": observed["value"].sum(), "count": mv.row_count()}, by=(observed.axes[1],)
    ).persist()
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
    raise AssertionError('cold output read touched source, models or DuckDB')
ms.load = forbidden
duckdb.connect = forbidden
ibis.duckdb.connect = forbidden
DatasourceConnectionService.use_backend = forbidden
session = mv.session.resume(sys.argv[1], by='id')
before = len(session.runs().items)
frame = session.persisted_frame(sys.argv[2]).to_pandas()
assert frame[('value','sum')].sum() / frame[('value','count')].sum() == 49
assert len(session.runs().items) == before
"""
    completed = subprocess.run(
        [sys.executable, "-c", script, case.session.id, saved.artifact.id],
        cwd=case.root,
        env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT)},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_full_tuple_union_retains_denominator_only_and_cancelled_coordinates(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    session = case.session
    channel = session.catalog.fields.get("sales.order.channel")
    status = session.catalog.fields.get("sales.order.status")
    customer = session.catalog.fields.get("sales.order.customer_id")
    count = session.catalog.metrics.get("sales.order_count")
    web = count.where(channel == "web")
    for kind, expression in (("ratio", web / count), ("linear", count - count)):
        observed = session.observe(
            {"value": expression},
            during=session.time_scope(start="2026-08-01", end="2026-09-01"),
            by=(customer, channel, status),
        )
        grouped = _source(case, expression, by=(channel, status))
        saved = grouped.persist()
        assert saved.to_pandas()[("value", "value")].tolist() == (
            [0, 1] if kind == "ratio" else [0, 0]
        )
        assert _source(case, expression).compute().to_pandas()[
            ("value", "value")
        ].tolist() == pytest.approx([2 / 3] if kind == "ratio" else [0])
        assert session.persisted_frame(saved.artifact.id).to_pandas().equals(saved.to_pandas())


def test_required_terminal_data_damage_revokes_reads(retained_coordinates_case: DslCase) -> None:
    case = retained_coordinates_case
    regions = _region(case)
    statistic = regions.reduce({"count": mv.row_count()}).persist()
    assert statistic.to_pandas()[("value", "count")].tolist() == [3]
    path = receipt_paths(case.root, statistic)[0]
    original = path.read_bytes()
    for violation in ("missing", "corrupt"):
        if violation == "missing":
            path.unlink()
        else:
            path.write_bytes(b"broken committed numerical data")
        try:
            reopened = case.session.persisted_frame(statistic.artifact.id)
            for value in (statistic, reopened):
                with pytest.raises(AnalysisError):
                    value.to_pandas()
        finally:
            path.write_bytes(original)
    assert case.session.persisted_frame(statistic.artifact.id).to_pandas()[
        ("value", "count")
    ].tolist() == [3]


def test_weighted_coordinates_merge_paired_components(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute('ALTER TABLE "order" ADD COLUMN weight BIGINT DEFAULT 1')
    model = case.root / "models/semantic/sales/models.py"
    model.write_text(
        model.read_text().replace(
            "finalized = ms.finalize()",
            "weight = ms.measure_column(name='weight', entity=orders, column='weight', additivity=ms.additive_all())\nfinalized = ms.finalize()",
        )
        + "\nweight_value = finalized.resolve(weight)\n@ms.metric()\ndef weighted():\n    return amount_value.weighted_mean(weight=weight_value, time=time_value)\n"
    )
    ms.load(workspace_dir=case.root)
    session = case.session
    customer = session.catalog.fields.get("sales.order.customer_id")
    channel = session.catalog.fields.get("sales.order.channel")
    observed = session.observe(
        {"value": session.catalog.metrics.get("sales.weighted")},
        during=session.time_scope(start="2026-08-01", end="2026-09-01"),
        by=(customer, channel),
    )
    partial = _source(case, session.catalog.metrics.get("sales.weighted"), by=(channel,))
    assert partial.compute().to_pandas()[("value", "value")].tolist() == [150, 425]
    assert _source(case, session.catalog.metrics.get("sales.weighted")).compute().to_pandas()[
        ("value", "value")
    ].tolist() == pytest.approx([1000 / 3])


def test_combined_member_classifications_do_not_expand_facts(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    model = case.root / "models/semantic/sales/models.py"
    model.write_text(
        model.read_text().replace(
            "finalized = ms.finalize()",
            "cohort = ms.dimension_column(name='cohort', entity=customer, column='customer_id')\nfinalized = ms.finalize()",
        )
    )
    ms.load(workspace_dir=case.root)
    session = case.session
    region = session.catalog.fields.get("sales.customer.region")
    cohort = session.catalog.fields.get("sales.customer.cohort")
    population = _members(case).classify(by=(region, cohort))
    grouped = session.observe(
        {"value": session.catalog.metrics.get("sales.revenue")},
        during=session.time_scope(start="2026-08-01", end="2026-09-01"),
        by=population,
    )
    frame = grouped.compute().to_pandas()
    assert len(frame) == 4
    assert frame[("value", "value")].tolist() == [450, 150, 400, 0]
    assert _observe(case, overall=True).compute().to_pandas()[("value", "value")].tolist() == [1000]


def test_numeric_read_grouping_creates_only_row_statistics(
    retained_coordinates_case: DslCase,
) -> None:
    case = retained_coordinates_case
    session = case.session
    channel = session.catalog.fields.get("sales.order.channel")
    raw = session.read(
        session.members(ms.ref.entity("sales.order")),
        {"amount": session.catalog.fields.get("sales.order.amount"), "channel": channel},
    )
    # Field values are explicit output; pandas owns post-export classification.
    frame = raw.compute().to_pandas()
    stats = frame[("value", "amount")].groupby(frame[("value", "channel")]).mean()
    assert stats.to_dict() == {"app": 13.5, "web": 60.0}
    assert stats.mean() == 36.75
    assert not hasattr(raw, "rollup")


def test_invalid_classification_rejects(retained_coordinates_case: DslCase) -> None:
    case = retained_coordinates_case
    values = _observe(case)
    region = _region(case)
    east = region.where(region["region"] == "east")
    with pytest.raises(AnalysisError):
        values.align(east, on=tuple(zip(values.axes, east.axes, strict=True))).compute()
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute("UPDATE customer SET region=NULL WHERE customer_id='C'")
    # Source Null is a real None classification in the current Field-axis contract.
    field = case.session.catalog.fields.get("sales.customer.region")
    domain = _members(case).classify(by=(field,))
    grouped = (
        case.session.observe(
            {"value": case.session.catalog.metrics.get("sales.revenue")},
            during=case.session.time_scope(start="2026-08-01", end="2026-09-01"),
            by=domain,
        )
        .compute()
        .to_pandas()
    )
    assert grouped[("value", "value")].loc[grouped.iloc[:, 0] == "east"].tolist() == [140]
    assert grouped[("value", "value")].loc[grouped.iloc[:, 0].isna()].tolist() == [7]


def test_duplicate_classification_rejects_source_and_fixed(
    retained_coordinates_case: DslCase,
) -> None:
    case = retained_coordinates_case
    region = case.session.catalog.fields.get("sales.customer.region")
    values = _observe(case, classified=True)
    before = case.session.runs().items
    with pytest.raises(AnalysisError):
        _source(case, case.session.catalog.metrics.get("sales.revenue"), by=(region, region))
    with pytest.raises(AnalysisError):
        values.reduce({"count": mv.row_count()}, by=(values.axes[1], values.axes[1]))
    assert case.session.runs().items == before


def test_terminal_schema_version_and_binding_mismatch_reject(
    retained_coordinates_case: DslCase,
) -> None:
    case = retained_coordinates_case
    result = _observe(case).persist()
    frame = result.to_pandas()
    schema = result._schema
    assert isinstance(schema, DataSchema)
    changed = replace(schema, values=(replace(schema.values[0], slot_id="unrecorded-output-slot"),))
    backing = result._backing
    assert isinstance(backing, StoredFrameBacking)
    forged = mv.AnalysisFrame._from_stored(
        result._local,
        replace(backing.data, schema=changed),
        artifact_id=backing.artifact_id,
        metadata=backing.metadata,
    )
    with pytest.raises(AnalysisError):
        forged.to_pandas()
    with pytest.raises(AnalysisError):
        DataSchema(schema.axes, schema.values + schema.values)
    assert case.session.persisted_frame(result.artifact.id).to_pandas().equals(frame)


def test_typed_integer_classification_and_singleton(retained_coordinates_case: DslCase) -> None:
    case = retained_coordinates_case
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute("ALTER TABLE customer ADD COLUMN band BIGINT DEFAULT 2")
        connection.execute("UPDATE customer SET band=1 WHERE customer_id='A'")
    model = case.root / "models/semantic/sales/models.py"
    model.write_text(
        model.read_text().replace(
            "finalized = ms.finalize()",
            "band=ms.dimension_column(name='band', entity=customer, column='band')\nfinalized = ms.finalize()",
        )
    )
    ms.load(workspace_dir=case.root)
    session = case.session
    band = session.catalog.fields.get("sales.customer.band")
    grouped = session.observe(
        {"value": session.catalog.metrics.get("sales.revenue")},
        during=session.time_scope(start="2026-08-01", end="2026-09-01"),
        by=_members(case).classify(by=(band,)),
    )
    assert _dict(grouped.compute().to_pandas()) == {1: 120, 2: 27}
    assert _observe(case, overall=True).compute().to_pandas()[("value", "value")].tolist() == [147]


def test_selected_entities_keep_complete_subject_mapping(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    regions = _region(case)
    selected_values = regions.where(regions["region"] == "east")
    selected = _selected_members(case, selected_values)
    observed = _observe(case, members=selected)
    frame = observed.compute().to_pandas()
    assert _dict(frame) == {"A": 450, "B": 150}
    identities = case.session.frame(_selected_members(case, observed)).compute().to_pandas()
    assert identities.iloc[:, 0].tolist() == ["A", "B"]
    assert _observe(case, overall=True, members=selected).compute().to_pandas()[
        ("value", "value")
    ].tolist() == [600]


def test_review_selected_category_retains_classification(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    category = _region(case)
    selected = category.where(category["region"] == "east")
    current = selected.persist()
    assert _dict(current.to_pandas(), "region") == {"A": "east", "B": "east"}
    statistic = selected.reduce({"count": mv.row_count()}).compute()
    assert statistic.to_pandas()[("value", "count")].tolist() == [2]
    assert case.session.persisted_frame(current.artifact.id).to_pandas().equals(current.to_pandas())


@pytest.mark.parametrize("parquet", [False])
def test_review_foreign_fact_predicate_transports_source_bindings(
    analysis_dsl_case_factory: DslCaseFactory, parquet: bool
) -> None:
    case = analysis_dsl_case_factory("j3")
    if parquet:
        export_dsl_parquet_models(case, case.root)
        ms.load(workspace_dir=case.root)
    session = case.session
    quantities = session.observe(
        {
            "value": session.catalog.metrics.get("sales.revenue"),
            "lines": session.catalog.metrics.get("sales.line_revenue"),
        },
        during=session.time_scope(start="2026-08-01", end="2026-09-01"),
        by=_members(case),
    )
    selected = quantities.where(quantities["lines"] > 80)
    selected = selected.select({"value": selected["value"]})
    result = selected.persist()
    assert _dict(result.to_pandas()) == {"A": 0}
    assert _observe(
        case, overall=True, members=_selected_members(case, selected)
    ).compute().to_pandas()[("value", "value")].tolist() == [0]
    assert session.persisted_frame(result.artifact.id).to_pandas().equals(result.to_pandas())


def test_review_fixed_group_statistics_keep_axes_and_current_rows(
    retained_coordinates_case: DslCase,
) -> None:
    case = retained_coordinates_case
    session = case.session
    region = session.catalog.fields.get("sales.customer.region")
    grouped = _observe(case, grouped=True)
    fixed = grouped.compute()
    assert _dict(fixed.to_pandas()) == {"east": 140, "south": 7}
    statistic = grouped.reduce({"mean": grouped["value"].mean()}, by=(grouped.axes[0],)).persist()
    frame = statistic.to_pandas()
    assert _dict(frame, "mean") == {"east": 140, "south": 7}
    assert frame.columns[0] == fixed.to_pandas().columns[0]
    assert session.persisted_frame(statistic.artifact.id).to_pandas().equals(frame)
    assert _observe(case, overall=True).compute().to_pandas()[("value", "value")].tolist() == [147]


def test_fixed_grouped_statistic_preserves_cell_binding_and_empty_schema(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    observed = _observe(case, classified=True)
    region = case.session.catalog.fields.get("sales.customer.region")
    for empty, method in ((False, "count"), (False, "mean"), (True, "mean")):
        current = observed
        if method == "mean":
            current = current.where(current["value"] > 0)
        if empty:
            current = current.where(current["value"] > 10000)
        request = mv.row_count() if method == "count" else current["value"].mean()
        result = current.reduce({"value": request}, by=(current.axes[1],)).persist()
        frame = result.to_pandas()
        expected = (
            {}
            if empty
            else {"east": 300, "south": 400}
            if method == "mean"
            else {"east": 2, "south": 1, "west": 1}
        )
        assert _dict(frame) == expected
        assert len(frame.columns) == 2
        assert case.session.persisted_frame(result.artifact.id).to_pandas().equals(frame)
