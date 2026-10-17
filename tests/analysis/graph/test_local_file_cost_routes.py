"""Exact local-file consumers for the original integer-total question."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Literal

import pandas as pd
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.core.data_types import ScalarDataType
from marivo.analysis.methods import builtin
from marivo.analysis.methods.errors import MethodRegistrationError
from marivo.analysis.methods.physical import (
    DecimalType,
    NoTime,
    QualificationKey,
    Qualified,
    ScalarName,
    ScalarType,
    SourceShape,
    TimeShape,
)
from marivo.analysis.methods.semantics import MethodKey
from marivo.datasource.adapters import CompiledRead, SourceSession
from tests.analysis.graph.physical_workloads import _rows, workload


def test_file_cost_keys_are_exact_and_do_not_specialize_other_numeric_inputs() -> None:
    time = TimeShape("instant", "us", "UTC")
    source_routes: tuple[
        tuple[Literal["csv", "json", "parquet"], Literal["ibis", "ibis_python"]], ...
    ] = (
        ("csv", "ibis"),
        ("json", "ibis"),
    )
    expected = {
        QualificationKey(
            MethodKey("state_rollup.sum_zero"),
            (ScalarType("int64"),),
            (kind,),
            SourceShape("duckdb", form, form, time),
            route,
        )
        for form, route in source_routes
        for kind in ("entity", "group", "singleton")
    }
    expected.update(
        QualificationKey(
            MethodKey(name),
            (ScalarType("int64" if name == "deviation.zscore" else "float64"),),
            ("entity",),
            SourceShape("duckdb", form, form, NoTime()),
            "ibis_python",
        )
        for name in ("deviation.zscore", "deviation.read")
        for form in ("csv", "json")
    )
    entries = tuple(
        entry
        for name in ("state_rollup.sum_zero", "deviation.zscore", "deviation.read")
        for entry in builtin.implementations(MethodKey(name))
        if isinstance(entry.qualification, Qualified)
        and entry.qualification.implementation_id.startswith("r96.local_file.")
    )
    assert {entry.key for entry in entries} == expected
    assert len(entries) == len(expected)
    for entry in entries:
        assert isinstance(entry.qualification, Qualified)
        assert entry.qualification.evidence_id == "tests/test_r96_local_file_cost_routes.py"
        other_scalar: ScalarName = (
            "int64" if entry.key.method.name == "deviation.read" else "float64"
        )
        for typ in (ScalarType(other_scalar), DecimalType(20, 2)):
            requested = replace(entry.key, input_types=(typ,))
            assert builtin.specialize_numeric(entry, requested).key != requested
        with pytest.raises(MethodRegistrationError):
            replace(entry.key, input_domains=("occurrence",))  # type: ignore[arg-type]  # Reject an unsupported domain at the runtime boundary.
    for name in (
        "deviation.mad",
        "metric.mean",
        "cell.difference",
        "attribution.additive_difference",
    ):
        assert not any(
            isinstance(entry.key.shape, SourceShape) and entry.key.shape.form in ("csv", "json")
            for entry in builtin.implementations(MethodKey(name))
        )


@pytest.mark.parametrize(
    ("profile", "composition"),
    (
        ("csv", "source"),
        ("local-json", "source"),
        ("csv", "rollup"),
        ("local-json", "rollup"),
        ("parquet", "rollup"),
    ),
)
def test_file_cost_original_sum_executes_exact_real_file_route(
    profile: str,
    composition: Literal["source", "rollup"],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    facts = _rows(4, "baseline")
    amounts = [row["amount"] for row in facts]
    assert all(amount is None or type(amount) is int for amount in amounts)
    total = sum(amount for amount in amounts if isinstance(amount, int))
    captures: list[tuple[CompiledRead, pd.DataFrame]] = []
    execute = SourceSession.execute

    def captured(source: SourceSession, read: CompiledRead) -> pd.DataFrame:
        frame = execute(source, read)
        captures.append((read, frame))
        return frame

    monkeypatch.setattr(SourceSession, "execute", captured)
    with workload("duckdb", profile, len(facts), "baseline", tmp_path, monkeypatch) as work:
        values = work.values()
        query = work.total() if composition == "source" else values.rollup()
        result = query.execute()
        assert work.validate(result)["passed"] is True
        assert result.to_pandas()[("value", "value")].tolist() == [total]
        assert captures and any("SUM(" in read.sql.upper() for read, _ in captures)
        observed = values.execute()
        frame = observed.to_pandas()
        assert set(frame.iloc[:, 0]) == {("a", 2**53 + index, 1) for index in range(len(facts))}
        assert frame[("value", "value")].tolist() == [0, 2, 3, 4]
        raw = work.session.read(
            work.session.members(ms.ref.entity("cost.facts")),
            {"amount": work.session.catalog.fields.get("cost.facts.amount")},
        )
        support = raw.where(~raw["amount"].is_null()).reduce({"support": mv.row_count()})
        assert support.execute().to_pandas()[("value", "support")].tolist() == [3]
        assert [column.dtype for column in observed._manifest.data.schema.axes[0].columns] == [
            ScalarDataType("string"),
            ScalarDataType("int64"),
            ScalarDataType("int64"),
        ]
        assert work.session._runtime.store.resources(work.session.id) == ()
