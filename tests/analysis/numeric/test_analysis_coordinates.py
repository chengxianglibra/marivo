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
from tests.shared_fixtures import (
    DslCase,
    DslCaseFactory,
    catalog_value,
    export_dsl_parquet_models,
    terminal_runs,
)
from tests.support.paths import PROJECT_ROOT


def _members(case: DslCase) -> mv.Population:
    return case.session.members(case.session.catalog.get("entity:sales.customer"))


def _observe(
    case: DslCase,
    *,
    classified: bool = False,
    grouped: bool = False,
    overall: bool = False,
    members: mv.Population | None = None,
) -> mv.AnalysisFrame:
    population = _members(case) if members is None else members
    region = catalog_value(case.session.catalog, "sales.customer.region", kind="field")
    scope = case.session.time_scope(start="2026-08-01", end="2026-09-01")
    domain = (
        (scope,)
        if overall
        else mv.product(scope, population.classify(by=(region,)))
        if grouped
        else mv.product(scope, population.classify(by=(population.axes[0], region)))
        if classified
        else mv.product(scope, population)
    )
    return case.session.observe(
        {"value": case.session.catalog.get("metric:sales.revenue")},
        by=domain,
        where=mv.in_population(population),
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
        by=(case.session.time_scope(start="2026-08-01", end="2026-09-01"), *by),
        where=mv.in_population(members) if members is not None else None,
    )


def _selected_members(case: DslCase, frame: mv.AnalysisFrame) -> mv.Population:
    return case.session.population_from(
        frame,
        on=(
            (
                next(axis for axis in frame.axes if axis.role == "identity"),
                case.session.catalog.get("entity:sales.customer"),
            ),
        ),
    )


def _region(case: DslCase) -> mv.AnalysisFrame:
    return case.session.read(
        _members(case),
        {"region": catalog_value(case.session.catalog, "sales.customer.region", kind="field")},
    )


def _dict(frame: pd.DataFrame, name: str = "value") -> dict[object, object]:
    coordinates = [axis for axis in frame.attrs["axes"] if axis["role"] != "interval"]
    assert len(coordinates) == 1
    return dict(zip(frame[coordinates[0]["name"].rsplit(".", 1)[-1]], frame[name], strict=True))


def test_row_methods_source_and_fixed(analysis_dsl_case_factory: DslCaseFactory) -> None:
    case = analysis_dsl_case_factory("j1")
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute("DELETE FROM customer WHERE customer_id='D'")
    observed = _observe(case)
    fixed = observed.persist()
    frame = fixed.to_pandas()
    assert frame["value"].min() == 150
    assert frame["value"].max() == 450
    offline = case.database_path.with_suffix(".offline")
    case.database_path.rename(offline)
    try:
        values = case.session.persisted_frame(fixed.artifact_id).to_pandas()["value"]
        assert values.min() == 150 and values.max() == 450
    finally:
        offline.rename(case.database_path)


def test_complete_coordinate_tuple_rollup(analysis_dsl_case_factory: DslCaseFactory) -> None:
    case = analysis_dsl_case_factory("j1")
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute("UPDATE \"order\" SET status='refunded' WHERE channel='mobile'")
    session = case.session
    customer = catalog_value(session.catalog, "sales.order.customer_id", kind="field")
    channel = catalog_value(session.catalog, "sales.order.channel", kind="field")
    status = catalog_value(session.catalog, "sales.order.status", kind="field")
    observed = session.observe(
        {"value": session.catalog.get("metric:sales.revenue")},
        by=(session.time_scope(start="2026-08-01", end="2026-09-01"), customer, channel, status),
    )
    result = _source(
        case, session.catalog.get("metric:sales.revenue"), by=(channel, status)
    ).persist()
    frame = result.to_pandas()
    assert frame["value"].tolist() == [150, 850]
    assert len(frame) == 2
    assert case.session.persisted_frame(result.artifact_id).to_pandas().equals(frame)


def test_empty_numeric_row_state(analysis_dsl_case_factory: DslCaseFactory) -> None:
    case = analysis_dsl_case_factory("j1")
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute('DELETE FROM "order"')
        connection.execute("DELETE FROM customer")
    observed = _observe(case)
    frame = observed.compute().to_pandas()
    assert frame.empty and pd.isna(frame["value"].min()) and pd.isna(frame["value"].max())
    result = observed.reduce(
        {"sum": observed["value"].sum(), "mean": observed["value"].mean(), "count": mv.row_count()}
    ).persist()
    row = result.to_pandas().iloc[0]
    assert row["sum"] == 0 and row["count"] == 0
    assert pd.isna(row["mean"])
    assert case.session.persisted_frame(result.artifact_id).to_pandas().equals(result.to_pandas())


def test_explicit_classification_rollup_and_current_rows(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    observed = _observe(case, classified=True)
    summary = (
        observed.reduce(
            {"sum": observed["value"].sum(), "count": mv.row_count()},
            by=(observed.axes[2],),
        )
        .compute()
        .to_pandas()
    )
    assert _dict(summary, "sum") == {"east": 600, "south": 400, "west": 0}
    assert _dict(summary, "count") == {"east": 2, "south": 1, "west": 1}


def test_selected_row_groups_preserve_count_mean_state(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    regions = _region(case)
    selected_values = regions.where(regions["region"] == "east")
    selected = _selected_members(case, selected_values)
    observed = _observe(case, classified=True, members=selected)
    region = catalog_value(case.session.catalog, "sales.customer.region", kind="field")
    stats = observed.reduce(
        {"count": mv.row_count(), "sum": observed["value"].sum(), "mean": observed["value"].mean()},
        by=(observed.axes[2],),
    )
    result = stats.persist()
    frame = result.to_pandas()
    assert _dict(frame, "count") == {"east": 2}
    assert _dict(frame, "sum") == {"east": 600}
    assert _dict(frame, "mean") == {"east": 300}
    assert (frame["sum"].sum() / frame["count"].sum()) == 300
    assert case.session.persisted_frame(result.artifact_id).to_pandas().equals(frame)
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
        {"value": session.catalog.get("metric:sales.mean_amount")},
        by=mv.product(session.time_scope(start="2026-08-01", end="2026-09-01"), _members(case)),
    )
    fixed = observed.compute()
    assert fixed.to_pandas()["value"].tolist() == [1, 100]
    assert _source(
        case, session.catalog.get("metric:sales.mean_amount"), members=_members(case)
    ).compute().to_pandas()["value"].tolist() == pytest.approx([200 / 101])
    assert fixed.to_pandas()["value"].mean() == 50.5


def test_partial_reduction_matches_components_and_preserves_keys(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    session = case.session
    customer = catalog_value(session.catalog, "sales.order.customer_id", kind="field")
    channel = catalog_value(session.catalog, "sales.order.channel", kind="field")
    status = catalog_value(session.catalog, "sales.order.status", kind="field")
    observed = session.observe(
        {"value": session.catalog.get("metric:sales.revenue")},
        by=(session.time_scope(start="2026-08-01", end="2026-09-01"), customer, channel, status),
    )
    partial = _source(case, session.catalog.get("metric:sales.revenue"), by=(customer, channel))
    assert partial.compute().to_pandas()["value"].tolist() == [450, 150, 400]
    direct = _source(case, session.catalog.get("metric:sales.revenue")).compute()
    assert direct.to_pandas()["value"].tolist() == [1000]
    assert len(direct.to_pandas().attrs["frozen_coordinates"]) == 1
    assert direct.axes[0].role == "interval"


def test_row_statistics_grouped_merge_preserves_mean_state_and_identity(
    retained_coordinates_case: DslCase,
) -> None:
    case = retained_coordinates_case
    values = _observe(case, classified=True)
    region = catalog_value(case.session.catalog, "sales.customer.region", kind="field")
    grouped = (
        values.reduce({"sum": values["value"].sum(), "count": mv.row_count()}, by=(values.axes[2],))
        .compute()
        .to_pandas()
    )
    assert _dict(grouped, "sum") == {"east": 140, "south": 7}
    assert _dict(grouped, "count") == {"east": 2, "south": 1}
    merged = grouped["sum"].sum() / grouped["count"].sum()
    assert merged == 49
    assert values.reduce({"mean": values["value"].mean()}).compute().to_pandas()[
        "mean"
    ].tolist() == [49]


@pytest.mark.slow
def test_cold_grouped_statistic_recovers_without_models_source_or_duckdb(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    import os
    import subprocess
    import sys

    case = analysis_dsl_case_factory("j1")
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute("DELETE FROM customer WHERE customer_id='D'")
        connection.execute(
            """UPDATE "order" SET amount = CASE order_id
                WHEN 'j1_a' THEN 120 WHEN 'j1_b' THEN 20 WHEN 'j1_c' THEN 7
            END WHERE order_id IN ('j1_a', 'j1_b', 'j1_c')"""
        )
    observed = _observe(case, classified=True)
    saved = observed.reduce(
        {"sum": observed["value"].sum(), "count": mv.row_count()}, by=(observed.axes[2],)
    ).persist()
    case.database_path.rename(case.database_path.with_suffix(".offline"))
    (case.root / "models").rename(case.root / "models.offline")
    script = """
import sys
import duckdb
import ibis
import marivo.analysis as mv
from tests.shared_fixtures import terminal_runs
import marivo.semantic as ms
from marivo.datasource.runtime import DatasourceConnectionService

def forbidden(*args, **kwargs):
    raise AssertionError('cold output read touched source, models or DuckDB')
ms.load = forbidden
duckdb.connect = forbidden
ibis.duckdb.connect = forbidden
DatasourceConnectionService.use_backend = forbidden
session = mv.session.resume(sys.argv[1])
before = len(terminal_runs(session))
frame = session.persisted_frame(sys.argv[2]).to_pandas()
axes = [axis for axis in frame.attrs['axes'] if axis['role'] != 'interval']
assert len(axes) == 1
coordinate = axes[0]['name'].rsplit('.', 1)[-1]
assert set(frame.columns) == {coordinate, 'sum', 'count'}
groups = frame[coordinate].tolist()
assert dict(zip(groups, frame['sum'], strict=True)) == {'east': 140, 'south': 7}
assert dict(zip(groups, frame['count'], strict=True)) == {'east': 2, 'south': 1}
assert frame['sum'].sum() / frame['count'].sum() == 49
assert len(terminal_runs(session)) == before
"""
    completed = subprocess.run(
        [sys.executable, "-c", script, case.session.id, saved.artifact_id],
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
    channel = catalog_value(session.catalog, "sales.order.channel", kind="field")
    status = catalog_value(session.catalog, "sales.order.status", kind="field")
    count = session.catalog.get("metric:sales.order_count")
    web = count.where(channel == "web")
    expressions = {"ratio": web / count, "linear": count - count}
    scope = session.time_scope(start="2026-08-01", end="2026-09-01")
    saved = session.observe(expressions, by=(scope, channel, status)).persist()
    rows = saved.to_pandas()
    overall = session.observe(expressions, by=(scope,)).compute().to_pandas()
    for kind in expressions:
        assert rows.set_index(["channel", "status"])[kind].to_dict() == {
            ("mobile", "paid"): 0,
            ("web", "paid"): 1 if kind == "ratio" else 0,
        }
        assert overall[kind].tolist() == pytest.approx([2 / 3] if kind == "ratio" else [0])
    assert session.persisted_frame(saved.artifact_id).to_pandas().equals(rows)


def test_required_terminal_data_damage_revokes_reads(retained_coordinates_case: DslCase) -> None:
    case = retained_coordinates_case
    regions = _region(case)
    statistic = regions.reduce({"count": mv.row_count()}).persist()
    assert statistic.to_pandas()["count"].tolist() == [3]
    path = receipt_paths(case.root, statistic)[0]
    original = path.read_bytes()
    for violation in ("missing", "corrupt"):
        if violation == "missing":
            path.unlink()
        else:
            path.write_bytes(b"broken committed numerical data")
        try:
            reopened = case.session.persisted_frame(statistic.artifact_id)
            for value in (statistic, reopened):
                with pytest.raises(AnalysisError):
                    value.to_pandas()
        finally:
            path.write_bytes(original)
    assert case.session.persisted_frame(statistic.artifact_id).to_pandas()["count"].tolist() == [3]


def test_weighted_coordinates_merge_paired_components(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute('ALTER TABLE "order" ADD COLUMN weight BIGINT DEFAULT 1')
    model = case.root / "models/semantic/sales/models.py"
    model.write_text(
        model.read_text().replace(
            "# Named expressions",
            "weight = ms.measure_column(name='weight', entity=orders, column='weight', additivity=ms.additive_all())\n# Named expressions",
        )
        + "\nweight_value = weight\n@ms.metric()\ndef weighted():\n    return amount_value.weighted_mean(weight=weight_value, time=time_value)\n"
    )
    ms.load(workspace_dir=case.root)
    session = case.session
    customer = catalog_value(session.catalog, "sales.order.customer_id", kind="field")
    channel = catalog_value(session.catalog, "sales.order.channel", kind="field")
    observed = session.observe(
        {"value": session.catalog.get("metric:sales.weighted")},
        by=(session.time_scope(start="2026-08-01", end="2026-09-01"), customer, channel),
    )
    partial = _source(case, session.catalog.get("metric:sales.weighted"), by=(channel,))
    assert partial.compute().to_pandas()["value"].tolist() == [150, 425]
    assert _source(case, session.catalog.get("metric:sales.weighted")).compute().to_pandas()[
        "value"
    ].tolist() == pytest.approx([1000 / 3])


def test_combined_member_classifications_do_not_expand_facts(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    model = case.root / "models/semantic/sales/models.py"
    model.write_text(
        model.read_text().replace(
            "# Named expressions",
            "cohort = ms.dimension_column(name='cohort', entity=customer, column='customer_id')\n# Named expressions",
        )
    )
    ms.load(workspace_dir=case.root)
    session = case.session
    region = catalog_value(session.catalog, "sales.customer.region", kind="field")
    cohort = catalog_value(session.catalog, "sales.customer.cohort", kind="field")
    population = _members(case).classify(by=(region, cohort))
    grouped = session.observe(
        {"value": session.catalog.get("metric:sales.revenue")},
        by=mv.product(session.time_scope(start="2026-08-01", end="2026-09-01"), population),
    )
    frame = grouped.compute().to_pandas()
    assert len(frame) == 4
    assert frame["value"].tolist() == [450, 150, 400, 0]
    assert _observe(case, overall=True).compute().to_pandas()["value"].tolist() == [1000]


def test_numeric_read_grouping_creates_only_row_statistics(
    retained_coordinates_case: DslCase,
) -> None:
    case = retained_coordinates_case
    session = case.session
    channel = catalog_value(session.catalog, "sales.order.channel", kind="field")
    raw = session.read(
        session.members(session.catalog.get("entity:sales.order")),
        {
            "amount": catalog_value(session.catalog, "sales.order.amount", kind="field"),
            "channel": channel,
        },
    )
    # Field values are explicit output; pandas owns post-export classification.
    frame = raw.compute().to_pandas()
    stats = frame["amount"].groupby(frame["channel"]).mean()
    assert stats.to_dict() == {"app": 13.5, "web": 60.0}
    assert stats.mean() == 36.75
    assert not hasattr(raw, "rollup")


def test_invalid_classification_rejects(retained_coordinates_case: DslCase) -> None:
    case = retained_coordinates_case
    values = _observe(case)
    region = _region(case)
    east = region.where(region["region"] == "east")
    with pytest.raises(AnalysisError):
        values.align(east, on=((values.axes[1], east.axes[0]),)).compute()
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute("UPDATE customer SET region=NULL WHERE customer_id='C'")
    # Source Null is a real None classification in the current Field-axis contract.
    field = catalog_value(case.session.catalog, "sales.customer.region", kind="field")
    domain = _members(case).classify(by=(field,))
    grouped = (
        case.session.observe(
            {"value": case.session.catalog.get("metric:sales.revenue")},
            by=mv.product(case.session.time_scope(start="2026-08-01", end="2026-09-01"), domain),
        )
        .compute()
        .to_pandas()
    )
    assert grouped["value"].loc[
        grouped[grouped.attrs["axes"][1]["name"].rsplit(".", 1)[-1]] == "east"
    ].tolist() == [140]
    assert grouped["value"].loc[
        grouped[grouped.attrs["axes"][1]["name"].rsplit(".", 1)[-1]].isna()
    ].tolist() == [7]


def test_duplicate_classification_rejects_source_and_fixed(
    retained_coordinates_case: DslCase,
) -> None:
    case = retained_coordinates_case
    region = catalog_value(case.session.catalog, "sales.customer.region", kind="field")
    values = _observe(case, classified=True)
    before = terminal_runs(case.session)
    with pytest.raises(AnalysisError):
        _source(case, case.session.catalog.get("metric:sales.revenue"), by=(region, region))
    with pytest.raises(AnalysisError):
        values.reduce({"count": mv.row_count()}, by=(values.axes[1], values.axes[1]))
    assert terminal_runs(case.session) == before


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
        DataSchema(
            schema.axes,
            schema.values + schema.values,
            value_paths=schema.value_paths + schema.value_paths,
        )
    assert case.session.persisted_frame(result.artifact_id).to_pandas().equals(frame)


def test_typed_integer_classification_and_singleton(retained_coordinates_case: DslCase) -> None:
    case = retained_coordinates_case
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute("ALTER TABLE customer ADD COLUMN band BIGINT DEFAULT 2")
        connection.execute("UPDATE customer SET band=1 WHERE customer_id='A'")
    model = case.root / "models/semantic/sales/models.py"
    model.write_text(
        model.read_text().replace(
            "# Named expressions",
            "band=ms.dimension_column(name='band', entity=customer, column='band')\n# Named expressions",
        )
    )
    ms.load(workspace_dir=case.root)
    session = case.session
    band = catalog_value(session.catalog, "sales.customer.band", kind="field")
    grouped = session.observe(
        {"value": session.catalog.get("metric:sales.revenue")},
        by=mv.product(
            session.time_scope(start="2026-08-01", end="2026-09-01"),
            _members(case).classify(by=(band,)),
        ),
    )
    assert _dict(grouped.compute().to_pandas()) == {1: 120, 2: 27}
    assert _observe(case, overall=True).compute().to_pandas()["value"].tolist() == [147]


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
        "value"
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
    assert statistic.to_pandas()["count"].tolist() == [2]
    assert case.session.persisted_frame(current.artifact_id).to_pandas().equals(current.to_pandas())


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
            "value": session.catalog.get("metric:sales.revenue"),
            "lines": session.catalog.get("metric:sales.line_revenue"),
        },
        by=mv.product(session.time_scope(start="2026-08-01", end="2026-09-01"), _members(case)),
    )
    selected = quantities.where(quantities["lines"] > 80)
    selected = selected.select({"value": selected["value"]})
    result = selected.persist()
    assert _dict(result.to_pandas()) == {"A": 0}
    assert _observe(
        case, overall=True, members=_selected_members(case, selected)
    ).compute().to_pandas()["value"].tolist() == [0]
    assert session.persisted_frame(result.artifact_id).to_pandas().equals(result.to_pandas())


def test_review_fixed_group_statistics_keep_axes_and_current_rows(
    retained_coordinates_case: DslCase,
) -> None:
    case = retained_coordinates_case
    session = case.session
    region = catalog_value(session.catalog, "sales.customer.region", kind="field")
    grouped = _observe(case, grouped=True)
    fixed = grouped.compute()
    assert _dict(fixed.to_pandas()) == {"east": 140, "south": 7}
    statistic = grouped.reduce({"mean": grouped["value"].mean()}, by=(grouped.axes[1],)).persist()
    frame = statistic.to_pandas()
    assert _dict(frame, "mean") == {"east": 140, "south": 7}
    assert statistic.axes[0].name == grouped.axes[1].name
    assert session.persisted_frame(statistic.artifact_id).to_pandas().equals(frame)
    assert _observe(case, overall=True).compute().to_pandas()["value"].tolist() == [147]


def test_fixed_grouped_statistic_preserves_cell_binding_and_empty_schema(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    observed = _observe(case, classified=True)
    region = catalog_value(case.session.catalog, "sales.customer.region", kind="field")
    for empty, method in ((False, "count"), (False, "mean"), (True, "mean")):
        current = observed
        if method == "mean":
            current = current.where(current["value"] > 0)
        if empty:
            current = current.where(current["value"] > 10000)
        request = mv.row_count() if method == "count" else current["value"].mean()
        result = current.reduce({"value": request}, by=(current.axes[2],)).persist()
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
        assert case.session.persisted_frame(result.artifact_id).to_pandas().equals(frame)
