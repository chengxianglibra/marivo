"""A complete export supplies all pandas views without further source reads or Runs."""

from typing import Literal

import pandas as pd
import pytest

from marivo.analysis.errors import AnalysisError
from marivo.datasource import adapters
from tests.analysis.statistics.deviation_oracle import forbidden
from tests.analysis.statistics.pandas_workflows import (
    assert_clean,
    deviation,
    forecast,
    orders,
    plain,
)
from tests.shared_fixtures import DslCaseFactory, run_ids


@pytest.mark.parametrize("method", ("deviation", "runs", "association", "forecast"))
def test_complete_export_supports_pandas_views_without_new_reads(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
    method: Literal["deviation", "runs", "association", "forecast"],
    capsys: pytest.CaptureFixture[str],
) -> None:
    case = analysis_dsl_case_factory("j2")
    issued: list[str] = []
    submitted: list[str] = []
    execute = adapters.SourceSession.execute

    def audited(source: adapters.SourceSession, read: adapters.CompiledRead) -> pd.DataFrame:
        assert read.sql == source._backend.compile(
            read.expression.as_table(), params=dict(read.params), limit=None
        )
        issued.append(read.sql)
        output = execute(source, read)
        submitted.append(source.submissions[-1].sql)
        return output

    monkeypatch.setattr(adapters.SourceSession, "execute", audited)
    query = orders(case)
    before = run_ids(case.session)
    fixed = query.compute()
    frame = plain(fixed)
    assert run_ids(case.session) == before
    assert_clean(case.session, artifacts=0)
    artifact = fixed.persist()
    before = run_ids(case.session)
    assert issued and issued == submitted
    assert not any(
        token in sql.lower()
        for sql in submitted
        for token in ("create macro", "bignum", "__mv_exact", "axis_concentration")
    )
    monkeypatch.setattr(adapters.SourceSession, "execute", forbidden)
    if method == "deviation":
        output = deviation(frame, "zscore")
        assert output.key_0.tolist() == frame.key_0.tolist()
        assert output.value.tolist() == frame.value.tolist()
        selected = output.loc[output.score > 0]
        assert selected.reference.nunique() == 1
        rank = selected.score.rank(method="dense", ascending=False)
    elif method == "association":
        output = frame[["value"]].astype(float).assign(other=frame.value.astype(float)).corr()
        assert output.iloc[0, 1] == 1
        rank = output.iloc[0].rank(method="dense", ascending=False)
    elif method == "forecast":
        output = forecast(frame.value)
        assert len(output) == 2
        assert (output.lower <= output.prediction).all() and (
            output.upper >= output.prediction
        ).all()
        rank = output.prediction.rank(method="dense", ascending=False)
    else:
        mask = frame.value > 0
        group = mask.ne(mask.shift(fill_value=False)).astype(int).cumsum()
        output = frame.loc[mask].groupby(group[mask]).agg(count=("value", "size"))
        assert output["count"].sum() == int(mask.sum())
        rank = output["count"].rank(method="dense", ascending=False)
    assert rank.min() == 1
    assert run_ids(case.session) == before
    assert callable(artifact.correlate)
    assert not hasattr(artifact.artifact, "correlate")
    for name in ("contract", "rollup", "deviation", "forecast", "runs", "rank"):
        assert not hasattr(artifact, name)
        assert not hasattr(artifact.artifact, name)
    for invalid in (-1, True):
        with pytest.raises(AnalysisError):
            artifact.show(n=invalid)
    fixed.show(n=0)
    artifact.show(n=3)
    assert len(capsys.readouterr().out.encode()) <= 8192
    artifact.artifact.show()
    assert len(capsys.readouterr().out.encode()) <= 8192
    assert "\n" not in repr(artifact) and ".show()" in repr(artifact)
    pd.testing.assert_frame_equal(plain(case.session.persisted_frame(artifact.artifact.id)), frame)
    assert run_ids(case.session) == before
