"""Public logical contract shape, cards and native Help stay aligned."""

from typing import Literal

import pytest

import marivo.analysis as mv
from tests.shared_fixtures import DslCaseFactory, catalog_value


@pytest.mark.parametrize("kind", ("AnalysisFrame", "Population"))
def test_contract_cards_and_actions_without_source_reads_or_runs(
    analysis_dsl_case_factory: DslCaseFactory,
    capsys: pytest.CaptureFixture[str],
    kind: Literal["AnalysisFrame", "Population"],
) -> None:
    case = analysis_dsl_case_factory("j2")
    metric = catalog_value(case.catalog, "sales.revenue", kind="metric")

    population = case.session.members(
        catalog_value(case.session.catalog, "sales.customer", kind="entity")
    )
    receiver = case.session.observe({"revenue": metric}) if kind == "AnalysisFrame" else population
    assert capsys.readouterr().out == ""
    assert receiver.show() is None
    text = capsys.readouterr().out
    assert len(text.encode("utf-8")) <= 8193
    receiver.show()
    assert capsys.readouterr().out == text


@pytest.mark.parametrize("selection", ("members", "where"))
def test_selected_member_filter_applies_and_source_reads_survive_reopen(
    analysis_dsl_case_factory: DslCaseFactory,
    capsys: pytest.CaptureFixture[str],
    selection: Literal["members", "where"],
) -> None:
    case = analysis_dsl_case_factory("j2")
    session = case.session
    region = catalog_value(session.catalog, "sales.customer.region", kind="field")
    revenue = catalog_value(session.catalog, "sales.revenue", kind="metric")

    members = session.members(
        catalog_value(session.catalog, "sales.customer", kind="entity"), where=region == "east"
    )
    complete = mv.coordinates(by=(region,), keys=(("east",), ("south",), ("west",)))
    query = session.observe(
        {"revenue": revenue},
        by=mv.product(session.time_scope(start="2026-08-01", end="2026-09-01"), complete),
        where=mv.in_population(members),
    )
    selected = query.select({"revenue": query["revenue"]})
    for receiver in (members, query, selected):
        receiver.show()
        card = capsys.readouterr().out
        assert len(card.encode()) <= 8193

    saved = selected.persist()
    artifact = saved
    output = saved.to_pandas()
    # Complete output coordinates retain the two groups without selected facts;
    # their fixed sum is additive zero.
    assert output["revenue"].tolist() == [180, 0, 0]
    reads = artifact.source_reads
    assert reads
    assert all(read.scopes and read.read_id for read in reads)
    assert any(scope.source for read in reads for scope in read.scopes)
    read = reads[0]
    values = (read, read.scopes[0], read.scopes[0].period)
    for value in values:
        if value is None:
            continue
        assert "\n" not in repr(value)
        assert len(value.render().encode()) <= 8192
    reopened = session.persisted_frame(artifact.artifact_id)
    assert reopened.source_reads == reads
    assert reopened.to_pandas()["revenue"].tolist() == [180, 0, 0]


def test_population_scope_is_bounded_for_large_literal_and_nested_domains(
    analysis_dsl_case_factory: DslCaseFactory,
    capsys: pytest.CaptureFixture[str],
) -> None:
    case = analysis_dsl_case_factory("j2")
    entity = catalog_value(case.catalog, "sales.customer", kind="entity")
    large = mv.coordinates(
        by=(mv.identity(entity),),
        keys=tuple(((f"member-{i}",),) for i in range(5000)),
    )
    large.show()
    first = capsys.readouterr().out
    large.show()
    assert capsys.readouterr().out == first
    assert len(first.encode()) <= 8193


def test_unicode_scope_keeps_every_continuation_visible(
    analysis_dsl_case_factory: DslCaseFactory,
    capsys: pytest.CaptureFixture[str],
) -> None:
    case = analysis_dsl_case_factory("j2")
    region = catalog_value(case.catalog, "sales.customer.region", kind="field")
    domains = [
        mv.coordinates(by=(region,), keys=tuple(("\u754c" * 600 + f"{i}-{j}",) for j in range(3)))
        for i in range(6)
    ]
    population = domains[0]
    for domain in domains[1:]:
        updated = population.union(domain)
        assert isinstance(updated, mv.Population)
        population = updated
    population.show()
    card = capsys.readouterr().out
    assert len(card.encode()) <= 8193


def test_output_names_and_column_show_preserve_complete_metadata(
    analysis_dsl_case_factory: DslCaseFactory,
    capsys: pytest.CaptureFixture[str],
) -> None:
    case = analysis_dsl_case_factory("j2")
    metric = catalog_value(case.catalog, "sales.revenue", kind="metric")
    names = tuple(f"amount_{index}" for index in range(20))
    query = case.session.observe(dict.fromkeys(names, metric))

    assert query.output_names == names
    column = query[query.output_names[-1]]
    assert column.show() is None  # type: ignore[func-returns-value]  # Verify runtime return contract.


def test_long_captured_output_facts_are_explicitly_omitted(
    analysis_dsl_case_factory: DslCaseFactory,
    capsys: pytest.CaptureFixture[str],
) -> None:
    case = analysis_dsl_case_factory("j2")
    name, unit = "value_" + "x" * 9000, "unit_" + "z" * 9000
    query = case.session.values(by=(), columns={name: unit}, rows=(((), {name: 1}),))
    assert query.output_names == (name,)
    query.show()
    assert name in capsys.readouterr().out
    assert len(repr(query[name])) < 200
    query[name].show()
    card = capsys.readouterr().out
    assert len(card.encode()) <= 8192


def test_frame_and_audit_cards_advertise_only_actual_state_continuations(
    analysis_dsl_case_factory: DslCaseFactory,
    capsys: pytest.CaptureFixture[str],
) -> None:
    case = analysis_dsl_case_factory("j2")
    pending = case.session.values(by=(), columns={"value": None}, rows=(((), {"value": 1}),))
    fixed = pending.compute()
    saved = fixed.persist()
    saved.show()
    audit = capsys.readouterr().out
    assert saved.artifact_id in audit


def test_removed_action_contract_is_not_public() -> None:
    assert "AnalysisContract" not in mv.__all__
    assert "AnalysisAction" not in mv.__all__
    assert not hasattr(mv.Population, "contract")
    assert not hasattr(mv.AnalysisFrame, "contract")
