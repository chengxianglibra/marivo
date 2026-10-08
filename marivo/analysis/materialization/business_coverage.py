"""Retain physical completeness and partial Cells under explicit business coverage."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

import pyarrow as pa

from marivo.analysis.core.business_coverage import (
    POLICY,
    REASON,
    complete,
    identity,
    validate_windows,
)
from marivo.analysis.core.model import CoveragePart, ObservedQuantity
from marivo.analysis.core.rules import PartsTransport
from marivo.analysis.materialization.cell_arrow import compact_schema, from_rows, logical_schema
from marivo.analysis.materialization.cell_arrow import rows as cell_rows
from marivo.analysis.materialization.execute_deadline import check

if TYPE_CHECKING:
    from marivo.analysis.compiler.graph_lowering import LoweredLocal
    from marivo.analysis.materialization.graph_exchange import (
        ExchangeContract,
        ExchangePart,
        ExchangeResult,
    )


def partial_primary(
    contract: ExchangeContract, parts: tuple[ExchangePart, ...], primary: pa.Table
) -> pa.Table:
    """Verify the owning declaration and expose partial Cells for state integrity only."""
    from marivo.analysis.materialization.graph_exchange import _invalid

    declared = next((p for p in contract.signature.parts if isinstance(p, CoveragePart)), None)
    quantity = contract.signature.quantity
    if (
        isinstance(quantity, ObservedQuantity)
        and quantity.value_policy.startswith(POLICY)
        and (declared is None or declared.business_windows is None)
    ):
        raise _invalid("business-covered exchange lacks its owning coverage declaration")
    if declared is None or declared.business_windows is None:
        return primary
    grid = contract.signature.domain.time_grid
    if (
        grid is None
        or any(c.partial for c in grid.cells)
        or not isinstance(quantity, ObservedQuantity)
        or not quantity.value_policy.startswith(POLICY)
        or quantity.method_version not in ("sum@v1", "sum_zero@v1")
        or quantity.definition_id
        != identity(quantity.value_policy.removeprefix(POLICY), declared.business_windows)
    ):
        raise _invalid("business coverage differs from its original quantity and complete grid")
    validate_windows(declared.business_windows, grid)
    position = next(
        i for i, c in enumerate(contract.signature.domain.instance_key) if c.role == "anchor"
    )
    cells = {c.identity: c for c in grid.cells}
    coverage = next(p.table for p in parts if p.role == "coverage")
    expected_types = {
        "coverage__complete": pa.bool_(),
        "coverage__business_complete": pa.bool_(),
        "coverage__partial_value": primary.schema.field("value").type,
        "coverage__partial_cell_tag": pa.string(),
        "coverage__partial_cell_reason": pa.string(),
    }
    if any(
        name not in logical_schema(coverage.schema).names
        or logical_schema(coverage.schema).field(name).type != typ
        for name, typ in expected_types.items()
    ):
        raise _invalid("business coverage lacks typed retained partial Cells")
    keyed = {tuple(r[k] for k in contract.key_fields): r for r in cell_rows(coverage)}
    original: list[dict[str, object]] = []
    for row in cell_rows(primary):
        check()
        key = tuple(row[k] for k in contract.key_fields)
        support = keyed[key]
        cell = cells.get(str(key[position]))
        if cell is None or support["coverage__complete"] is not True:
            raise _invalid("business coverage cannot substitute for an actual complete source read")
        known = complete(cell, declared.business_windows)
        if support["coverage__business_complete"] is not known:
            raise _invalid("business completeness differs from the original bucket boundaries")
        partial = {
            field: support["coverage__partial_" + field]
            for field in ("value", "cell_tag", "cell_reason")
        }
        if partial["cell_tag"] not in ("defined", "null"):
            raise _invalid("business partial Cell is not an original sum observation")
        expected = (
            partial if known else {"value": None, "cell_tag": "unknown", "cell_reason": REASON}
        )
        if any(row[field] != value for field, value in expected.items()):
            raise _invalid("business coverage and primary Cell disagree")
        original.append({**row, **partial})
    return from_rows(original, primary.schema)


def produce(method: LoweredLocal, source: ExchangeResult, input_binding: str) -> ExchangeResult:
    from marivo.analysis.materialization.graph_exchange import (
        ExchangePart,
        PartContract,
        _invalid,
        from_arrow,
    )

    params = method.stage.node.parameters
    assert isinstance(params, PartsTransport) and params.business_windows is not None
    grid = source.contract.signature.domain.time_grid
    assert grid is not None
    position = next(
        i for i, c in enumerate(source.contract.signature.domain.instance_key) if c.role == "anchor"
    )
    cells = {c.identity: c for c in grid.cells}
    rows = cell_rows(source.primary)
    known: list[bool] = []
    for row in rows:
        check()
        known.append(
            complete(cells[str(row[source.contract.key_fields[position]])], params.business_windows)
        )
    reasons = dict(source.contract.cell_reasons)
    reasons["unknown"] = (*reasons.get("unknown", ()), REASON)
    owned_reasons = tuple(reasons.items())
    primary = from_rows(
        [
            row if covered else {**row, "value": None, "cell_tag": "unknown", "cell_reason": REASON}
            for row, covered in zip(rows, known, strict=True)
        ],
        compact_schema(source.primary.schema, owned_reasons),
    )
    previous = next(p.table for p in source.parts if p.role == "coverage")
    if any(value is not True for value in previous["coverage__complete"].to_pylist()):
        raise _invalid("business declaration received an incomplete source read")
    keyed = {
        tuple(r[k] for k in source.contract.key_fields): (r, covered)
        for r, covered in zip(rows, known, strict=True)
    }
    support_rows = cell_rows(previous)
    support = previous.append_column(
        "coverage__business_complete",
        pa.array(
            [keyed[tuple(r[k] for k in source.contract.key_fields)][1] for r in support_rows],
            type=pa.bool_(),
        ),
    )
    for field in ("value", "cell_tag", "cell_reason"):
        support = support.append_column(
            "coverage__partial_" + field,
            pa.array(
                [
                    keyed[tuple(r[k] for k in source.contract.key_fields)][0][field]
                    for r in support_rows
                ],
                type=logical_schema(source.primary.schema).field(field).type,
            ),
        )
    parts = tuple(
        ExchangePart("coverage", support) if p.role == "coverage" else p for p in source.parts
    )
    contract = replace(
        source.contract,
        signature=method.stage.node.signature,
        method=method.stage.node.method,
        input_binding=input_binding,
        schema=primary.schema,
        parts=tuple(
            PartContract(p.role, p.table.schema, prior.key_fields)
            for p, prior in zip(parts, source.contract.parts, strict=True)
        ),
        cell_reasons=tuple(reasons.items()),
        state_kind="none",
        state_schema=None,
    )
    return from_arrow(primary, contract, parts=parts, validate=False)
