"""Public Session-to-Artifact Analysis DSL journeys over authored DuckDB sources."""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import duckdb
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo._help.model import NativeHelpRoute
from marivo._help.route import route_help_target
from marivo.analysis.errors import AnalysisError
from tests.shared_fixtures import DslCaseFactory


@pytest.mark.runtime
def test_public_j1_total_uses_registered_source(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    names = case.names
    customers = case.session.members(ms.ref.entity(f"{names.domain}.{names.customer}"))
    revenue = ms.ref.metric(f"{names.domain}.{names.revenue}")
    buyer = ms.ref.relationship(f"{names.domain}.{names.buyer}")
    august = mv.time_scope(start="2026-08-01", end="2026-09-01")

    result = customers.observe(revenue, during=august, via=buyer).rollup().execute()

    assert isinstance(result, mv.MaterializedRolledNumericRelation)
    assert result.to_pandas().iloc[0]["value"] == 1000


@pytest.mark.runtime
def test_public_p2_contract_actions_and_result_card(
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
    )
    saved = observed.execute()
    assert ".show()" in repr(saved)
    assert all(
        isinstance(route_help_target(action.help_target), NativeHelpRoute)
        for action in saved.contract().actions
    )
    saved.show()
    card = capsys.readouterr().out
    assert "unit=CNY" in card
    assert "method=sum" in card
    assert "cell_state_fields=" in card
    assert "<identity>" in card
    assert subject_key not in card
    assert len(card.encode("utf-8")) <= 8192
    saved.show(max_output_bytes=128)
    assert len(capsys.readouterr().out.encode("utf-8")) <= 128

    offline = case.database_path.with_suffix(".offline")
    case.database_path.rename(offline)
    try:
        restored = case.session.artifact(saved.state.artifact_ref)
        assert restored.contract() == saved.contract()
        restored.show()
        assert "exact retained Artifact" in capsys.readouterr().out
    finally:
        offline.rename(case.database_path)


@pytest.mark.runtime
def test_public_p2_empty_and_undefined_cards(
    analysis_dsl_case_factory: DslCaseFactory,
    capsys: pytest.CaptureFixture[str],
) -> None:
    empty = analysis_dsl_case_factory("empty_domain")
    empty_members = empty.session.members(
        ms.ref.entity(f"{empty.names.domain}.{empty.names.customer}")
    ).execute()
    empty_members.show()
    empty_card = capsys.readouterr().out
    assert "rows=0" in empty_card
    assert "<identity>" not in empty_card
    assert empty_members.contract().actions == ()

    case = analysis_dsl_case_factory("zero_denominator")
    names = case.names
    members = case.session.members(ms.ref.entity(f"{names.domain}.{names.customer}"))
    observed = members.observe(
        ms.ref.metric(f"{names.domain}.{names.aov}"),
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=mv.routes(
            mv.route(
                ms.ref.entity(f"{names.domain}.{names.order_line}"),
                through=(
                    ms.ref.relationship(f"{names.domain}.{names.line_order}"),
                    ms.ref.relationship(f"{names.domain}.{names.buyer}"),
                ),
            ),
            mv.route(
                ms.ref.entity(f"{names.domain}.{names.order}"),
                through=(ms.ref.relationship(f"{names.domain}.{names.buyer}"),),
            ),
        ),
    )
    undefined = observed.rollup().execute()
    undefined.show()
    card = capsys.readouterr().out
    assert "undefined" in card.casefold()
    assert "zero_denominator" in card
    assert "weighting=original numerator and denominator components" in card


@pytest.mark.runtime
def test_public_member_read_and_category_selection(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    names = case.names
    members = case.session.members(ms.ref.entity(f"{names.domain}.{names.customer}"))
    region = ms.ref.dimension(f"{names.domain}.{names.customer}.{names.region}")
    read = members.read(region)
    east = read.where(read.value.eq("east")).members()
    east_total = (
        east.observe(
            ms.ref.metric(f"{names.domain}.{names.revenue}"),
            during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
            via=ms.ref.relationship(f"{names.domain}.{names.buyer}"),
        )
        .rollup()
        .execute()
    )
    assert east_total.to_pandas().iloc[0]["value"] == 600
    by_region = (
        members.group_by(region)
        .observe(
            ms.ref.metric(f"{names.domain}.{names.revenue}"),
            during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
            via=ms.ref.relationship(f"{names.domain}.{names.buyer}"),
        )
        .execute()
    )
    assert by_region.to_pandas().set_index("group").loc["east", "value"] == 600
    selected = read.where(read.value.eq("west")).execute()

    assert isinstance(selected, mv.MaterializedSelectedCategoryRelation)
    actions = selected.contract().actions
    assert tuple(action.call for action in actions) == ("relation.members()",)
    assert all(action.help_target.startswith("analysis.") for action in actions)
    assert isinstance(
        case.session.artifact(selected.state.artifact_ref), mv.MaterializedSelectedCategoryRelation
    )
    assert selected.members().execute().to_pandas()["member"].tolist() == ["D"]
    before = len(case.session.runs().items)
    with pytest.raises(AnalysisError, match="fixed selected members plus live Metric"):
        selected.members().observe(
            ms.ref.metric(f"{names.domain}.{names.revenue}"),
            during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
            via=ms.ref.relationship(f"{names.domain}.{names.buyer}"),
        )
    assert len(case.session.runs().items) == before


@pytest.mark.runtime
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
        coordinates=(channel,),
    )
    source_group = observed.group_by(channel).execute()
    fixed_group = observed.execute().group_by(channel).execute()

    assert isinstance(fixed_group, mv.MaterializedGroupedNumericRelation)
    assert fixed_group.to_pandas().equals(source_group.to_pandas())


@pytest.mark.runtime
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
    fixed = members.observe(revenue, during=july, via=buyer).execute()
    live = members.observe(revenue, during=august, via=buyer)
    count_before = len(case.session.runs().items)

    with pytest.raises(AnalysisError, match="mixed live and materialized"):
        live.compare(fixed).execute()
    assert len(case.session.runs().items) == count_before


@pytest.mark.runtime
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
    current = first_members.observe(revenue, during=august, via=buyer)
    separate = separate_members.observe(revenue, during=july, via=buyer)
    different_metric = first_members.observe(count, during=july, via=buyer)
    before = len(case.session.runs().items)

    with pytest.raises(AnalysisError, match="incompatible comparison endpoints"):
        current.compare(separate)
    with pytest.raises(AnalysisError, match="incompatible comparison endpoints"):
        current.compare(different_metric)
    assert len(case.session.runs().items) == before


@pytest.mark.runtime
def test_public_source_repeats_and_failed_run_preserves_prior_artifact(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    names = case.names
    logical = case.session.members(ms.ref.entity(f"{names.domain}.{names.customer}")).observe(
        ms.ref.metric(f"{names.domain}.{names.revenue}"),
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=ms.ref.relationship(f"{names.domain}.{names.buyer}"),
    )
    first = logical.execute()
    second = logical.execute()
    assert first.state.artifact_ref != second.state.artifact_ref
    assert len(case.session.runs().items) == 2

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


@pytest.mark.runtime
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

    change = customers.observe(revenue, during=august, via=buyer).compare(
        customers.observe(revenue, during=july, via=buyer)
    )
    selected = change.where(change.value.lt(0)).members()
    result = selected.observe(revenue, during=september, via=buyer).summarize(mv.mean()).execute()

    assert result.to_pandas().iloc[0]["value"] == 15


@pytest.mark.runtime
def test_public_j3_ratio_rollup_differs_from_current_row_mean(
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
        via=mv.routes(
            mv.route(line, through=(line_order, buyer)),
            mv.route(order, through=(buyer,)),
        ),
        coordinates=(channel,),
    )

    overall = observed.rollup().execute()
    current_mean = observed.summarize(mv.mean()).execute()
    by_channel = observed.group_by(channel).rollup().execute()

    assert overall.to_pandas().iloc[0]["value"] == 40
    assert current_mean.to_pandas().iloc[0]["value"] == pytest.approx(130 / 3)
    assert dict(
        zip(by_channel.to_pandas()["group"], by_channel.to_pandas()["value"], strict=True)
    ) == {
        "web": pytest.approx(160 / 3),
        "mobile": 0,
    }
    restored = case.session.artifact(by_channel.state.artifact_ref)
    assert isinstance(restored, mv.MaterializedRolledRatioRelation)
    assert restored.to_pandas().equals(by_channel.to_pandas())
    assert set(overall.contract().required_parts) == {
        "value.numerator.sum",
        "value.numerator.non_null_count",
        "value.numerator.row_count",
        "value.denominator.count",
        "value.denominator.row_count",
    }
    assert set(overall.contract().retained_parts) == set(overall.contract().required_parts)
    overall.contract().show()
    assert "weighting=original numerator and denominator components" in capsys.readouterr().out
    fixed_observed = observed.execute()
    fixed_ratio = case.session.artifact(fixed_observed.state.artifact_ref)
    assert isinstance(fixed_ratio, mv.MaterializedRatioRelation)
    offline = case.database_path.with_suffix(".offline")
    case.database_path.rename(offline)
    try:
        assert fixed_ratio.rollup().execute().to_pandas().iloc[0]["value"] == 40
    finally:
        offline.rename(case.database_path)


@pytest.mark.runtime
def test_public_j4_spearman_and_fixed_coefficient_selection(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = analysis_dsl_case_factory("j4")
    names = case.names
    customers = case.session.members(ms.ref.entity(f"{names.domain}.{names.customer}"))
    buyer = ms.ref.relationship(f"{names.domain}.{names.buyer}")
    august = mv.time_scope(start="2026-08-01", end="2026-09-01")
    revenue = customers.observe(
        ms.ref.metric(f"{names.domain}.{names.revenue}"), during=august, via=buyer
    )
    count = customers.observe(
        ms.ref.metric(f"{names.domain}.{names.order_count}"), during=august, via=buyer
    )

    association = revenue.correlate(count, method="spearman").execute()
    assert association.contract().actions == (
        mv.AnalysisAction("relation.coefficient", "analysis.MaterializedCoefficientRelation"),
    )
    coefficient = association.coefficient
    selected = coefficient.where(coefficient.value.lt(0)).execute()

    assert association.to_pandas().iloc[0]["coefficient"] == pytest.approx(-0.4)
    assert association.to_pandas().iloc[0]["complete_pair_count"] == 4
    assert selected.to_pandas().iloc[0]["coefficient"] == pytest.approx(-0.4)
    import marivo.analysis.materialization.dsl_public_source as source_bridge
    import marivo.semantic.catalog as catalog

    def unavailable(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("cold recovery must not load semantics or a datasource")

    monkeypatch.setattr(catalog, "load", unavailable)
    monkeypatch.setattr(source_bridge, "public_j1_source", unavailable)
    cold_session = mv.session.resume(case.session.id, by="id")
    restored = cold_session.artifact(association.state.artifact_ref)
    assert isinstance(restored, mv.MaterializedAssociationResult)
    assert restored.to_pandas().iloc[0]["coefficient"] == pytest.approx(-0.4)
    restored_selected = restored.coefficient.where(restored.coefficient.value.lt(0)).execute()
    assert restored_selected.state.artifact_ref == selected.state.artifact_ref


@pytest.mark.runtime
def test_public_snapshot_mismatch_rejects_exact_artifact(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = analysis_dsl_case_factory("j1")
    names = case.names
    result = case.session.members(ms.ref.entity(f"{names.domain}.{names.customer}")).execute()
    store = case.session._runtime.store
    original = store.artifact(result.state.artifact_ref.ref)
    assert original is not None and original.descriptor.j1_exchange is not None
    exchange = replace(original.descriptor.j1_exchange, public_snapshot="{}")
    changed = replace(original, descriptor=replace(original.descriptor, j1_exchange=exchange))
    old_artifact = type(store).artifact

    def altered_artifact(self: object, reference: str) -> object:
        if reference == result.state.artifact_ref.ref:
            return changed
        return old_artifact(self, reference)

    monkeypatch.setattr(type(store), "artifact", altered_artifact)
    with pytest.raises(AnalysisError, match="public continuation binding mismatch") as mismatch:
        case.session.artifact(result.state.artifact_ref)
    assert mismatch.value.expected and mismatch.value.received
    assert mismatch.value.repair is not None
    assert mismatch.value.repair.help_target.canonical_id == "session.artifact"


@pytest.mark.runtime
def test_public_missing_key_and_wrong_relationship_reject(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("missing_key")
    names = case.names
    members = case.session.members(ms.ref.entity(f"{names.domain}.{names.customer}"))
    with pytest.raises(AnalysisError, match="key") as missing:
        members.execute()
    assert missing.value.expected and missing.value.received
    assert missing.value.repair is not None
    assert missing.value.repair.help_target.canonical_id == "actions.execute"

    with pytest.raises(AnalysisError, match=r"Relationship|path") as route:
        members.observe(
            ms.ref.metric(f"{names.domain}.{names.revenue}"),
            during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
            via=ms.ref.relationship(f"{names.domain}.{names.line_order}"),
        )
    assert route.value.repair is not None
    assert route.value.repair.help_target.canonical_id == "dsl.LogicalAnalysisDomain.observe"


@pytest.mark.runtime
def test_public_missing_retained_part_blocks_recovery(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    names = case.names
    result = (
        case.session.members(ms.ref.entity(f"{names.domain}.{names.customer}"))
        .observe(
            ms.ref.metric(f"{names.domain}.{names.revenue}"),
            during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
            via=ms.ref.relationship(f"{names.domain}.{names.buyer}"),
        )
        .execute()
    )
    record = case.session._runtime.store.artifact(result.state.artifact_ref.ref)
    assert record is not None and record.descriptor.retained_parts
    receipt = record.descriptor.retained_parts[0].storage_receipt
    path = case.root / receipt.project_relative_path
    offline = path.with_name(path.name + ".offline")
    path.rename(offline)
    try:
        with pytest.raises(AnalysisError, match=r"(?i)part|receipt|file|missing") as missing:
            case.session.artifact(result.state.artifact_ref)
        assert missing.value.repair is not None
        assert missing.value.repair.help_target.canonical_id == "session.artifact"
    finally:
        offline.rename(path)


@pytest.mark.runtime
def test_public_cold_process_continues_from_retained_parts(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    names = case.names
    customers = case.session.members(ms.ref.entity(f"{names.domain}.{names.customer}"))
    observed = customers.observe(
        ms.ref.metric(f"{names.domain}.{names.revenue}"),
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=ms.ref.relationship(f"{names.domain}.{names.buyer}"),
    ).execute()
    saved_ref = observed.state.artifact_ref.ref
    offline = case.database_path.with_suffix(".offline")
    case.database_path.rename(offline)
    script = """
import sys
import marivo.analysis as mv
saved = mv.session.resume(sys.argv[1], by="id").artifact(sys.argv[2])
assert isinstance(saved, mv.MaterializedNumericRelation)
assert saved.rollup().execute().to_pandas().iloc[0]["value"] == 1000
"""
    try:
        completed = subprocess.run(
            [sys.executable, "-c", script, case.session.id, saved_ref],
            cwd=case.root,
            env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])},
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        offline.rename(case.database_path)
    assert completed.returncode == 0, completed.stderr
