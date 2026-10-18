"""Public logical contract shape, cards and native Help stay aligned."""

from typing import Literal

import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.datasource.adapters import SourceSession
from tests.shared_fixtures import DslCaseFactory, rendered_help


def test_removed_action_contract_is_not_public() -> None:
    assert "AnalysisContract" not in mv.__all__
    assert "AnalysisAction" not in mv.__all__
    assert not hasattr(mv.Population, "contract")
    assert not hasattr(mv.Query, "contract")


@pytest.mark.parametrize("kind", ("Query", "Population"))
def test_contract_cards_and_actions_without_source_reads_or_runs(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    kind: Literal["Query", "Population"],
) -> None:
    case = analysis_dsl_case_factory("j2")
    metric = case.catalog.metrics.get("sales.revenue")

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("logical contract opened or read a source")

    monkeypatch.setattr(SourceSession, "__enter__", forbidden)
    monkeypatch.setattr(SourceSession, "execute", forbidden)
    population = case.session.members(ms.ref.entity("sales.customer"))
    receiver = case.session.observe({"revenue": metric}) if kind == "Query" else population
    assert capsys.readouterr().out == ""
    assert ".show()" in repr(receiver)
    assert receiver.show() is None
    text = capsys.readouterr().out
    assert text.startswith(f"logical {kind}\n")
    assert len(text.encode("utf-8")) <= 8193
    assert "definition=" in text and "owner=" in text
    assert "call=" not in text
    target = f"analysis.{kind.lower()}.show"
    assert rendered_help(receiver.show) == rendered_help(target)
    receiver.show()
    assert capsys.readouterr().out == text
    assert case.session.runs().items == ()


@pytest.mark.parametrize("selection", ("members", "where"))
def test_selected_member_scope_survives_contract_transform_and_saved_evidence(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    selection: Literal["members", "where"],
) -> None:
    case = analysis_dsl_case_factory("j2")
    session = case.session
    region = session.catalog.fields.get("sales.customer.region")
    revenue = session.catalog.metrics.get("sales.revenue")

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("scope disclosure opened or read a source")

    with monkeypatch.context() as guard:
        guard.setattr(SourceSession, "__enter__", forbidden)
        guard.setattr(SourceSession, "execute", forbidden)
        members = session.members(ms.ref.entity("sales.customer"), where=region == "east")
        complete = mv.coordinates(by=(region,), keys=(("east",), ("south",), ("west",)))
        query = session.observe(
            {"revenue": revenue},
            members=members if selection == "members" else None,
            where=mv.in_population(members) if selection == "where" else None,
            by=complete,
            during=session.time_scope(start="2026-08-01", end="2026-09-01"),
        )
        selected = query.select({"revenue": query["revenue"]})
        for receiver in (members, query, selected):
            receiver.show()
            card = capsys.readouterr().out
            assert "sales.customer.region" in card and "east" in card
            if isinstance(receiver, mv.Query):
                assert "south" in card and "west" in card
            assert len(card.encode()) <= 8193
        assert session.runs().items == ()

    artifact = selected.execute()
    output = artifact.to_pandas()
    # This fixture explicitly declares empty="zero"; the complete output
    # coordinates retain the two groups outside the selected member domain.
    assert output[("value", "revenue")].tolist() == [180, 0, 0]
    declarations = tuple(
        item for item in artifact.evidence_digest().items if item.kind == "declaration"
    )
    assert declarations
    scope = "\n".join(str(value) for item in declarations for _, value in item.values)
    assert "sales.customer.region" in scope and "east" in scope
    # Retained scope is a declared fact; reopening must not consult its source.
    monkeypatch.setattr(SourceSession, "__enter__", forbidden)
    monkeypatch.setattr(SourceSession, "execute", forbidden)
    assert (
        tuple(
            item
            for item in session.artifact(artifact.id).evidence_digest().items
            if item.kind == "declaration"
        )
        == declarations
    )


def test_population_scope_is_bounded_for_large_literal_and_nested_domains(
    analysis_dsl_case_factory: DslCaseFactory,
    capsys: pytest.CaptureFixture[str],
) -> None:
    case = analysis_dsl_case_factory("j2")
    entity = case.catalog.entities.get("sales.customer")
    large = mv.identities(entity, keys=tuple((f"member-{i}",) for i in range(5000)))
    large.show()
    first = capsys.readouterr().out
    large.show()
    assert capsys.readouterr().out == first
    assert "5000" in first and "member-4999" not in first
    assert len(first.encode()) <= 8193
    region = case.catalog.fields.get("sales.customer.region")
    east = case.session.members(ms.ref.entity("sales.customer"), where=region == "east")
    south = case.session.members(ms.ref.entity("sales.customer"), where=region == "south")
    domain = east.union(south).difference(south)
    domain.show()
    scope = capsys.readouterr().out
    assert "east" in scope and "south" in scope
    assert "union" in scope.lower() and "difference" in scope.lower()
    assert case.session.runs().items == ()


def test_unicode_scope_keeps_every_continuation_visible(
    analysis_dsl_case_factory: DslCaseFactory,
    capsys: pytest.CaptureFixture[str],
) -> None:
    case = analysis_dsl_case_factory("j2")
    region = case.catalog.fields.get("sales.customer.region")
    domains = [
        mv.coordinates(by=(region,), keys=tuple(("\u754c" * 600 + f"{i}-{j}",) for j in range(3)))
        for i in range(6)
    ]
    population = domains[0]
    for domain in domains[1:]:
        population = population.union(domain)
    population.show()
    card = capsys.readouterr().out
    assert "omitted" in card
    assert len(card.encode()) <= 8193
    assert "Help: analysis.population" in card


def test_output_names_and_column_show_preserve_complete_metadata(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    case = analysis_dsl_case_factory("j2")
    metric = case.catalog.metrics.get("sales.revenue")
    names = tuple(f"amount_{index}" for index in range(20))
    query = case.session.observe(dict.fromkeys(names, metric))

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("metadata disclosure accessed a source")

    monkeypatch.setattr(SourceSession, "__enter__", forbidden)
    monkeypatch.setattr(SourceSession, "execute", forbidden)
    assert query.output_names == names
    query.show()
    text = capsys.readouterr().out
    assert "outputs_omitted=12" in text and "query.output_names" in text
    column = query[query.output_names[-1]]
    assert ".show()" in repr(column)
    assert column.show() is None
    card = capsys.readouterr().out
    assert "name=amount_19" in card and "role=value" in card
    assert "kind=numeric" in card and "unit=CNY" in card
    assert "Help: analysis.column" in card
    assert case.session.runs().items == ()


def test_long_captured_output_facts_are_explicitly_omitted(
    analysis_dsl_case_factory: DslCaseFactory,
    capsys: pytest.CaptureFixture[str],
) -> None:
    case = analysis_dsl_case_factory("j2")
    name, unit = "value_" + "x" * 9000, "unit_" + "z" * 9000
    query = case.session.values(by=(), columns={name: unit}, rows=(((), {name: 1}),))
    assert query.output_names == (name,)
    query.show()
    card = capsys.readouterr().out
    assert "output[0]=omitted" in card and "query.output_names" in card
    assert len(card.encode()) <= 8192
    assert len(repr(query[name])) < 200
    query[name].show()
    card = capsys.readouterr().out
    assert "name=omitted" in card and "unit=omitted" in card
    assert len(card.encode()) <= 8192
    assert case.session.runs().items == ()
