"""Exact local-file consumers for the original R9.6 integer-total question."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Literal

import pyarrow as pa
import pytest

from marivo.analysis.methods import builtin
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
from marivo.datasource.adapters import SourceBatchStream
from tests.physical_workloads import _rows, workload


def test_file_cost_keys_are_exact_and_do_not_specialize_other_numeric_inputs() -> None:
    time = TimeShape("instant", "us", "UTC")
    source_routes: tuple[
        tuple[Literal["csv", "json", "parquet"], Literal["ibis", "ibis_python"]], ...
    ] = (
        ("csv", "ibis"),
        ("json", "ibis"),
        ("csv", "ibis_python"),
        ("json", "ibis_python"),
        ("parquet", "ibis_python"),
    )
    expected = {
        QualificationKey(
            MethodKey("state_rollup.sum_zero"),
            (ScalarType("int64"),),
            ("entity",),
            SourceShape("duckdb", form, form, time),
            route,
        )
        for form, route in source_routes
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
        requested = replace(entry.key, input_domains=("group",))
        assert builtin.specialize_numeric(entry, requested).key != requested
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


@pytest.mark.runtime
@pytest.mark.parametrize(
    ("profile", "route"),
    (
        ("csv", "ibis"),
        ("local-json", "ibis"),
        ("csv", "ibis_python"),
        ("local-json", "ibis_python"),
        ("parquet", "ibis_python"),
    ),
)
def test_file_cost_original_sum_executes_exact_real_file_route(
    profile: str,
    route: Literal["ibis", "ibis_python"],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    facts = _rows(1000, "baseline")
    amounts = [row["amount"] for row in facts]
    assert all(amount is None or type(amount) is int for amount in amounts)
    total = sum(amount for amount in amounts if isinstance(amount, int))
    support = sum(amount is not None for amount in amounts)
    captures: list[pa.RecordBatch] = []
    iterate = SourceBatchStream._iterate

    def captured(stream: SourceBatchStream) -> Iterator[pa.RecordBatch]:
        for batch in iterate(stream):
            if stream._submission.purpose == "analysis.domain.prepare":
                captures.append(batch)
            yield batch

    monkeypatch.setattr(SourceBatchStream, "_iterate", captured)
    with workload("duckdb", profile, 1000, "baseline", tmp_path, monkeypatch) as work:
        result = work.source(route)
        assert work.validate(result)["passed"] is True
        identity = work.identity(result)
        assert identity["root_route"] == route
        assert result._dataset is not None
        checked = result._dataset.verified()
        assert checked.primary is not None
        assert checked.primary.schema.field("value").type == pa.int64()
        assert checked.primary.to_pylist() == [
            {"value": total, "cell_tag": "defined", "cell_reason": None}
        ]
        parts = {part.role: part.table for part in checked.parts}
        assert parts["original_state"].to_pylist() == [
            {"original_state__sum": total, "original_state__non_null_count": support}
        ]
        assert parts["coverage"].to_pylist() == [{"coverage__complete": True}]
        if route == "ibis_python":
            keys = {
                (row["key_0"], row["key_1"], row["key_2"])
                for batch in captures
                if {"key_0", "key_1", "key_2"} <= set(batch.schema.names)
                for row in batch.to_pylist()
            }
            assert keys == {("a", 2**53 + index, 1) for index in range(1000)}
            assert all(
                batch.schema.field("key_1").type == pa.int64()
                and batch.schema.field("key_2").type == pa.int64()
                for batch in captures
                if {"key_0", "key_1", "key_2"} <= set(batch.schema.names)
            )
        assert work.session._runtime.store.resources(work.session.id) == ()
