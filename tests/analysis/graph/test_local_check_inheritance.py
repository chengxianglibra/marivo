"""Period key checks finish before downstream Query result consumers."""

from __future__ import annotations

import pandas as pd
import pytest

import marivo.analysis as mv
from marivo._definition.query import ReduceValues
from marivo.analysis.compiler.graph_plan import LocalCompute
from marivo.analysis.errors import AnalysisError
from tests.shared_fixtures import DslCaseFactory


@pytest.mark.parametrize("incomplete", (False, True))
def test_period_comparison_coverage_finishes_before_inherited_reduction(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
    incomplete: bool,
) -> None:
    from marivo.analysis.methods import query_values
    from tests.analysis.numeric.test_period_alignment import period

    case = analysis_dsl_case_factory("j2")
    _, current = period(case.session, 8, 3)
    _, baseline = period(case.session, 7, 3)
    endpoint = current.where(current["value"] > 0) if incomplete else current
    paired = endpoint.compare(baseline, match=mv.ordinal())
    changed = paired.select({"value": paired["value.absolute"]})
    reduced = changed.reduce({"total": changed["value"].sum(), "rows": mv.row_count()})
    consumed: list[tuple[pd.DataFrame, ...]] = []
    execute = query_values.evaluate_result

    def traced(stage: LocalCompute, inputs: tuple[pd.DataFrame, ...]) -> pd.DataFrame:
        if stage.result is not None and isinstance(stage.result.operation, ReduceValues):
            consumed.append(inputs)
        return execute(stage, inputs)

    monkeypatch.setattr(query_values, "evaluate_result", traced)
    if incomplete:
        with pytest.raises(AnalysisError, match="equal complete endpoint key images"):
            reduced.execute()
        assert consumed == []
        assert case.session._runtime.store.resources(case.session.id) == ()
        return
    frame = reduced.execute().to_pandas()
    assert len(consumed) == 1
    assert frame[("value", "total")].tolist() == [1]
    assert frame[("value", "rows")].tolist() == [12]
    original = changed.execute().to_pandas()
    assert original[("value", "value")].sum() == 1 and len(original) == 12
