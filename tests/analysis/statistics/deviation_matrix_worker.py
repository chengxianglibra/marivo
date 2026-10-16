"""Public non-time domain/type/key qualifications with offline fixed kernels."""

from __future__ import annotations

from decimal import Decimal
from fractions import Fraction

import pyarrow as pa

import marivo.analysis as mv
from marivo.analysis.materialization.cell_arrow import column as cell_column
from marivo.analysis.materialization.deviation_execution import decode as _decode
from marivo.analysis.materialization.fit_state import load
from marivo.analysis.materialization.graph_protocol import descriptor_plan
from marivo.analysis.methods.deviation_numeric import DeviationMethod
from marivo.analysis.methods.physical import DecimalType, NoTime, ScalarType
from tests.analysis.statistics.deviation_oracle import PROFILES, decimal_finish, expected


def verify(
    result: mv.MaterializedDeviationResult,
    index: int,
    method: DeviationMethod,
    domain: str,
    key_profile: str,
    route: str,
) -> None:
    assert result._dataset is not None
    checked = result._dataset.verified()
    inputs, state = _decode(checked.parts)
    original, views = load(inputs.primary), load(state.views)
    ordering = [(key, "ascending") for key in inputs.keys]
    if ordering:
        original, views = original.sort_by(ordering), views.sort_by(ordering)
    scale = int(PROFILES[index].split(",")[1][:-1]) if index > 1 else 0
    raw = (
        tuple(Decimal((0, (digit,), -scale)) for digit in (1, 2, 7))
        if index > 1
        else (0.1, 0.2, 0.7)
        if index == 1
        else (1, 2, 7)
    )
    wanted = (raw[-1],) if domain == "scalar" else raw
    assert original["value"].to_pylist() == list(wanted)
    assert cell_column(original, "cell_tag").to_pylist() == ["defined"] * len(wanted)
    assert len(state.partitions) == 1 and state.partitions[0].fit.n == len(wanted)
    if domain == "entity":
        assert original[inputs.keys[0]].to_pylist() == (
            [1, 2, 3] if key_profile == "KI" else ["a", "b", "c"]
        )
        if key_profile == "KC":
            assert len(inputs.keys) == 2
            assert original[inputs.keys[1]].to_pylist() == [11, 12, 13]
    elif domain == "category":
        assert original[inputs.keys[0]].to_pylist() == ["a", "b", "c"]
    center, _, _, scores = expected(wanted, method)
    assert views["score__value"].to_pylist() == (list(scores) if len(wanted) > 1 else [None])
    if len(wanted) == 1:
        assert cell_column(views, "score__cell_reason").to_pylist() == ["insufficient_samples"]
    ref_type = views.schema.field("reference__value").type
    reference = (
        decimal_finish(center, ref_type.scale)
        if isinstance(ref_type, pa.Decimal128Type)
        else float(center)
    )
    assert views["reference__value"].to_pylist() == [reference] * len(wanted)
    assert views["deviation__value"].to_pylist() == [
        decimal_finish(Fraction(x) - center, ref_type.scale)
        if isinstance(ref_type, pa.Decimal128Type)
        else float(Fraction(x) - center)
        for x in wanted
    ]
    typ = (
        DecimalType(int(PROFILES[index].split("(")[1].split(",")[0]), scale)
        if index > 1
        else ScalarType("int64" if index == 0 else "float64")
    )
    actual = next(
        p.key
        for p in descriptor_plan(
            result._dataset.artifact.descriptor, result._node.definition
        ).physical_requirements
        if p.key.method.name == f"deviation.{method}"
    )
    assert (
        actual.input_types == (typ,)
        and actual.route == route
        and isinstance(actual.shape.time, NoTime)
    )
    assert actual.input_domains == (
        ("singleton",)
        if domain == "scalar"
        else ("group",)
        if domain == "category"
        else ("entity",)
    )
