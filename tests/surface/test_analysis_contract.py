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
    assert not hasattr(mv.AnalysisFrame, "contract")


@pytest.mark.parametrize("kind", ("AnalysisFrame", "Population"))
def test_contract_cards_and_actions_without_source_reads_or_runs(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    kind: Literal["AnalysisFrame", "Population"],
) -> None:
    case = analysis_dsl_case_factory("j2")
    metric = case.catalog.metrics.get("sales.revenue")

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("logical contract opened or read a source")

    monkeypatch.setattr(SourceSession, "__enter__", forbidden)
    monkeypatch.setattr(SourceSession, "execute", forbidden)
    population = case.session.members(ms.ref.entity("sales.customer"))
    receiver = case.session.observe({"revenue": metric}) if kind == "AnalysisFrame" else population
    assert capsys.readouterr().out == ""
    assert ".show()" in repr(receiver)
    assert receiver.show() is None
    text = capsys.readouterr().out
    assert text.startswith("<AnalysisFrame" if kind == "AnalysisFrame" else "logical Population\n")
    assert len(text.encode("utf-8")) <= 8193
    if kind == "Population":
        assert "definition=" in text and "owner=" in text
    else:
        assert "operation" in text and "inputs" in text and "pending" in text
    assert "call=" not in text
    target = (
        "analysis.analysis_frame.show" if kind == "AnalysisFrame" else "analysis.population.show"
    )
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
            if isinstance(receiver, mv.AnalysisFrame):
                assert "south" in card and "west" in card
            assert len(card.encode()) <= 8193
        assert session.runs().items == ()

    saved = selected.persist()
    artifact = saved.artifact
    output = saved.to_pandas()
    # This fixture explicitly declares empty="zero"; the complete output
    # coordinates retain the two groups outside the selected member domain.
    assert output[("value", "revenue")].tolist() == [180, 0, 0]
    reads = tuple(item for item in artifact.evidence_digest().items if item.kind == "read")
    assert reads
    scoped_reads = tuple(
        item for item in reads if {"source_scopes", "filters"} <= dict(item.values).keys()
    )
    assert scoped_reads
    scope = "\n".join(
        str(value)
        for item in scoped_reads
        for name, value in item.values
        if name in ("source_scopes", "filters")
    )
    assert all(value in scope for value in ("sales.customer.region", "east", "south", "west"))
    # These are captured read facts, not a reusable producer declaration.
    # Reopening must preserve them exactly without consulting their source.
    monkeypatch.setattr(SourceSession, "__enter__", forbidden)
    monkeypatch.setattr(SourceSession, "execute", forbidden)
    assert (
        tuple(
            item
            for item in session.persisted_frame(artifact.id).artifact.evidence_digest().items
            if item.kind == "read"
        )
        == reads
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
    assert all(name in text for name in names)
    column = query[query.output_names[-1]]
    assert ".show()" in repr(column)
    assert column.show() is None
    card = capsys.readouterr().out
    assert "name=amount_19" in card and "role=value" in card
    assert "kind=unresolved" in card and "unit=CNY" in card
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
    assert "omitted" in card and "output_names" in card
    assert len(card.encode()) <= 8192
    assert len(repr(query[name])) < 200
    query[name].show()
    card = capsys.readouterr().out
    assert "name=omitted" in card and "unit=omitted" in card
    assert len(card.encode()) <= 8192
    assert case.session.runs().items == ()


def test_frame_and_audit_cards_advertise_only_actual_state_continuations(
    analysis_dsl_case_factory: DslCaseFactory,
    capsys: pytest.CaptureFixture[str],
) -> None:
    case = analysis_dsl_case_factory("j2")
    pending = case.session.values(by=(), columns={"value": None}, rows=(((), {"value": 1}),))
    pending.show(n=0)
    card = capsys.readouterr().out
    assert ".compute()" in card and ".persist()" in card
    assert ".to_pandas()" not in card and ".artifact" not in card
    fixed = pending.compute()
    fixed.show(n=0)
    card = capsys.readouterr().out
    assert ".to_pandas()" in card and ".persist()" in card and ".artifact" not in card
    saved = fixed.persist()
    saved.show(n=0)
    card = capsys.readouterr().out
    assert ".to_pandas()" in card and ".artifact" in card and ".compute()" not in card
    saved.artifact.show()
    audit = capsys.readouterr().out
    assert f"session.persisted_frame({saved.artifact.id!r}).show(n=20)" in audit
    assert "Artifact.to_pandas" not in audit and "artifact.show(n=" not in audit
    case.session.show()
    recap = capsys.readouterr().out
    assert f".persisted_frame({saved.artifact.id!r}).artifact" in recap
    assert ".artifact(ref)" not in recap
