"""Public logical contract shape, cards and native Help stay aligned."""

from dataclasses import fields
from typing import Literal

import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.datasource.adapters import SourceSession
from tests.shared_fixtures import DslCaseFactory, rendered_help


def test_contract_public_fields_and_help_snapshot() -> None:
    contract = mv.coordinates(by=(), keys=((),)).contract()
    assert tuple(field.name for field in fields(contract) if not field.name.startswith("_")) == (
        "kind",
        "actions",
        "domain",
        "quantity",
    )
    text = rendered_help("analysis.AnalysisContract")
    assert text == rendered_help(mv.AnalysisContract) == rendered_help(contract)
    assert [line for line in text.splitlines() if line.startswith("  ")] == [
        "  actions: tuple[marivo.analysis._contract.AnalysisAction, ...]",
        "  domain: <class 'str'>",
        "  kind: <class 'str'>",
        "  quantity: str | None",
    ]
    assert "Producer: Query.contract" in text and "Producer: Population.contract" in text
    assert "Consumer: AnalysisContract.show" in text


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
    contract = receiver.contract()
    assert contract.kind == kind
    assert capsys.readouterr().out == ""
    assert repr(contract) == f"<AnalysisContract kind={kind}; use .show()>"
    assert contract.show() is None
    text = capsys.readouterr().out
    assert text.splitlines()[:2] == [
        f"logical {kind}",
        "domain=singleton; quantity=1 named Metric outputs"
        if kind == "Query"
        else "domain=complete coordinates; quantity=keys only",
    ]
    assert len(text.encode("utf-8")) <= 8193
    assert all(name not in text for name in ("phase=", "required_parts", "retained_parts"))
    assert "definition=" in text and "owner=" in text
    for action in contract.actions:
        assert f"call={action.call}; help={action.help_target}\n" in text
        assert rendered_help(action.help_target)
    assert rendered_help(contract.show) == rendered_help("analysis.AnalysisContract.show")
    assert rendered_help(receiver.contract) == rendered_help(f"analysis.{kind}.contract")
    contract.show()
    assert capsys.readouterr().out == text
    assert case.session.runs().items == ()
