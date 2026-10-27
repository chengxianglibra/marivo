"""Public Session-to-Artifact journeys over authored DuckDB sources."""

from __future__ import annotations

from dataclasses import replace

import duckdb
import pytest

import marivo.analysis as mv
from marivo._help.model import NativeHelpRoute
from marivo._help.route import route_help_target
from marivo.analysis.errors import AnalysisError
from marivo.datasource.errors import DatasourceConnectionError
from marivo.semantic.errors import SemanticExpressionError
from tests.shared_fixtures import DSL_NAMES, DslCase, DslCaseFactory, catalog_value, terminal_runs


def _observe(
    case: DslCase,
    start: str,
    end: str,
    *,
    session: mv.Session | None = None,
    metric: str | None = None,
    members: mv.Population | None = None,
    scalar: bool = False,
) -> mv.AnalysisFrame:
    session = case.session if session is None else session
    names = case.names
    population = (
        session.members(
            catalog_value(session.catalog, f"{names.domain}.{names.customer}", kind="entity")
        )
        if members is None
        else members
    )
    value = catalog_value(
        session.catalog,
        f"{names.domain}.{(names.revenue if metric is None else metric)}",
        kind="metric",
    )
    return session.observe(
        {"value": value},
        by=(session.time_scope(start=start, end=end),)
        if scalar
        else mv.product(session.time_scope(start=start, end=end), population),
        where=mv.in_population(population),
    )


def test_public_j1_scope_uses_persisted_report_timezone(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    session = mv.session.get_or_create("j1-report-zone", timezone="Asia/Shanghai")
    local = _observe(case, "2026-08-01", "2026-09-01", session=session, scalar=True).persist()
    assert local.to_pandas()["value"].tolist() == [677]
    absolute = _observe(
        case, "2026-08-01T00:00:00+00:00", "2026-09-01T00:00:00+00:00", session=session, scalar=True
    ).persist()
    assert absolute.to_pandas()["value"].tolist() == [1000]


def test_public_contract_actions_and_result_card(
    analysis_dsl_case_factory: DslCaseFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    case = analysis_dsl_case_factory("j1")
    population = case.session.members(case.session.catalog.get("entity:sales.customer").value)
    assert isinstance(route_help_target("analysis.population"), NativeHelpRoute)
    assert ".show()" in repr(population)
    subject_key = "subject-key-only-private-7b5d4e"
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute("UPDATE customer SET customer_id=? WHERE customer_id='A'", [subject_key])
        connection.execute(
            'UPDATE "order" SET customer_id=? WHERE customer_id=?', [subject_key, "A"]
        )
    observed = _observe(case, "2026-08-01", "2026-09-01")
    assert observed.output_names == ("value",)
    assert isinstance(route_help_target("analysis.analysis_frame"), NativeHelpRoute)
    saved = observed.persist()
    assert subject_key not in repr(saved) and ".show()" in repr(saved)
    assert (
        saved._local._audit_manifest(saved._require_stored("artifact_id"))
        .data.schema.values[0]
        .unit
        == "CNY"
    )
    saved.show(n=2)
    card = capsys.readouterr().out
    assert "AnalysisFrame" in card and "rows: 4" in card
    assert "cell_state_fields" not in card
    assert len(card.encode()) <= 8192
    with pytest.raises(AnalysisError):
        saved.show(n=-1)
    assert capsys.readouterr().out == ""
    offline = case.database_path.with_suffix(".offline")
    case.database_path.rename(offline)
    try:
        reopened = case.session.persisted_frame(saved.artifact_id)
        reopened.show(n=2)
        reopened_card = capsys.readouterr().out
        assert "source declaration retained" in card
        assert "no retained source declaration" in reopened_card
        assert reopened_card == card.replace(
            "source declaration retained; attribute(by=...) checks rules and reads fresh source facts",
            "no retained source declaration; rebuild an explicit source observation with saved joint keys",
        )
        assert reopened.to_pandas().equals(saved.to_pandas())
    finally:
        offline.rename(case.database_path)


def test_public_empty_and_undefined_cards(
    analysis_dsl_case_factory: DslCaseFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    empty = analysis_dsl_case_factory("empty_domain")
    members = empty.session.members(empty.session.catalog.get("entity:sales.customer").value)
    saved = empty.session.frame(members).persist()
    assert saved.to_pandas().empty
    saved.show()
    assert "rows: 0" in capsys.readouterr().out
    case = analysis_dsl_case_factory("zero_denominator")
    ratio = _observe(case, "2026-08-01", "2026-09-01", metric=case.names.aov, scalar=True).persist()
    assert ratio.to_pandas()["value"].isna().all()
    ratio.show()
    assert "rows: 1" in capsys.readouterr().out


def test_public_singleton_same_entity_observation_allows_omitted_route(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    session = case.session
    metric = session.catalog.get("metric:sales.revenue").value
    channel = catalog_value(session.catalog, "sales.order.channel", kind="field")
    window = session.time_scope(start="2026-08-01", end="2026-09-01")
    grouped = session.observe({"value": metric}, by=(window, channel))
    frame = grouped.persist().to_pandas()
    assert dict(zip(frame["sales.order.channel"], frame["value"], strict=True)) == {
        "web": 850,
        "mobile": 150,
    }
    assert session.observe({"value": metric}, by=(window,)).persist().to_pandas()[
        "value"
    ].tolist() == [1000]


def test_public_grouped_foreign_entity_observation_infers_unique_route(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    session = case.session
    region = catalog_value(session.catalog, "sales.customer.region", kind="field")
    population = session.members(session.catalog.get("entity:sales.customer").value).classify(
        by=(region,)
    )
    before = terminal_runs(session)
    query = session.observe(
        {"value": session.catalog.get("metric:sales.revenue").value},
        by=mv.product(session.time_scope(start="2026-08-01", end="2026-09-01"), population),
    )
    assert terminal_runs(session) == before
    frame = query.persist().to_pandas()
    assert dict(zip(frame["sales.customer.region"], frame["value"], strict=True)) == {
        "east": 600,
        "south": 400,
        "west": 0,
    }


def test_public_coordinate_rollup_is_composed_before_terminal_publication(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    session = case.session
    channel = catalog_value(session.catalog, "sales.order.channel", kind="field")
    customer = catalog_value(session.catalog, "sales.order.customer_id", kind="field")
    query = session.observe(
        {"value": session.catalog.get("metric:sales.revenue").value},
        by=(session.time_scope(start="2026-08-01", end="2026-09-01"), customer, channel),
    )
    grouped = query.reduce(
        {"value": query["value"].sum()}, by=(query.axes[0], query.axes[2])
    ).persist()
    direct = session.observe(
        {"value": session.catalog.get("metric:sales.revenue").value},
        by=(session.time_scope(start="2026-08-01", end="2026-09-01"), channel),
    ).persist()
    grouped_rows, direct_rows = grouped.to_pandas(), direct.to_pandas()
    assert grouped.axes[1].name == direct.axes[1].name
    assert grouped_rows.to_numpy().tolist() == direct_rows.to_numpy().tolist()
    assert grouped_rows.dtypes.tolist() == direct_rows.dtypes.tolist()
    assert session.persisted_frame(grouped.artifact_id).to_pandas().equals(grouped.to_pandas())


def test_public_mixed_fixed_and_live_captures_without_a_run(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    fixed = _observe(case, "2026-07-01", "2026-08-01").persist()
    live = _observe(case, "2026-08-01", "2026-09-01")
    before = terminal_runs(case.session)
    combined = live.compare(
        {"reference": mv.baseline(fixed, on=tuple(zip(live.axes, fixed.axes, strict=True)))}
    )
    assert isinstance(combined, mv.AnalysisFrame)
    assert terminal_runs(case.session) == before


def test_public_comparison_uses_semantic_member_binding_and_checks_units(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j4")
    current = _observe(case, "2026-08-01", "2026-09-01")
    separate = _observe(case, "2026-07-01", "2026-08-01")
    compared = (
        current.compare(
            {
                "reference": mv.baseline(
                    separate,
                    on=tuple(zip(current.axes, separate.axes, strict=True)),
                    match=mv.ordinal(),
                )
            }
        )
        .persist()
        .to_pandas()
    )
    assert len(compared) == 4
    assert not compared["reference.absolute.value"].isna().any()
    count = _observe(case, "2026-07-01", "2026-08-01", metric=case.names.order_count)
    before = terminal_runs(case.session)
    with pytest.raises(AnalysisError):
        current.compare(
            {"reference": mv.baseline(count, on=tuple(zip(current.axes, count.axes, strict=True)))}
        )
    assert terminal_runs(case.session) == before


def test_public_failed_run_preserves_prior_artifact(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    logical = _observe(case, "2026-08-01", "2026-09-01")
    first = logical.persist()
    expected = first.to_pandas()
    offline = case.database_path.with_suffix(".offline")
    case.database_path.rename(offline)
    try:
        before = terminal_runs(case.session)
        with pytest.raises(DatasourceConnectionError):
            logical.persist()
        assert terminal_runs(case.session) == before
        assert case.session.persisted_frame(first.artifact_id).to_pandas().equals(expected)
    finally:
        offline.rename(case.database_path)


def test_public_j2_selected_members_and_next_month_mean(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    current = _observe(case, "2026-08-01", "2026-09-01")
    baseline = _observe(case, "2026-07-01", "2026-08-01")
    change = current.compare(
        {
            "reference": mv.baseline(
                baseline,
                on=tuple(zip(current.axes, baseline.axes, strict=True)),
                match=mv.ordinal(),
            )
        }
    )
    chosen = change.where(change["reference.absolute.value"] < 0)
    selected = case.session.population_from(
        chosen, on=((chosen.axes[1], case.session.catalog.get("entity:sales.customer").value),)
    )
    observed = _observe(case, "2026-09-01", "2026-10-01", members=selected)
    summary = (
        observed.reduce({"mean": observed["value"].mean(), "value": observed["value"].sum()})
        .persist()
        .to_pandas()
    )
    assert summary["mean"].tolist() == [15]
    assert summary["value"].tolist() == [30]


def test_public_j3_ratio_rollup_uses_original_components(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j3")
    query = _observe(case, "2026-08-01", "2026-09-01", metric=case.names.aov, scalar=True)
    assert query.compute().to_pandas()["value"].tolist() == [40]
    assert not hasattr(query, "rollup")


def test_public_j4_renamed_entity_and_fields_keep_the_public_route(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    names = replace(
        DSL_NAMES,
        domain="telemetry",
        customer="device",
        order="reading",
        order_line="sample",
        customer_id="device_key",
        order_id="reading_key",
        line_id="sample_key",
        region="zone",
        channel="sensor_type",
        status="quality_flag",
        ordered_at="observed_at",
        amount="signal_value",
        line_amount="sample_value",
        buyer="reading_device",
        line_order="sample_reading",
        revenue="signal_sum",
        order_count="reading_count",
        line_revenue="sample_sum",
        aov="sample_per_reading",
    )
    case = analysis_dsl_case_factory("j4", names=names, revenue_unit="kWh")
    session = case.session
    devices = session.members(session.catalog.get("entity:telemetry.device").value)
    query = session.observe(
        {
            "signal": session.catalog.get("metric:telemetry.signal_sum").value,
            "readings": session.catalog.get("metric:telemetry.reading_count").value,
        },
        by=mv.product(session.time_scope(start="2026-08-01", end="2026-09-01"), devices),
    )
    saved = query.persist()
    frame = saved.to_pandas()
    assert len(frame) == 4
    assert frame["signal"].corr(frame["readings"], method="spearman") == pytest.approx(-0.4)
    assert frame["signal"].count() == 4
    assert saved._schema.values[0].unit == "kWh"


def test_public_wrong_relationship_rejects_before_execution(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    session = case.session
    members = session.members(session.catalog.get("entity:sales.order").value)
    wrong = session.catalog.get("relationship:sales.line_order").value
    region = catalog_value(session.catalog, "sales.customer.region", kind="field")
    before = terminal_runs(session)
    with pytest.raises(SemanticExpressionError) as route:
        session.read(members, {"region": region.via((wrong,))})
    assert route.value.repair is not None
    assert route.value.repair.help_target is not None
    assert terminal_runs(session) == before
