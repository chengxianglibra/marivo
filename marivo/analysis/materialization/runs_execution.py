"""Complete-grid condition capture, maximal runs and offline checked projections."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from math import isfinite
from typing import Literal

import pyarrow as pa
from pydantic import TypeAdapter, ValidationError

from marivo.analysis.core.graph import MethodNode
from marivo.analysis.core.model import ConditionCellsPart, RunCellsPart, Signature, part_role
from marivo.analysis.core.predicates import ValuePredicate, compose, leaves
from marivo.analysis.core.rules import TimeRunRead, TimeRuns
from marivo.analysis.materialization.contracts import canonical_json
from marivo.analysis.materialization.deviation_execution import SavedTable, load, save
from marivo.analysis.materialization.execute_deadline import check
from marivo.analysis.materialization.graph_exchange import (
    ExchangeContract,
    ExchangePart,
    ExchangeResult,
    PartContract,
    from_arrow,
)
from marivo.analysis.materialization.graph_protocol import invalid
from marivo.analysis.methods.physical import ValueType, matches_arrow_scalar


@dataclass(frozen=True, slots=True)
class Capture:
    signature: Signature
    run_id: str
    predicate: ValuePredicate
    keys: tuple[str, ...]
    inputs: tuple[SavedTable, ...]
    coverage: SavedTable
    subject: SavedTable | None
    input_bindings: tuple[str, ...]
    input_signatures: tuple[Signature, ...]
    value_types: tuple[ValueType, ...]
    input_reasons: tuple[tuple[tuple[str, tuple[str, ...]], ...], ...]
    version: Literal["r8.condition_cells/v1"] = "r8.condition_cells/v1"


@dataclass(frozen=True, slots=True)
class Runs:
    input_digest: str
    views: SavedTable
    classifications: tuple[Literal["true", "false", "unavailable"], ...]
    reasons: tuple[tuple[str, ...], ...]
    version: Literal["r8.run_cells/v1"] = "r8.run_cells/v1"


CAPTURE = TypeAdapter(Capture)
RUNS = TypeAdapter(Runs)


_VIEW_SCHEMA = pa.schema(
    [
        ("key_0", pa.string()),
        ("start", pa.timestamp("us", "UTC")),
        ("end", pa.timestamp("us", "UTC")),
        ("count", pa.int64()),
        ("duration", pa.duration("us")),
        ("cells", pa.list_(pa.string())),
        ("input_rows", pa.list_(pa.int64())),
        ("left_kind", pa.string()),
        ("right_kind", pa.string()),
        ("left_cell", pa.string()),
        ("right_cell", pa.string()),
    ]
)


def _index(table: pa.Table, keys: tuple[str, ...]) -> dict[tuple[object, ...], int]:
    result: dict[tuple[object, ...], int] = {}
    for i, row in enumerate(table.to_pylist()):
        key = tuple(row[k] for k in keys)
        if key in result or any(x is None for x in key):
            raise invalid("runs input has null or duplicate complete coordinates")
        result[key] = i
    return result


def _classify(
    predicate: ValuePredicate, rows: tuple[dict[str, object], ...]
) -> tuple[Literal["true", "false", "unavailable"], tuple[str, ...]]:
    truth: list[bool | None] = []
    reasons: set[str] = set()
    for leaf in leaves(predicate):
        row = rows[leaf.input_index]
        if leaf.operator == "is_defined":
            truth.append(row["cell_tag"] == "defined")
            continue
        operands = (row,) if leaf.right_index is None else (row, rows[leaf.right_index])
        unavailable = tuple(r for r in operands if r["cell_tag"] != "defined")
        if unavailable:
            reasons.update(str(r["cell_reason"]) for r in unavailable)
            truth.append(None)
        else:
            left = row["value"]
            right = leaf.literal if leaf.right_index is None else rows[leaf.right_index]["value"]
            if not isinstance(left, (int, float, Decimal, str, date)) or not isinstance(
                right, (int, float, Decimal, str, date)
            ):
                raise invalid("run predicate has an unsupported defined value")
            if isinstance(left, (int, float, Decimal)) and isinstance(right, (int, float, Decimal)):
                truth.append(
                    left == right
                    if leaf.operator == "eq"
                    else left != right
                    if leaf.operator == "ne"
                    else left < right
                    if leaf.operator == "lt"
                    else left <= right
                    if leaf.operator == "le"
                    else left > right
                    if leaf.operator == "gt"
                    else left >= right
                )
            elif isinstance(left, str) and isinstance(right, str):
                truth.append(left == right)
            elif (isinstance(left, datetime) and isinstance(right, datetime)) or (
                isinstance(left, date) and isinstance(right, date)
            ):
                truth.append(
                    left == right
                    if leaf.operator == "eq"
                    else left < right
                    if leaf.operator == "lt"
                    else left > right
                    if leaf.operator == "gt"
                    else left <= right
                    if leaf.operator == "le"
                    else left >= right
                )
            else:
                raise invalid("run predicate has incompatible carriers")
    if None in truth:
        return "unavailable", tuple(sorted(reasons))
    return ("true" if compose(predicate, tuple(truth)) else "false"), ()


def _condition_scope(
    capture: Capture,
) -> tuple[
    tuple[Literal["true", "false", "unavailable"], ...],
    tuple[tuple[str, ...], ...],
    dict[tuple[object, ...], dict[str, int]],
    tuple[str, ...],
]:
    check()
    grid = capture.signature.domain.time_grid
    if grid is None or any(c.partial for c in grid.cells):
        raise invalid("runs requires the original non-partial complete grid")
    position = next(
        i for i, c in enumerate(capture.signature.domain.instance_key) if c.role == "anchor"
    )
    tables = tuple(load(t) for t in capture.inputs)
    if (
        len(tables) != len(capture.input_bindings)
        or len(tables) != len(capture.input_signatures)
        or len(tables) != len(capture.value_types)
        or len(tables) != len(capture.input_reasons)
        or not tables
    ):
        raise invalid(
            "runs lacks an exact binding, signature or type for every condition dependency"
        )
    if capture.predicate.binding != capture.signature.domain.binding or any(
        leaf.input_index >= len(tables)
        or (leaf.right_index is not None and leaf.right_index >= len(tables))
        for leaf in leaves(capture.predicate)
    ):
        raise invalid("runs condition binding or dependency position escapes its capture")
    for table, signature, typ, binding, cell_reasons in zip(
        tables,
        capture.input_signatures,
        capture.value_types,
        capture.input_bindings,
        capture.input_reasons,
        strict=True,
    ):
        if (
            not binding
            or signature.domain != capture.signature.domain
            or not matches_arrow_scalar(table.schema.field("value").type, typ)
        ):
            raise invalid("runs dependency type or original domain differs from its capture")
        allowed_reasons = dict(cell_reasons)
        for row in table.to_pylist():
            check()
            value, tag, reason = row["value"], row["cell_tag"], row["cell_reason"]
            if tag == "defined":
                if (
                    value is None
                    or reason is not None
                    or (isinstance(value, float) and not isfinite(value))
                    or (isinstance(value, Decimal) and not value.is_finite())
                ):
                    raise invalid("runs input has a malformed or non-finite Defined Cell")
            elif (
                tag not in ("null", "undefined", "unknown")
                or value is not None
                or reason not in allowed_reasons.get(tag, ())
            ):
                raise invalid("runs input has a malformed Cell or unowned unavailable reason")

    indices = tuple(_index(t, capture.keys) for t in tables)
    keys = tuple(indices[0])
    if any(set(index) != set(keys) for index in indices[1:]):
        raise invalid("runs condition dependencies have different complete keys")
    coverage = load(capture.coverage)
    if set(_index(coverage, capture.keys)) != set(keys) or any(
        x is not True for x in coverage["complete"].to_pylist()
    ):
        raise invalid("runs original grid coverage is absent or false")
    semantic_inputs = tuple(
        hashlib.sha256(
            save(
                table.take(
                    pa.array([index[key] for key in sorted(keys, key=repr)], type=pa.int64())
                ).combine_chunks()
            ).data.encode()
        ).hexdigest()
        for table, index in zip(tables, indices, strict=True)
    )
    rows = tuple(t.to_pylist() for t in tables)
    classes: list[Literal["true", "false", "unavailable"]] = []
    reasons = []
    for key in keys:
        check()
        classification, reason = _classify(
            capture.predicate, tuple(r[index[key]] for r, index in zip(rows, indices, strict=True))
        )
        classes.append(classification)
        reasons.append(reason)
    series: dict[tuple[object, ...], dict[str, int]] = {}
    for i, key in enumerate(keys):
        sequence = key[:position] + key[position + 1 :]
        series.setdefault(sequence, {})[str(key[position])] = i
    expected = {c.identity for c in grid.cells}
    if any(set(cells) != expected for cells in series.values()):
        raise invalid("runs series has missing or extra original grid cells")
    return tuple(classes), tuple(reasons), series, semantic_inputs


def compute(
    capture: Capture,
) -> tuple[
    pa.Table, tuple[Literal["true", "false", "unavailable"], ...], tuple[tuple[str, ...], ...]
]:
    classes, reasons, series, semantic_inputs = _condition_scope(capture)
    grid = capture.signature.domain.time_grid
    assert grid is not None
    output: list[dict[str, object]] = []
    for sequence, cells in sorted(series.items(), key=lambda item: repr(item[0])):
        start = None
        for ordinal in range(len(grid.cells) + 1):
            check()
            active = (
                ordinal < len(grid.cells) and classes[cells[grid.cells[ordinal].identity]] == "true"
            )
            if active and start is None:
                start = ordinal
            if not active and start is not None:
                first, last = grid.cells[start], grid.cells[ordinal - 1]
                delta = last.end - first.start
                ticks = (delta.days * 86400 + delta.seconds) * 1000000 + delta.microseconds
                if not 0 < ticks < 2**63 or ordinal - start >= 2**63:
                    raise invalid("run Duration/count exceeds checked int64")
                identity = hashlib.sha256(
                    canonical_json(
                        [
                            capture.run_id,
                            capture.input_bindings,
                            semantic_inputs,
                            sequence,
                            grid.identity,
                            first.identity,
                            last.identity,
                        ]
                    ).encode()
                ).hexdigest()

                def termination(at: int, mapping: dict[str, int] = cells) -> str:
                    return (
                        "scope_boundary"
                        if at < 0 or at == len(grid.cells)
                        else classes[mapping[grid.cells[at].identity]]
                    )

                output.append(
                    {
                        "key_0": identity,
                        "start": first.start,
                        "end": last.end,
                        "count": ordinal - start,
                        "duration": ticks,
                        "cells": [grid.cells[j].identity for j in range(start, ordinal)],
                        "input_rows": [
                            cells[grid.cells[j].identity] for j in range(start, ordinal)
                        ],
                        "left_kind": termination(start - 1),
                        "right_kind": termination(ordinal),
                        "left_cell": None if start == 0 else grid.cells[start - 1].identity,
                        "right_cell": None
                        if ordinal == len(grid.cells)
                        else grid.cells[ordinal].identity,
                    }
                )
                start = None
    return pa.Table.from_pylist(output, schema=_VIEW_SCHEMA), tuple(classes), tuple(reasons)


def _primary(views: pa.Table, field: str) -> pa.Table:
    return pa.table(
        {
            "key_0": views["key_0"],
            "value": views[field],
            "cell_tag": pa.array(["defined"] * len(views), type=pa.string()),
            "cell_reason": pa.array([None] * len(views), type=pa.string()),
        }
    )


def _payload(role: str, value: str) -> ExchangePart:
    return ExchangePart(role, pa.table({role + "__retained": [value]}))


def _decode(parts: tuple[ExchangePart, ...]) -> tuple[Capture, Runs]:
    by_role = {p.role: p for p in parts}
    try:
        capture = CAPTURE.validate_json(
            by_role["condition_cells"].table["condition_cells__retained"][0].as_py()
        )
        state = RUNS.validate_json(by_role["run_cells"].table["run_cells__retained"][0].as_py())
    except (KeyError, IndexError, ValidationError) as error:
        raise invalid("runs lacks valid v1 condition_cells/run_cells payloads") from error
    return capture, state


def _result(
    node: MethodNode,
    primary: pa.Table,
    parts: tuple[ExchangePart, ...],
    binding: str,
    *,
    validate: bool = True,
) -> ExchangeResult:
    return from_arrow(
        primary,
        ExchangeContract(
            node.signature,
            node.method,
            binding,
            primary.schema,
            ("key_0",),
            tuple(
                PartContract(
                    p.role,
                    p.table.schema,
                    ()
                    if p.role in ("condition_cells", "run_cells", "finding_policy")
                    else tuple(n for n in p.table.column_names if n.startswith("key_")),
                )
                for p in parts
            ),
        ),
        parts=parts,
        validate=validate,
    )


def execute(node: MethodNode, values: tuple[ExchangeResult, ...], binding: str) -> ExchangeResult:
    params = node.parameters
    if isinstance(params, TimeRunRead):
        capture, state = _decode(values[0].parts)
        views = load(state.views)
        index = _index(views, ("key_0",))
        selected = views.take(
            pa.array([index[k] for k in _index(values[0].primary, ("key_0",))], type=pa.int64())
        )
        return _result(
            node, _primary(selected, params.field), values[0].parts, binding, validate=False
        )
    assert isinstance(params, TimeRuns)
    source = values[0]
    coverage = next((p.table for p in source.parts if p.role == "coverage"), None)
    if coverage is not None:
        coverage = coverage.select(
            [*source.contract.key_fields, "coverage__complete"]
        ).rename_columns([*source.contract.key_fields, "complete"])
    else:
        mapping = next((p.table for p in source.parts if p.role == "grid_cells"), None)
        if mapping is None:
            endpoints = tuple(
                next((p.table for p in source.parts if p.role == side + "_endpoint"), None)
                for side in ("current", "baseline")
            )
            if any(t is None for t in endpoints):
                raise invalid("runs input lacks original captured coverage")
            lookup = tuple(
                _index(t, source.contract.key_fields) for t in endpoints if t is not None
            )
            keys = tuple(_index(source.primary, source.contract.key_fields))
            if any(set(i) != set(keys) for i in lookup):
                raise invalid("runs endpoint coverage has different complete keys")
            if any(
                side + "_endpoint__complete" not in table.column_names
                for side, table in zip(("current", "baseline"), endpoints, strict=True)
                if table is not None
            ):
                raise invalid("runs endpoint lacks its original captured coverage fact")
            columns = tuple(
                t[side + "_endpoint__complete"].to_pylist()
                for side, t in zip(("current", "baseline"), endpoints, strict=True)
                if t is not None
            )
            coverage = pa.table(
                {
                    **{k: source.primary[k] for k in source.contract.key_fields},
                    "complete": [
                        all(
                            column[index[key]] is True
                            for column, index in zip(columns, lookup, strict=True)
                        )
                        for key in keys
                    ],
                }
            )
        else:
            coverage = mapping.select(
                [*source.contract.key_fields, "grid_cells__coverage"]
            ).rename_columns([*source.contract.key_fields, "complete"])
    subject = next((p.table for p in source.parts if p.role == "subject"), None)
    capture = Capture(
        source.contract.signature,
        params.run_id,
        params.predicate,
        source.contract.key_fields,
        tuple(save(v.primary) for v in values),
        save(coverage),
        save(subject) if subject is not None else None,
        tuple(v.contract.input_binding for v in values),
        tuple(v.contract.signature for v in values),
        tuple(e.node.value_type for e in node.inputs),
        tuple(v.contract.cell_reasons for v in values),
    )
    views, classes, reasons = compute(capture)
    encoded = CAPTURE.dump_json(capture)
    state = Runs(hashlib.sha256(encoded).hexdigest(), save(views), classes, reasons)
    parts = [
        _payload("condition_cells", encoded.decode()),
        _payload("run_cells", RUNS.dump_json(state).decode()),
    ]
    from marivo.analysis.materialization.graph_findings import POLICY, Policy

    parts.append(ExchangePart("grid_cells", grid_mapping(capture)))
    parts.append(
        _payload(
            "finding_policy",
            POLICY.dump_json(
                Policy(
                    "time.runs",
                    "v1",
                    "graph.no_findings@v1",
                    "zero_findings@v1",
                    ("capture:" + state.input_digest,),
                    0,
                    0,
                    0,
                )
            ).decode(),
        )
    )
    if subject is not None:
        parts.append(ExchangePart("subject", subject_mapping(capture, views)))
    by_role = {p.role: p for p in parts}
    return _result(
        node,
        _primary(views, "count"),
        tuple(by_role[part_role(p)] for p in node.signature.parts),
        binding,
    )


def grid_mapping(capture: Capture) -> pa.Table:
    original = load(capture.inputs[0])
    grid = capture.signature.domain.time_grid
    assert grid is not None
    position = next(
        i for i, c in enumerate(capture.signature.domain.instance_key) if c.role == "anchor"
    )
    cells = {c.identity: (i, c) for i, c in enumerate(grid.cells)}
    chosen = [cells[row[capture.keys[position]]] for row in original.to_pylist()]
    mapping = {key: original[key] for key in capture.keys}
    for name in ("start", "end", "original_start", "original_end"):
        mapping["grid_cells__" + name] = pa.chunked_array(
            [pa.array([getattr(cell, name) for _, cell in chosen], type=pa.timestamp("us", "UTC"))]
        )
    mapping["grid_cells__identity"] = pa.chunked_array(
        [pa.array([cell.identity for _, cell in chosen], type=pa.string())]
    )
    mapping["grid_cells__ordinal"] = pa.chunked_array(
        [pa.array([i for i, _ in chosen], type=pa.int64())]
    )
    mapping["grid_cells__partial"] = pa.chunked_array(
        [pa.array([cell.partial for _, cell in chosen], type=pa.bool_())]
    )
    mapping["grid_cells__precision"] = pa.chunked_array(
        [pa.array([grid.precision] * len(chosen), type=pa.string())]
    )
    coverage = load(capture.coverage)
    lookup = _index(coverage, capture.keys)
    mapping["grid_cells__coverage"] = coverage["complete"].take(
        pa.array([lookup[k] for k in _index(original, capture.keys)], type=pa.int64())
    )
    return pa.table(mapping)


def subject_mapping(capture: Capture, views: pa.Table) -> pa.Table:
    if capture.subject is None:
        raise invalid("run Subject declaration lacks captured actual Subject parts")
    subject = load(capture.subject)
    original = _index(subject, capture.keys)
    source_keys = tuple(_index(load(capture.inputs[0]), capture.keys))
    if set(original) != set(source_keys):
        raise invalid("run Subject map differs from the original complete key domain")
    mapped = []
    subject_names = [n for n in subject.column_names if n not in capture.keys]
    subjects = subject.to_pylist()
    for row in views.to_pylist():
        images = [subjects[original[source_keys[i]]] for i in row["input_rows"]]
        image = {name: images[0][name] for name in subject_names}
        if any(any(item[n] != image[n] for n in subject_names) for item in images):
            raise invalid("run cells do not retain a single actual Subject image")
        mapped.append({"key_0": row["key_0"], **image})
    return pa.Table.from_pylist(
        mapped,
        schema=pa.schema(
            [("key_0", pa.string()), *(subject.schema.field(n) for n in subject_names)]
        ),
    )


def _verify_intervals(
    capture: Capture,
    views: pa.Table,
    classes: tuple[Literal["true", "false", "unavailable"], ...],
    series: dict[tuple[object, ...], dict[str, int]],
    semantic_inputs: tuple[str, ...],
) -> None:
    """Verify frozen interval witnesses without constructing or segmenting runs."""
    if views.schema != _VIEW_SCHEMA:
        raise invalid("run witness schema differs from the closed interval fields")
    grid = capture.signature.domain.time_grid
    assert grid is not None
    ownership = {
        index: (sequence, ordinal)
        for sequence, cells in series.items()
        for ordinal, cell in enumerate(grid.cells)
        for index in (cells[cell.identity],)
    }
    covered: set[int] = set()
    previous: tuple[str, int] | None = None
    for row in views.to_pylist():
        check()
        indices = row["input_rows"]
        if (
            not indices
            or any(type(i) is not int or i not in ownership for i in indices)
            or any(i in covered or classes[i] != "true" for i in indices)
            or len(set(indices)) != len(indices)
        ):
            raise invalid("run witness overlaps or includes an invalid/non-true original cell")
        sequence, start = ownership[indices[0]]
        stop = start + len(indices)
        cells = series[sequence]
        if stop > len(grid.cells) or indices != [
            cells[grid.cells[j].identity] for j in range(start, stop)
        ]:
            raise invalid("run witness is not one adjacent original sequence")
        ordering = repr(sequence), start
        if previous is not None and ordering <= previous:
            raise invalid("run witnesses are not in original deterministic order")
        previous = ordering
        first, last = grid.cells[start], grid.cells[stop - 1]
        delta = last.end - first.start
        ticks = (delta.days * 86400 + delta.seconds) * 1000000 + delta.microseconds
        identity = hashlib.sha256(
            canonical_json(
                [
                    capture.run_id,
                    capture.input_bindings,
                    semantic_inputs,
                    sequence,
                    grid.identity,
                    first.identity,
                    last.identity,
                ]
            ).encode()
        ).hexdigest()
        left = "scope_boundary" if start == 0 else classes[cells[grid.cells[start - 1].identity]]
        right = (
            "scope_boundary"
            if stop == len(grid.cells)
            else classes[cells[grid.cells[stop].identity]]
        )
        if (
            left == "true"
            or right == "true"
            or not 0 < ticks < 2**63
            or len(indices) >= 2**63
            or row["key_0"] != identity
            or row["start"] != first.start
            or row["end"] != last.end
            or row["count"] != len(indices)
            or row["duration"] != delta
            or row["cells"] != [grid.cells[j].identity for j in range(start, stop)]
            or row["left_kind"] != left
            or row["right_kind"] != right
            or row["left_cell"] != (None if start == 0 else grid.cells[start - 1].identity)
            or row["right_cell"] != (None if stop == len(grid.cells) else grid.cells[stop].identity)
        ):
            raise invalid(
                "run witness identity, endpoints, Duration or maximal termination differs"
            )
        covered.update(indices)
    if covered != {i for i, classification in enumerate(classes) if classification == "true"}:
        raise invalid("run witnesses do not cover every original true cell exactly once")


def validate(
    contract: ExchangeContract, primary: pa.Table, parts: tuple[ExchangePart, ...]
) -> None:
    capture, state = _decode(parts)
    from marivo.analysis.materialization.graph_findings import Policy, _policy

    if _policy(parts) != Policy(
        "time.runs",
        "v1",
        "graph.no_findings@v1",
        "zero_findings@v1",
        ("capture:" + state.input_digest,),
        0,
        0,
        0,
    ):
        raise invalid("run zero-Findings policy differs from original condition capture")
    declaration = next(p for p in contract.signature.parts if isinstance(p, ConditionCellsPart))
    view = next(p for p in contract.signature.parts if isinstance(p, RunCellsPart))
    if (
        declaration.condition_digest
        != hashlib.sha256(TypeAdapter(ValuePredicate).dump_json(capture.predicate)).hexdigest()
        or declaration.run_id != capture.run_id
        or declaration.input_domain != capture.signature.domain
        or view.run_id != capture.run_id
    ):
        raise invalid("run declaration differs from captured grid/condition identity")
    classes, reasons, series, semantic_inputs = _condition_scope(capture)
    if classes != state.classifications or reasons != state.reasons:
        raise invalid("run full classification scope differs")
    views = load(state.views)
    _verify_intervals(capture, views, classes, series, semantic_inputs)
    index = _index(views, ("key_0",))
    selected = tuple(_index(primary, ("key_0",)))
    if not set(selected) <= set(index):
        raise invalid("selected runs escape original full interval scope")
    selected_views = views.take(pa.array([index[k] for k in selected], type=pa.int64()))
    by_role = {p.role: p for p in parts}
    if "grid_cells" not in by_role or not grid_mapping(capture).equals(by_role["grid_cells"].table):
        raise invalid("run original per-cell grid mapping differs from its frozen authority")
    if "subject" in by_role and not subject_mapping(capture, selected_views).equals(
        by_role["subject"].table
    ):
        raise invalid("run Subject image differs from its original cell-to-Subject map")
    expected = _primary(
        selected_views,
        "count" if view.view == "result" else view.view,
    )
    if (
        contract.method.name
        in (
            "time.runs",
            "time.runs_read",
            "parts_transport",
        )
        and contract.signature.quantity is not None
        and contract.signature.quantity.definition_id
        == capture.run_id + ":" + ("count" if view.view == "result" else view.view)
        and not expected.equals(primary)
    ):
        raise invalid("run owned field differs from original retained intervals")
