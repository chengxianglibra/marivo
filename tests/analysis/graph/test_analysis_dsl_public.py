"""Public Session-to-Artifact Analysis DSL journeys over authored DuckDB sources."""

from __future__ import annotations

from dataclasses import replace

import duckdb
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo._help.model import NativeHelpRoute
from marivo._help.route import route_help_target
from marivo.analysis.errors import AnalysisError
from tests.shared_fixtures import DSL_NAMES, DslCaseFactory


def test_public_j1_scope_uses_persisted_report_timezone(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    report_timezone = "Asia/Shanghai"
    expected = 677
    case = analysis_dsl_case_factory("j1")
    names = case.names
    session = mv.session.get_or_create("j1-report-zone", report_timezone=report_timezone)
    revenue = ms.ref.metric(f"{names.domain}.{names.revenue}")
    customers = session.members(ms.ref.entity(f"{names.domain}.{names.customer}"))
    buyer = ms.ref.relationship(f"{names.domain}.{names.buyer}")
    august = mv.time_scope(start="2026-08-01", end="2026-09-01")

    member_total = (
        customers.observe(
            revenue,
            during=august,
            via=buyer,
            by=(mv.member(),),
        )
        .rollup()
        .execute()
    )
    assert isinstance(member_total, mv.MaterializedRolledNumericRelation)
    assert member_total.to_pandas().iloc[0]["value"] == expected

    utc_instants = mv.time_scope(start="2026-08-01T00:00:00+00:00", end="2026-09-01T00:00:00+00:00")
    absolute_total = (
        customers.observe(
            revenue,
            during=utc_instants,
            via=buyer,
            by=(mv.member(),),
        )
        .rollup()
        .execute()
    )
    assert absolute_total.to_pandas().iloc[0]["value"] == 1000


def test_public_contract_actions_and_result_card(
    analysis_dsl_case_factory: DslCaseFactory,
    capsys: pytest.CaptureFixture[str],
) -> None:
    case = analysis_dsl_case_factory("j1")
    names = case.names
    logical = case.session.members(ms.ref.entity(f"{names.domain}.{names.customer}"))
    for action in logical.contract().actions:
        assert isinstance(action, mv.AnalysisAction)
        assert action.call.startswith("relation.")
        assert isinstance(route_help_target(action.help_target), NativeHelpRoute)
    assert ".contract().show()" in repr(logical)

    subject_key = "subject-key-only-private-7b5d4e"
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute(
            f'UPDATE "{names.customer}" SET "{names.customer_id}"=? WHERE "{names.customer_id}"=?',
            (subject_key, "A"),
        )
        connection.execute(
            f'UPDATE "{names.order}" SET "{names.customer_id}"=? WHERE "{names.customer_id}"=?',
            (subject_key, "A"),
        )

    observed = logical.observe(
        ms.ref.metric(f"{names.domain}.{names.revenue}"),
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=ms.ref.relationship(f"{names.domain}.{names.buyer}"),
        by=(mv.member(),),
    )
    saved = observed.execute()
    assert ".show()" in repr(saved)
    assert all(
        isinstance(route_help_target(action.help_target), NativeHelpRoute)
        for action in saved.contract().actions
    )
    saved.show()
    card = capsys.readouterr().out
    assert "unit: CNY" in card
    assert "method: sum" in card
    assert "cell_state_fields" not in card
    assert "<identity>" in card
    assert subject_key not in card
    assert len(card.encode("utf-8")) <= 8192
    with pytest.raises(ValueError, match="minimum is"):
        saved.show(max_output_bytes=128)
    assert capsys.readouterr().out == ""

    offline = case.database_path.with_suffix(".offline")
    case.database_path.rename(offline)
    try:
        restored = case.session.artifact(saved.state.artifact_ref)
        assert restored.contract() == saved.contract()
        restored.show()
        assert capsys.readouterr().out == card
    finally:
        offline.rename(case.database_path)


def test_public_empty_and_undefined_cards(
    analysis_dsl_case_factory: DslCaseFactory,
    capsys: pytest.CaptureFixture[str],
) -> None:
    empty = analysis_dsl_case_factory("empty_domain")
    empty_members = empty.session.members(
        ms.ref.entity(f"{empty.names.domain}.{empty.names.customer}")
    ).execute()
    empty_members.show()
    empty_card = capsys.readouterr().out
    assert "Rows: 0 total; 0 shown" in empty_card
    assert "<identity>" not in empty_card
    assert tuple(action.call for action in empty_members.contract().actions) == (
        "relation.cohort(predicate, rule=rule)",
        "relation.penetration_in(reference)",
    )

    case = analysis_dsl_case_factory("zero_denominator")
    names = case.names
    members = case.session.members(ms.ref.entity(f"{names.domain}.{names.customer}"))
    observed = members.observe(
        ms.ref.metric(f"{names.domain}.{names.aov}"),
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=(
            mv.path(
                ms.ref.relationship(f"{names.domain}.{names.line_order}"),
                ms.ref.relationship(f"{names.domain}.{names.buyer}"),
            ),
            mv.path(ms.ref.relationship(f"{names.domain}.{names.buyer}")),
        ),
        by=(mv.member(),),
    )
    undefined = observed.rollup().execute()
    undefined.show()
    card = capsys.readouterr().out
    assert "undefined" in card.casefold()
    assert "zero_denominator" in card
    assert "weighting: original numerator and denominator components" in card


def test_public_singleton_same_entity_observation_allows_omitted_route(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    names = case.names
    orders = case.session.members(ms.ref.entity(f"{names.domain}.{names.order}"))
    channel = ms.ref.dimension(f"{names.domain}.{names.order}.{names.channel}")
    revenue = ms.ref.metric(f"{names.domain}.{names.revenue}")
    august = mv.time_scope(start="2026-08-01", end="2026-09-01")
    grouped = orders.observe(revenue, during=august, by=(channel,))
    assert isinstance(grouped, mv.LogicalNumericRelation)
    rows = grouped.execute().to_pandas()
    assert dict(zip(rows["group"], rows["value"], strict=True)) == {"web": 850, "mobile": 150}

    observed = orders.observe(
        revenue,
        during=august,
        by=(),
    )

    assert observed.rollup().execute().to_pandas()["value"].tolist() == [1000]


def test_public_grouped_foreign_entity_observation_infers_unique_route(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    names = case.names
    customers = case.session.members(ms.ref.entity(f"{names.domain}.{names.customer}"))
    revenue = ms.ref.metric(f"{names.domain}.{names.revenue}")
    august = mv.time_scope(start="2026-08-01", end="2026-09-01")
    before = case.session.runs().items

    inferred = customers.observe(
        revenue,
        during=august,
        by=(ms.ref.dimension(f"{names.domain}.{names.customer}.{names.region}"),),
    )
    assert case.session.runs().items == before
    rows = inferred.execute().to_pandas()
    values = rows.set_index("group")["value"]
    assert values.dropna().to_dict() == {"east": 600, "south": 400}
    assert values.loc[["west"]].isna().all()


def test_public_fixed_coordinate_rollup_uses_retained_group_state(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    names = case.names
    members = case.session.members(ms.ref.entity(f"{names.domain}.{names.customer}"))
    channel = ms.ref.dimension(f"{names.domain}.{names.order}.{names.channel}")
    observed = members.observe(
        ms.ref.metric(f"{names.domain}.{names.revenue}"),
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=ms.ref.relationship(f"{names.domain}.{names.buyer}"),
        by=(
            mv.member(),
            channel,
        ),
    )
    source_group = observed.group_by(channel).execute()
    fixed_group = observed.execute().group_by(channel).execute()

    assert isinstance(fixed_group, mv.MaterializedGroupedNumericRelation)
    assert fixed_group.to_pandas().equals(source_group.to_pandas())


def test_public_mixed_fixed_and_live_rejects_before_run(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    names = case.names
    members = case.session.members(ms.ref.entity(f"{names.domain}.{names.customer}"))
    revenue = ms.ref.metric(f"{names.domain}.{names.revenue}")
    buyer = ms.ref.relationship(f"{names.domain}.{names.buyer}")
    july = mv.time_scope(start="2026-07-01", end="2026-08-01")
    august = mv.time_scope(start="2026-08-01", end="2026-09-01")
    fixed = members.observe(revenue, during=july, via=buyer, by=(mv.member(),)).execute()
    live = members.observe(revenue, during=august, via=buyer, by=(mv.member(),))
    count_before = len(case.session.runs().items)

    with pytest.raises(AnalysisError, match="mixed live and materialized"):
        live.compare(fixed).absolute.execute()
    assert len(case.session.runs().items) == count_before


def test_public_comparison_rejects_different_members_and_metric_units(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j4")
    names = case.names
    entity = ms.ref.entity(f"{names.domain}.{names.customer}")
    first_members = case.session.members(entity)
    separate_members = case.session.members(entity)
    buyer = ms.ref.relationship(f"{names.domain}.{names.buyer}")
    july = mv.time_scope(start="2026-07-01", end="2026-08-01")
    august = mv.time_scope(start="2026-08-01", end="2026-09-01")
    revenue = ms.ref.metric(f"{names.domain}.{names.revenue}")
    count = ms.ref.metric(f"{names.domain}.{names.order_count}")
    current = first_members.observe(revenue, during=august, via=buyer, by=(mv.member(),))
    separate = separate_members.observe(revenue, during=july, via=buyer, by=(mv.member(),))
    different_metric = first_members.observe(count, during=july, via=buyer, by=(mv.member(),))
    before = len(case.session.runs().items)

    with pytest.raises(AnalysisError, match="only one comparison condition"):
        current.compare(separate)
    with pytest.raises(AnalysisError, match="different quantity templates"):
        current.compare(different_metric)
    assert len(case.session.runs().items) == before


def test_public_failed_run_preserves_prior_artifact(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    names = case.names
    logical = case.session.members(ms.ref.entity(f"{names.domain}.{names.customer}")).observe(
        ms.ref.metric(f"{names.domain}.{names.revenue}"),
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=ms.ref.relationship(f"{names.domain}.{names.buyer}"),
        by=(mv.member(),),
    )
    first = logical.execute()

    offline = case.database_path.with_suffix(".offline")
    case.database_path.rename(offline)
    try:
        with pytest.raises(AnalysisError, match=r"(?i)duckdb|source|database"):
            logical.execute()
        recovered = case.session.artifact(first.state.artifact_ref)
        assert isinstance(recovered, mv.MaterializedNumericRelation)
        assert recovered.to_pandas().equals(first.to_pandas())
    finally:
        offline.rename(case.database_path)


def test_public_j2_selected_members_and_next_month_mean(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    names = case.names
    customers = case.session.members(ms.ref.entity(f"{names.domain}.{names.customer}"))
    revenue = ms.ref.metric(f"{names.domain}.{names.revenue}")
    buyer = ms.ref.relationship(f"{names.domain}.{names.buyer}")
    july = mv.time_scope(start="2026-07-01", end="2026-08-01")
    august = mv.time_scope(start="2026-08-01", end="2026-09-01")
    september = mv.time_scope(start="2026-09-01", end="2026-10-01")

    change = (
        customers.observe(revenue, during=august, via=buyer, by=(mv.member(),))
        .compare(customers.observe(revenue, during=july, via=buyer, by=(mv.member(),)))
        .absolute
    )
    selected = change.where(change.value.lt(0)).members()
    observed = selected.observe(
        revenue,
        during=september,
        via=buyer,
        by=(mv.member(),),
    )
    result = observed.aggregate(mv.mean()).execute()
    assert result.to_pandas().iloc[0]["value"] == 15
    assert observed.rollup().execute().to_pandas()["value"].tolist() == [30]


def test_public_j3_ratio_rollup_retains_original_components(
    analysis_dsl_case_factory: DslCaseFactory,
    capsys: pytest.CaptureFixture[str],
) -> None:
    case = analysis_dsl_case_factory("j3")
    names = case.names
    customers = case.session.members(ms.ref.entity(f"{names.domain}.{names.customer}"))
    aov = ms.ref.metric(f"{names.domain}.{names.aov}")
    order = ms.ref.entity(f"{names.domain}.{names.order}")
    line = ms.ref.entity(f"{names.domain}.{names.order_line}")
    buyer = ms.ref.relationship(f"{names.domain}.{names.buyer}")
    line_order = ms.ref.relationship(f"{names.domain}.{names.line_order}")
    channel = ms.ref.dimension(f"{names.domain}.{names.order}.{names.channel}")
    august = mv.time_scope(start="2026-08-01", end="2026-09-01")
    observed = customers.observe(
        aov,
        during=august,
        via=(
            mv.path(line_order, buyer),
            mv.path(buyer),
        ),
        by=(
            mv.member(),
            channel,
        ),
    )

    overall = observed.rollup().execute()

    assert overall.to_pandas().iloc[0]["value"] == 40
    assert set(overall.contract().required_parts) == {
        "original_state",
        "coverage",
        "allocation_state",
    }
    assert set(overall.contract().retained_parts) == set(overall.contract().required_parts)
    overall.contract().show()
    assert "weighting=original numerator and denominator components" in capsys.readouterr().out


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
    devices = case.session.members(ms.ref.entity("telemetry.device"))
    buyer = ms.ref.relationship("telemetry.reading_device")
    august = mv.time_scope(start="2026-08-01", end="2026-09-01")
    signal = devices.observe(
        ms.ref.metric("telemetry.signal_sum"),
        during=august,
        via=buyer,
        by=(mv.member(),),
    )
    readings = devices.observe(
        ms.ref.metric("telemetry.reading_count"),
        during=august,
        via=buyer,
        by=(mv.member(),),
    )

    association = signal.correlate(readings, method="spearman").execute()
    association_frame = association.to_pandas()

    assert association_frame.iloc[0]["coefficient"] == pytest.approx(-0.4)
    assert association_frame.iloc[0]["complete_pair_count"] == 4


def test_public_missing_key_and_wrong_relationship_reject(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("missing_key")
    names = case.names
    members = case.session.members(ms.ref.entity(f"{names.domain}.{names.customer}"))
    with pytest.raises(AnalysisError, match="identity") as missing:
        members.execute()
    assert missing.value.expected and missing.value.received
    assert missing.value.repair is not None
    assert missing.value.repair.help_target.canonical_id == "actions.execute"

    with pytest.raises(AnalysisError, match=r"(?i)relationship|path") as route:
        members.observe(
            ms.ref.metric(f"{names.domain}.{names.revenue}"),
            during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
            via=ms.ref.relationship(f"{names.domain}.{names.line_order}"),
            by=(mv.member(),),
        )
    assert route.value.repair is not None
    assert route.value.repair.help_target.canonical_id == "dsl.LogicalAnalysisDomain.observe"
