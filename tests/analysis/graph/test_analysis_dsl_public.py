"""Public Session-to-Artifact journeys over authored DuckDB sources."""

from __future__ import annotations

from dataclasses import replace

import duckdb
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo._help.model import NativeHelpRoute
from marivo._help.route import route_help_target
from marivo.analysis.errors import AnalysisError
from marivo.semantic.errors import SemanticExpressionError
from tests.shared_fixtures import DSL_NAMES, DslCase, DslCaseFactory


def _observe(
    case: DslCase,
    start: str,
    end: str,
    *,
    session: mv.Session | None = None,
    metric: str | None = None,
    members: mv.Population | None = None,
) -> mv.Query:
    session = case.session if session is None else session
    names = case.names
    population = (
        session.members(ms.ref.entity(f"{names.domain}.{names.customer}"))
        if members is None
        else members
    )
    value = session.catalog.metrics.get(
        f"{names.domain}.{names.revenue if metric is None else metric}"
    )
    return session.observe(
        {"value": value},
        during=session.time_scope(start=start, end=end),
        by=population,
        members=population,
    )


def test_public_j1_scope_uses_persisted_report_timezone(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    session = mv.session.get_or_create("j1-report-zone", report_timezone="Asia/Shanghai")
    local = _observe(case, "2026-08-01", "2026-09-01", session=session).rollup().execute()
    assert local.to_pandas()[("value", "value")].tolist() == [677]
    absolute = (
        _observe(case, "2026-08-01T00:00:00+00:00", "2026-09-01T00:00:00+00:00", session=session)
        .rollup()
        .execute()
    )
    assert absolute.to_pandas()[("value", "value")].tolist() == [1000]


def test_public_contract_actions_and_result_card(
    analysis_dsl_case_factory: DslCaseFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    case = analysis_dsl_case_factory("j1")
    population = case.session.members(ms.ref.entity("sales.customer"))
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
    assert isinstance(route_help_target("analysis.query"), NativeHelpRoute)
    saved = observed.execute()
    assert subject_key not in repr(saved) and ".show()" in repr(saved)
    assert saved._manifest.data.schema.values[0].unit == "CNY"
    saved.show(n=2)
    card = capsys.readouterr().out
    assert "Artifact" in card and "rows: 4" in card
    assert "cell_state_fields" not in card
    assert len(card.encode()) <= 8192
    with pytest.raises(AnalysisError):
        saved.show(n=-1)
    assert capsys.readouterr().out == ""
    offline = case.database_path.with_suffix(".offline")
    case.database_path.rename(offline)
    try:
        reopened = case.session.artifact(saved.id)
        reopened.show(n=2)
        assert capsys.readouterr().out == card
        assert reopened.to_pandas().equals(saved.to_pandas())
    finally:
        offline.rename(case.database_path)


def test_public_empty_and_undefined_cards(
    analysis_dsl_case_factory: DslCaseFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    empty = analysis_dsl_case_factory("empty_domain")
    members = empty.session.members(ms.ref.entity("sales.customer"))
    saved = empty.session.query(members).execute()
    assert saved.to_pandas().empty
    saved.show()
    assert "rows: 0" in capsys.readouterr().out
    case = analysis_dsl_case_factory("zero_denominator")
    ratio = _observe(case, "2026-08-01", "2026-09-01", metric=case.names.aov).rollup().execute()
    assert ratio.to_pandas()[("value", "value")].isna().all()
    ratio.show()
    assert "rows: 1" in capsys.readouterr().out


def test_public_singleton_same_entity_observation_allows_omitted_route(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    session = case.session
    metric = session.catalog.metrics.get("sales.revenue")
    channel = session.catalog.fields.get("sales.order.channel")
    window = session.time_scope(start="2026-08-01", end="2026-09-01")
    grouped = session.observe({"value": metric}, during=window, by=(channel,))
    frame = grouped.execute().to_pandas()
    assert dict(zip(frame.iloc[:, 0], frame[("value", "value")], strict=True)) == {
        "web": 850,
        "mobile": 150,
    }
    assert session.observe({"value": metric}, during=window).rollup().execute().to_pandas()[
        ("value", "value")
    ].tolist() == [1000]


def test_public_grouped_foreign_entity_observation_infers_unique_route(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    session = case.session
    region = session.catalog.fields.get("sales.customer.region")
    population = session.members(ms.ref.entity("sales.customer")).classify(by=(region,))
    before = session.runs().items
    query = session.observe(
        {"value": session.catalog.metrics.get("sales.revenue")},
        during=session.time_scope(start="2026-08-01", end="2026-09-01"),
        by=population,
    )
    assert session.runs().items == before
    frame = query.execute().to_pandas()
    assert dict(zip(frame.iloc[:, 0], frame[("value", "value")], strict=True)) == {
        "east": 600,
        "south": 400,
        "west": 0,
    }


def test_public_coordinate_rollup_is_composed_before_terminal_publication(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    session = case.session
    channel = session.catalog.fields.get("sales.order.channel")
    customer = session.catalog.fields.get("sales.order.customer_id")
    query = session.observe(
        {"value": session.catalog.metrics.get("sales.revenue")},
        during=session.time_scope(start="2026-08-01", end="2026-09-01"),
        by=(customer, channel),
    )
    grouped = query.rollup(by=(channel,)).execute()
    direct = session.observe(
        {"value": session.catalog.metrics.get("sales.revenue")},
        during=session.time_scope(start="2026-08-01", end="2026-09-01"),
        by=(channel,),
    ).execute()
    assert grouped.to_pandas().equals(direct.to_pandas())
    assert session.artifact(grouped.id).to_pandas().equals(grouped.to_pandas())


def test_public_mixed_fixed_and_live_rejects_before_run(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    fixed = _observe(case, "2026-07-01", "2026-08-01").execute()
    live = _observe(case, "2026-08-01", "2026-09-01")
    before = case.session.runs().items
    with pytest.raises(AnalysisError):
        live.compare(fixed)  # type: ignore[arg-type]  # Exercise the terminal Artifact boundary.
    assert case.session.runs().items == before


def test_public_comparison_uses_semantic_member_binding_and_checks_units(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j4")
    current = _observe(case, "2026-08-01", "2026-09-01")
    separate = _observe(case, "2026-07-01", "2026-08-01")
    compared = current.compare(separate).execute().to_pandas()
    assert len(compared) == 4
    assert not compared[("value", "value.absolute")].isna().any()
    count = _observe(case, "2026-07-01", "2026-08-01", metric=case.names.order_count)
    before = case.session.runs().items
    with pytest.raises(AnalysisError):
        current.compare(count)
    assert case.session.runs().items == before


def test_public_failed_run_preserves_prior_artifact(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    logical = _observe(case, "2026-08-01", "2026-09-01")
    first = logical.execute()
    expected = first.to_pandas()
    offline = case.database_path.with_suffix(".offline")
    case.database_path.rename(offline)
    try:
        with pytest.raises(AnalysisError):
            logical.execute()
        assert case.session.artifact(first.id).to_pandas().equals(expected)
    finally:
        offline.rename(case.database_path)


def test_public_j2_selected_members_and_next_month_mean(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    current = _observe(case, "2026-08-01", "2026-09-01")
    baseline = _observe(case, "2026-07-01", "2026-08-01")
    change = current.compare(baseline)
    selected = change.where(change["value.absolute"] < 0).population()
    observed = _observe(case, "2026-09-01", "2026-10-01", members=selected)
    assert observed.reduce({"mean": observed["value"].mean()}).execute().to_pandas()[
        ("value", "mean")
    ].tolist() == [15]
    assert observed.rollup().execute().to_pandas()[("value", "value")].tolist() == [30]


def test_public_j3_ratio_rollup_uses_original_components(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j3")
    query = _observe(case, "2026-08-01", "2026-09-01", metric=case.names.aov)
    assert query.rollup().execute().to_pandas()[("value", "value")].tolist() == [40]
    assert query.rollup().rollup().execute().to_pandas()[("value", "value")].tolist() == [40]


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
    devices = session.members(ms.ref.entity("telemetry.device"))
    query = session.observe(
        {
            "signal": session.catalog.metrics.get("telemetry.signal_sum"),
            "readings": session.catalog.metrics.get("telemetry.reading_count"),
        },
        during=session.time_scope(start="2026-08-01", end="2026-09-01"),
        by=devices,
    )
    frame = query.execute().to_pandas()
    assert len(frame) == 4
    assert frame[("value", "signal")].corr(
        frame[("value", "readings")], method="spearman"
    ) == pytest.approx(-0.4)
    assert frame[("value", "signal")].count() == 4
    assert query.execute()._manifest.data.schema.values[0].unit == "kWh"


def test_public_wrong_relationship_rejects_before_execution(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    session = case.session
    members = session.members(ms.ref.entity("sales.order"))
    wrong = session.catalog.relationships.get("sales.line_order")
    region = session.catalog.fields.get("sales.customer.region")
    before = session.runs().items
    with pytest.raises(SemanticExpressionError) as route:
        session.read(members, {"region": region.via((wrong,))})
    assert route.value.repair is not None
    assert route.value.repair.help_target is not None
    assert session.runs().items == before
