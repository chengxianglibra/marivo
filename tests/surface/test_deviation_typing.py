"""Native Query typing and the explicit terminal-to-pandas statistics boundary."""

from pathlib import Path

import pytest

from tests.support.typing import check

pytestmark = pytest.mark.slow


def test_deviation_and_statistics_public_contracts(tmp_path: Path) -> None:
    source = tmp_path / "statistics_contracts.py"
    body = """import marivo.analysis as mv
import pandas as pd
from ibis.common.deferred import Deferred
from typing_extensions import assert_type

def positive(session: mv.Session, members: mv.Population, field: Deferred, other: mv.Query, artifact: mv.Artifact) -> None:
    query = session.observe({"value": field.mean()}, by=members)
    assert_type(query, mv.Query)
    assert_type(query["value"], mv.Column)
    assert_type(query.where(query["value"] > 0), mv.Query)
    assert_type(query.select({"centered": query["value"] - 10}), mv.Query)
    assert_type(query.top(3, by=query["value"]), mv.Query)
    assert_type(query.reduce({"mean": query["value"].mean(), "sum": query["value"].sum(), "rows": mv.row_count()}), mv.Query)
    assert_type(query.compare(other), mv.Query)
    assert_type(query.align(other), mv.Query)
    assert_type(query.execute(), mv.Artifact)
    frame = artifact.to_pandas()
    assert_type(frame, pd.DataFrame)
    assert_type(frame.corr(method="pearson", numeric_only=True), pd.DataFrame)
    assert_type(frame.corr(method="spearman", numeric_only=True), pd.DataFrame)
    assert_type(frame.corr(method="kendall", numeric_only=True), pd.DataFrame)
    assert_type(frame.describe(), pd.DataFrame)

def rejected(session: mv.Session, members: mv.Population, query: mv.Query, other: mv.Query, artifact: mv.Artifact, field: Deferred) -> None:
"""
    calls = (
        "query.deviation(method='zscore')",
        "query.deviation(method='mad', partition_by=(field,))",
        "query.runs(where=query['value'] > 0)",
        "query.correlate(other, method='pearson')",
        "query.forecast(horizon=2)",
        "artifact.execute()",
        "artifact.where(query['value'] > 0)",
        "artifact.reduce({'value': query['value'].mean()})",
        "artifact.forecast(horizon=2)",
        "query.reduce({'value': query['value']})",
        "query.reduce({'value': query['value'].mean()}, by=[field])",
        "query.reduce({'value': query['value'].mean()}, by=(query['value'],))",
        "query.where(pd.Series([True]))",
        "query.select({'value': artifact})",
        "query.top(1, by='value')",
        "session.observe({'value': artifact.to_pandas()}, by=members)",
        "mv.broadcast(artifact.to_pandas(), to=query)",
    )
    body += "".join(f"    {call}\n" for call in calls)
    source.write_text(body)
    result = check(source)
    assert result.returncode != 0
    errors = [line for line in result.stdout.splitlines() if ": error:" in line]
    error_locations = {line.split(": error:", maxsplit=1)[0] for line in errors}
    expected = {f"{source}:{body.splitlines().index('    ' + call) + 1}" for call in calls}
    assert error_locations == expected, result.stdout + result.stderr
    assert len(errors) == len(calls), result.stdout + result.stderr
