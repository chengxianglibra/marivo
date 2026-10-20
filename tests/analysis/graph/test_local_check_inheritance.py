"""Period key checks finish before downstream Frame result consumers."""

from __future__ import annotations

from typing import Literal

import pandas as pd
import pytest

import marivo.analysis as mv
from marivo._definition.query import ReduceValues
from marivo.analysis.compiler.query_program import FrameProgram
from marivo.analysis.errors import AnalysisError
from tests.shared_fixtures import DslCaseFactory


@pytest.mark.parametrize("incomplete", (False, True))
@pytest.mark.parametrize("prune", ("reduce", "select", "where", "top"))
def test_period_comparison_coverage_finishes_before_inherited_reduction(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
    incomplete: bool,
    prune: Literal["reduce", "select", "where", "top"],
) -> None:
    from marivo.analysis.methods import frame_values
    from tests.analysis.numeric.test_period_alignment import period

    case = analysis_dsl_case_factory("j2")
    _, current = period(case.session, 8, 3)
    _, baseline = period(case.session, 7, 3)
    endpoint = current.where(current["value"] > 0) if incomplete else current
    paired = endpoint.compare(
        {"reference": baseline},
        match={"reference": mv.ordinal()},
        on={"reference": tuple(zip(endpoint.axes, baseline.axes, strict=True))},
    )
    changed = paired.select({"value": paired["reference.absolute.value"]})
    selected = changed
    if prune == "select":
        selected = changed.select({"value": changed["value"]})
    elif prune == "where":
        selected = changed.where(changed["value"] > 1000)
    elif prune == "top":
        selected = changed.top(0, by=changed["value"])
    reduced = selected.reduce({"total": selected["value"].sum(), "rows": mv.row_count()})
    consumed: list[tuple[pd.DataFrame, ...]] = []
    execute = frame_values.evaluate_frame

    def traced(stage: FrameProgram, inputs: tuple[pd.DataFrame, ...]) -> pd.DataFrame:
        if isinstance(stage.operation, ReduceValues):
            consumed.append(inputs)
        return execute(stage, inputs)

    monkeypatch.setattr(frame_values, "evaluate_frame", traced)
    if incomplete:
        with pytest.raises(AnalysisError, match="equal actual complete row-key domains"):
            reduced.compute()
        assert consumed == []
        assert case.session._runtime.store.resources(case.session.id) == ()
        return
    frame = reduced.compute().to_pandas()
    assert len(consumed) == 1
    if prune in ("where", "top"):
        assert frame[("value", "total")].isna().all()
        assert frame[("value", "rows")].tolist() == [0]
    else:
        assert frame[("value", "total")].tolist() == [1]
        assert frame[("value", "rows")].tolist() == [12]
    original = changed.compute().to_pandas()
    assert original[("value", "value")].sum() == 1 and len(original) == 12
