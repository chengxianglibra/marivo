"""Public Decimal laws through a source producer, offline fixed and cold recovery."""

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
from marivo.analysis.methods.physical import DecimalType, NoTime
from tests.analysis.statistics.deviation_oracle import decimal_finish, expected


def verify(
    result: mv.MaterializedDeviationResult,
    precision: int,
    scale: int,
    method: DeviationMethod,
    route: str,
) -> None:
    assert result._dataset is not None
    checked = result._dataset.verified()
    inputs, state = _decode(checked.parts)
    ordering = [(key, "ascending") for key in inputs.keys]
    original = load(inputs.primary).sort_by(ordering)
    views = load(state.views).sort_by(ordering)
    values = tuple(Decimal((0, (digit,), -scale)) for digit in (1, 2, 7))
    assert original[inputs.keys[0]].to_pylist() == ["law_a", "law_b", "law_c", "law_null"]
    assert len(inputs.keys) == 2
    assert original[inputs.keys[1]].to_pylist() == [1, 2, 3, 4]
    assert original.schema.field(inputs.keys[0]).type == pa.string()
    assert original.schema.field(inputs.keys[1]).type == pa.int64()
    assert cell_column(original, "cell_tag").to_pylist() == ["defined"] * 3 + ["null"]
    assert original["value"].to_pylist() == [*values, None]
    assert original.schema.field("value").type == pa.decimal128(precision, scale)
    center, _, _, scores = expected(values, method)
    assert views["score__value"].to_pylist()[:3] == list(scores)
    assert views["reference__value"].to_pylist() == [decimal_finish(center, max(scale, 6))] * 4
    assert views["deviation__value"].to_pylist() == [
        *(decimal_finish(Fraction(x) - center, max(scale, 6)) for x in values),
        None,
    ]
    assert views.schema.field("reference__value").type == pa.decimal128(38, max(scale, 6))
    assert sum(len(partition.indices) for partition in state.partitions) == 4
    assert result.evidence_digest().finding_count == 0
    plan = descriptor_plan(result._dataset.artifact.descriptor, result._node.definition)
    actual = next(
        p.key for p in plan.physical_requirements if p.key.method.name == f"deviation.{method}"
    )
    assert actual.input_types == (DecimalType(precision, scale),)
    assert actual.input_domains == ("entity",) and isinstance(actual.shape.time, NoTime)
    assert actual.route == route
