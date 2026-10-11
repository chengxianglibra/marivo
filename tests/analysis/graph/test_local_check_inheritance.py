"""Deferred comparison checks complete before downstream local consumers."""

from __future__ import annotations

import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.core.graph import MethodNode
from marivo.analysis.materialization.errors import MaterializationError
from tests.shared_fixtures import DslCaseFactory


@pytest.mark.parametrize("incomplete", (False, True))
def test_period_comparison_coverage_finishes_before_inherited_deviation(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
    incomplete: bool,
) -> None:
    from marivo.analysis.core.rules import DeviationFit
    from marivo.analysis.materialization import deviation_execution
    from marivo.analysis.materialization.graph_exchange import ExchangeResult
    from tests.analysis.numeric.test_period_alignment import period

    case = analysis_dsl_case_factory("j2")
    members = case.session.members(ms.ref.entity("sales.customer"))
    _, current = period(members, 8, 3)
    _, baseline = period(members, 7, 3)
    endpoint = current.where(current.value.gt(0)) if incomplete else current
    changed = endpoint.compare(baseline, align=mv.exact(time=mv.ordinal())).absolute
    scored = changed.deviation(method="zscore")
    fits: list[ExchangeResult] = []
    execute = deviation_execution.execute

    def fit(node: MethodNode, inputs: tuple[ExchangeResult, ...], binding: str) -> ExchangeResult:
        if isinstance(node.parameters, DeviationFit):
            fits.extend(inputs)
        return execute(node, inputs, binding)

    monkeypatch.setattr(deviation_execution, "execute", fit)
    if incomplete:
        with pytest.raises(MaterializationError, match="complete original period buckets"):
            scored.execute()
        assert fits == []
        assert case.session._runtime.store.resources(case.session.id) == ()
        return
    result = scored.execute()
    assert len(fits) == 1
    inherited = tuple(
        obligation
        for obligation in scored._node.root.signature.obligations
        if obligation.check_id == "source.complete_coverage@v1"
        and obligation.fact.subject_id.startswith("buckets:")
    )
    assert len(inherited) == 2
    assert result._dataset is not None
    assert all(
        any(
            proof.fact == obligation.fact
            for proof in result._dataset.artifact.descriptor.completed_checks
        )
        for obligation in inherited
    )
    expected = changed.execute().deviation(method="zscore").execute()
    assert result.score.to_pandas().equals(expected.score.to_pandas())
