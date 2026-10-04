"""Caller-owned deviation consumers and immutable fitted-field transport."""

from __future__ import annotations

import base64
import hashlib
import struct
from dataclasses import dataclass
from fractions import Fraction
from itertools import pairwise
from typing import Literal

import pyarrow as pa
from pydantic import TypeAdapter

from marivo.analysis.core.graph import MethodNode
from marivo.analysis.core.model import (
    CoreRuleError,
    FitInputsPart,
    FitStatePart,
    GridCellsPart,
    PartRole,
    Signature,
    SubjectMapPart,
    SubjectPart,
    TableFitsPart,
    part_role,
)
from marivo.analysis.core.rules import DeviationFit, DeviationRead
from marivo.analysis.datasets.errors import DatasetConstructionError
from marivo.analysis.errors import StatisticalRelationError
from marivo.analysis.materialization.errors import IntegrityError
from marivo.analysis.materialization.execute_deadline import check
from marivo.analysis.materialization.graph_exchange import (
    ExchangeContract,
    ExchangePart,
    ExchangeResult,
    PartContract,
    from_arrow,
)
from marivo.analysis.materialization.graph_protocol import invalid
from marivo.analysis.methods import deviation_numeric as arithmetic
from marivo.analysis.methods.deviation_physical import output_type, parse_type
from marivo.analysis.methods.physical import arrow_scalar_type
from marivo.analysis.methods.semantics import MethodKey


@dataclass(frozen=True, slots=True)
class SavedTable:
    data: str
    digest: str
    rows: int


@dataclass(frozen=True, slots=True)
class SavedPart:
    role: PartRole
    keys: tuple[str, ...]
    table: SavedTable


@dataclass(frozen=True, slots=True)
class Inputs:
    fit_id: str
    input_binding: str
    signature: Signature
    keys: tuple[str, ...]
    primary: SavedTable
    categories: tuple[SavedTable, ...]
    parts: tuple[SavedPart, ...]
    version: Literal["r8.fit_inputs/v1"] = "r8.fit_inputs/v1"


@dataclass(frozen=True, slots=True)
class Partition:
    indices: tuple[int, ...]
    order: tuple[int, ...]
    absolute_order: tuple[int, ...]
    defined: int
    null: int
    undefined: int
    unknown: int
    fit: arithmetic.Fit


@dataclass(frozen=True, slots=True)
class ScoreWitness:
    index: int
    root: arithmetic.RootCertificate | None


@dataclass(frozen=True, slots=True)
class State:
    fit_id: str
    input_digest: str
    partitions: tuple[Partition, ...]
    views: SavedTable
    scores: tuple[ScoreWitness, ...]
    transform: Literal["select_output_retain_scope@v1"] = "select_output_retain_scope@v1"
    version: Literal["r8.fit_state/v1"] = "r8.fit_state/v1"


INPUTS = TypeAdapter(Inputs)
STATE = TypeAdapter(State)


@dataclass(frozen=True, slots=True)
class CapturedColumn:
    index: int
    input_binding: str
    keys: tuple[str, ...]
    primary: SavedTable
    parts: tuple[SavedPart, ...]
    cell_reasons: tuple[tuple[str, tuple[str, ...]], ...]
    allow_empty_singleton: bool


@dataclass(frozen=True, slots=True)
class TableFits:
    columns: tuple[CapturedColumn, ...]
    version: Literal["r8.table_fits/v1"] = "r8.table_fits/v1"


TABLE_FITS = TypeAdapter(TableFits)


def save(table: pa.Table) -> SavedTable:
    sink = pa.BufferOutputStream()
    with pa.ipc.new_stream(sink, table.schema) as writer:
        writer.write_table(table)
    raw = sink.getvalue().to_pybytes()
    return SavedTable(
        base64.b64encode(raw).decode(), hashlib.sha256(raw).hexdigest(), table.num_rows
    )


def load(value: SavedTable) -> pa.Table:
    check()
    raw = base64.b64decode(value.data, validate=True)
    if hashlib.sha256(raw).hexdigest() != value.digest:
        raise invalid("retained deviation table digest differs")
    with pa.ipc.open_stream(raw) as reader:
        table = reader.read_all()
    if table.num_rows != value.rows:
        raise invalid("retained deviation row count differs")
    return table


def _part(role: str, payload: str) -> ExchangePart:
    return ExchangePart(role, pa.table({role + "__retained": [payload]}))


def retain_table_fits(
    inputs: tuple[ExchangeResult, ...], declaration: TableFitsPart
) -> ExchangePart:
    columns = []
    for column in declaration.columns:
        check()
        source = inputs[column.index]
        if source.contract.signature != column.signature:
            raise invalid("fitted table column differs from its declared input signature")
        columns.append(
            CapturedColumn(
                column.index,
                source.contract.input_binding,
                source.contract.key_fields,
                save(source.primary),
                tuple(
                    SavedPart(
                        part_role(
                            next(p for p in column.signature.parts if part_role(p) == part.role)
                        ),
                        next(p.key_fields for p in source.contract.parts if p.role == part.role),
                        save(part.table),
                    )
                    for part in source.parts
                ),
                source.contract.cell_reasons,
                source.contract.allow_empty_singleton,
            )
        )
    return _part("table_fits", TABLE_FITS.dump_json(TableFits(tuple(columns))).decode())


def validate_table_fits(
    contract: ExchangeContract, primary: pa.Table, parts: tuple[ExchangePart, ...]
) -> None:
    declaration = next(p for p in contract.signature.parts if isinstance(p, TableFitsPart))
    fit = next(p for p in declaration.columns[0].signature.parts if isinstance(p, FitInputsPart))
    try:
        payload = next(p.table for p in parts if p.role == "table_fits")["table_fits__retained"][
            0
        ].as_py()
        captured = TABLE_FITS.validate_json(payload, strict=True)
        if TABLE_FITS.dump_json(captured).decode() != payload or tuple(
            c.index for c in captured.columns
        ) != tuple(c.index for c in declaration.columns):
            raise invalid(
                "fitted table capture version, canonical encoding or column binding differs"
            )
        current = _index(primary, contract.key_fields)
        for column, saved in zip(declaration.columns, captured.columns, strict=True):
            check()
            source = load(saved.primary)
            retained_parts = tuple(ExchangePart(p.role, load(p.table)) for p in saved.parts)
            original_contract = ExchangeContract(
                column.signature,
                MethodKey("parts_transport"),
                saved.input_binding,
                source.schema,
                saved.keys,
                tuple(
                    PartContract(p.role, retained.table.schema, p.keys)
                    for p, retained in zip(saved.parts, retained_parts, strict=True)
                ),
                saved.cell_reasons,
                allow_empty_singleton=saved.allow_empty_singleton,
            )
            from_arrow(source, original_contract, parts=retained_parts)
            original = _index(source, saved.keys)
            if saved.keys != contract.key_fields or set(original) != set(current):
                raise invalid("fitted table column keys differ from their captured current domain")
            expected = source.take(pa.array([original[key] for key in current], type=pa.int64()))
            actual = primary.select(
                (
                    *saved.keys,
                    *(f"column_{column.index}__{c}" for c in ("value", "cell_tag", "cell_reason")),
                )
            ).rename_columns((*saved.keys, "value", "cell_tag", "cell_reason"))
            if not actual.equals(expected):
                raise invalid("fitted table column differs from its verified original value view")
    except (
        ValueError,
        ArithmeticError,
        CoreRuleError,
        DatasetConstructionError,
        StatisticalRelationError,
        pa.ArrowInvalid,
        KeyError,
        IndexError,
    ) as error:
        raise StatisticalRelationError(
            code="r8.retained_part",
            operation="recover",
            method=f"deviation.{fit.method}@v1",
            input_identity=fit.fit_id,
            expected="all fitted table columns, original parts and exact current value bindings",
            received=str(error),
            repair="Restore the original table_fits part and column receipts; otherwise execute the original source table in a new Run.",
        ) from error


def _decode(parts: tuple[ExchangePart, ...]) -> tuple[Inputs, State]:
    by_role = {part.role: part.table for part in parts}
    first = by_role["fit_inputs"]["fit_inputs__retained"][0].as_py()
    second = by_role["fit_state"]["fit_state__retained"][0].as_py()
    inputs, state = (
        INPUTS.validate_json(first, strict=True),
        STATE.validate_json(second, strict=True),
    )
    if (
        INPUTS.dump_json(inputs).decode() != first
        or STATE.dump_json(state).decode() != second
        or inputs.fit_id != state.fit_id
        or state.input_digest != hashlib.sha256(first.encode()).hexdigest()
    ):
        raise invalid("deviation canonical encoding, input digest or fit identity differs")
    return inputs, state


def _index(table: pa.Table, keys: tuple[str, ...]) -> dict[tuple[object, ...], int]:
    result = {tuple(row[k] for k in keys): i for i, row in enumerate(table.to_pylist())}
    if len(result) != table.num_rows or any(None in key for key in result):
        raise invalid("deviation input contains null or duplicate complete keys")
    return result


def _primary(views: pa.Table, field: str, keys: tuple[str, ...]) -> pa.Table:
    return views.select(
        (*keys, *(field + "__" + c for c in ("value", "cell_tag", "cell_reason")))
    ).rename_columns((*keys, "value", "cell_tag", "cell_reason"))


def _mapping_parts(inputs: Inputs, declaration: FitInputsPart) -> tuple[ExchangePart, ...]:
    """Capture original per-instance mappings from their actual typed owners."""
    primary = load(inputs.primary)
    result = []
    subject = next((part for part in inputs.parts if part.role == "subject"), None)
    if subject is not None:
        table = load(subject.table)
        result.append(
            ExchangePart(
                "subject_map",
                table.rename_columns(
                    tuple(name.replace("subject__", "subject_map__") for name in table.column_names)
                ),
            )
        )
    grid = declaration.input_domain.time_grid
    if grid is not None:
        position = next(
            i
            for i, coordinate in enumerate(declaration.input_domain.instance_key)
            if coordinate.role == "anchor"
        )
        by_id = {cell.identity: (i, cell) for i, cell in enumerate(grid.cells)}
        cells = []
        for row in primary.to_pylist():
            check()
            identity = row[inputs.keys[position]]
            if identity not in by_id:
                raise invalid("deviation row has no captured grid cell")
            cells.append(by_id[identity])
        coverage = next((part for part in inputs.parts if part.role == "coverage"), None)
        retained_grid = next((part for part in inputs.parts if part.role == "grid_cells"), None)
        if coverage is None and retained_grid is not None:
            table = load(retained_grid.table)
            lookup = _index(table, inputs.keys)
            original_keys = tuple(_index(primary, inputs.keys))
            if not set(original_keys) <= set(lookup):
                raise invalid("deviation input escapes its retained complete grid mapping")
            complete = table["grid_cells__coverage"].take(
                pa.array([lookup[key] for key in original_keys], type=pa.int64())
            )
            if any(value is not True for value in complete.to_pylist()):
                raise invalid("deviation input lacks retained complete grid coverage")
        elif coverage is None:
            endpoint_coverage: list[pa.ChunkedArray] = []
            original_keys = tuple(_index(primary, inputs.keys))
            for side in ("current", "baseline"):
                endpoint = next(
                    (part for part in inputs.parts if part.role == side + "_endpoint"), None
                )
                if endpoint is None:
                    raise invalid("deviation grid input lacks captured endpoint coverage")
                table = load(endpoint.table)
                column = side + "_endpoint__complete"
                if column not in table.column_names:
                    raise invalid("deviation grid endpoint lacks its original coverage fact")
                index = _index(table, inputs.keys)
                if set(index) != set(original_keys):
                    raise invalid("deviation grid endpoint coverage has different complete keys")
                endpoint_coverage.append(
                    table[column].take(
                        pa.array([index[key] for key in original_keys], type=pa.int64())
                    )
                )
            if any(
                value is not True for column in endpoint_coverage for value in column.to_pylist()
            ):
                raise invalid("deviation grid endpoints do not retain complete coverage")
            complete = pa.chunked_array(
                [
                    pa.array(
                        [
                            a and b
                            for a, b in zip(
                                endpoint_coverage[0].to_pylist(),
                                endpoint_coverage[1].to_pylist(),
                                strict=True,
                            )
                        ],
                        type=pa.bool_(),
                    )
                ]
            )
        else:
            complete = load(coverage.table)["coverage__complete"]
        arrays = {key: primary[key] for key in inputs.keys}
        arrays.update(
            {
                "grid_cells__identity": pa.array(
                    [cell.identity for _, cell in cells], type=pa.string()
                ),
                "grid_cells__ordinal": pa.array([ordinal for ordinal, _ in cells], type=pa.int64()),
                **{
                    "grid_cells__" + name: pa.array(
                        [getattr(cell, name) for _, cell in cells], type=pa.timestamp("us", "UTC")
                    )
                    for name in ("original_start", "original_end", "start", "end")
                },
                "grid_cells__partial": pa.array(
                    [cell.partial for _, cell in cells], type=pa.bool_()
                ),
                "grid_cells__precision": pa.array([grid.precision] * len(cells), type=pa.string()),
                "grid_cells__coverage": complete,
            }
        )
        result.append(ExchangePart("grid_cells", pa.table(arrays)))
    return tuple(result)


def _execute(node: MethodNode, values: tuple[ExchangeResult, ...], binding: str) -> ExchangeResult:
    check()
    params = node.parameters
    if isinstance(params, DeviationRead):
        return project(values[0], params.field, node=node, binding=binding)
    assert isinstance(params, DeviationFit)
    source = values[0]
    typ = parse_type(params.input_type)
    rows, keys = source.primary.to_pylist(), source.contract.key_fields
    original = _index(source.primary, keys)
    categories = []
    for value in values[1:]:
        if value.contract.key_fields != keys or set(_index(value.primary, keys)) != set(original):
            raise invalid("deviation categorical input lacks the exact complete key set")
        category_index = _index(value.primary, keys)
        ordered = value.primary.take(
            pa.array([category_index[key] for key in original], type=pa.int64())
        )
        category_rows = ordered.to_pylist()
        if any(row["cell_tag"] not in ("defined", "null") for row in category_rows):
            raise invalid("deviation partitions require Defined or Null categories")
        categories.append(ordered)
    for part in source.parts:
        if part.role == "coverage" and any(
            value is not True for value in part.table["coverage__complete"].to_pylist()
        ):
            raise invalid("deviation input has false captured coverage")
    groups: dict[tuple[object, ...], list[int]] = {}
    category_values = [table["value"].to_pylist() for table in categories]
    for index in range(len(rows)):
        check()
        groups.setdefault(tuple(column[index] for column in category_values), []).append(index)
    if not rows and not categories:
        groups[()] = []
    partitions = []
    scores = []
    fields: dict[str, list[object]] = {
        field + "__" + c: [None] * len(rows)
        for field in ("observed", "reference", "deviation", "score")
        for c in ("value", "cell_tag", "cell_reason")
    }
    for indices in groups.values():
        check()
        facts = {
            i: arithmetic.exact(rows[i]["value"], typ)
            for i in indices
            if rows[i]["cell_tag"] == "defined"
        }
        fitted = arithmetic.fit(tuple(facts.values()), params.method)
        center = fitted.center.value() if fitted.center is not None else None
        order = tuple(sorted(facts, key=lambda i: facts[i]))
        absolute = (
            tuple(sorted(facts, key=lambda i: abs(facts[i] - center))) if center is not None else ()
        )
        counts = tuple(
            sum(rows[i]["cell_tag"] == tag for i in indices)
            for tag in ("defined", "null", "undefined", "unknown")
        )
        partitions.append(
            Partition(
                tuple(indices), order, absolute, counts[0], counts[1], counts[2], counts[3], fitted
            )
        )
        for i in indices:
            check()
            row = rows[i]
            for field in ("observed", "deviation", "score"):
                fields[field + "__cell_tag"][i], fields[field + "__cell_reason"][i] = (
                    row["cell_tag"],
                    row["cell_reason"],
                )
            fields["observed__value"][i] = row["value"]
            fields["reference__cell_tag"][i] = "defined" if center is not None else "undefined"
            fields["reference__cell_reason"][i] = None if center is not None else "no_valid_samples"
            fields["reference__value"][i] = (
                arithmetic.finish(center, arithmetic.unit_type(typ)) if center is not None else None
            )
            if i in facts:
                assert center is not None and fitted.raw_scale is not None
                delta = facts[i] - center
                fields["deviation__value"][i] = arithmetic.finish(delta, arithmetic.unit_type(typ))
                if fitted.n < 2 or fitted.raw_scale.value() == 0:
                    fields["score__cell_tag"][i] = "undefined"
                    fields["score__cell_reason"][i] = (
                        "insufficient_samples" if fitted.n < 2 else "zero_scale"
                    )
                else:
                    scored = arithmetic.certified_score(delta, fitted, checkpoint=check)
                    fields["score__value"][i] = scored.value
                    scores.append(ScoreWitness(i, scored.root))
    arrays = {key: source.primary[key] for key in keys}
    for name, data in fields.items():
        field, component = name.split("__")
        arrays[name] = pa.array(
            data,
            type=arrow_scalar_type(output_type(field, typ))
            if component == "value"
            else pa.string(),
        )
    views = pa.table(arrays)
    saved = Inputs(
        params.fit_id,
        source.contract.input_binding,
        source.contract.signature,
        keys,
        save(source.primary),
        tuple(save(table) for table in categories),
        tuple(
            SavedPart(
                next(
                    part_role(p)
                    for p in source.contract.signature.parts
                    if part_role(p) == part.role
                ),
                next(p.key_fields for p in source.contract.parts if p.role == part.role),
                save(part.table),
            )
            for part in source.parts
        ),
    )
    encoded = INPUTS.dump_json(saved).decode()
    state = State(
        params.fit_id,
        hashlib.sha256(encoded.encode()).hexdigest(),
        tuple(partitions),
        save(views),
        tuple(scores),
    )
    from marivo.analysis.materialization.graph_findings import POLICY, Policy

    policy = Policy(
        "deviation.zscore" if params.method == "zscore" else "deviation.mad",
        "v1",
        "graph.no_findings@v1",
        "zero_findings@v1",
        ("capture:" + state.input_digest,),
        0,
        0,
        0,
    )
    parts = (
        *tuple(p for p in source.parts if p.role == "subject"),
        _part("fit_inputs", encoded),
        _part("fit_state", STATE.dump_json(state).decode()),
        _part("finding_policy", POLICY.dump_json(policy).decode()),
    )
    declaration = next(p for p in node.signature.parts if isinstance(p, FitInputsPart))
    mapping = _mapping_parts(saved, declaration)
    by_role = {p.role: p for p in (*parts, *mapping)}
    parts = tuple(by_role[part_role(p)] for p in node.signature.parts)
    primary = _primary(views, "score", keys)
    return _result(node, primary, parts, keys, binding)


def _result(
    node: MethodNode,
    primary: pa.Table,
    parts: tuple[ExchangePart, ...],
    keys: tuple[str, ...],
    binding: str,
) -> ExchangeResult:
    reasons: dict[str, set[str]] = {}
    for row in primary.to_pylist():
        if row["cell_tag"] != "defined":
            reasons.setdefault(row["cell_tag"], set()).add(row["cell_reason"])
    contract = ExchangeContract(
        node.signature,
        node.method,
        binding,
        primary.schema,
        keys,
        tuple(
            PartContract(
                part.role,
                part.table.schema,
                () if part.role in ("fit_inputs", "fit_state", "finding_policy") else keys,
            )
            for part in parts
        ),
        tuple((tag, tuple(sorted(values))) for tag, values in sorted(reasons.items())),
        allow_empty_singleton=not keys and primary.num_rows == 0,
    )
    return from_arrow(primary, contract, parts=parts)


def project(
    source: ExchangeResult, field: str, *, node: MethodNode, binding: str
) -> ExchangeResult:
    inputs, state = _decode(source.parts)
    views = load(state.views)
    index = _index(views, inputs.keys)
    chosen = [index[key] for key in _index(source.primary, inputs.keys)]
    primary = _primary(views.take(pa.array(chosen, type=pa.int64())), field, inputs.keys)
    roles = {part_role(p) for p in node.signature.parts}
    parts = [p for p in source.parts if p.role in roles]
    if field == "observed":
        for saved in inputs.parts:
            if saved.role in roles and saved.role not in {p.role for p in parts}:
                table = load(saved.table)
                if saved.keys == inputs.keys:
                    lookup = _index(table, inputs.keys)
                    table = table.take(
                        pa.array(
                            [lookup[key] for key in _index(primary, inputs.keys)], type=pa.int64()
                        )
                    )
                parts.append(ExchangePart(saved.role, table))
    by_role = {p.role: p for p in parts}
    return _result(
        node,
        primary,
        tuple(by_role[part_role(p)] for p in node.signature.parts),
        inputs.keys,
        binding,
    )


def _validate(
    contract: ExchangeContract, primary: pa.Table, parts: tuple[ExchangePart, ...]
) -> None:
    """Verify retained witnesses without fitting or replacing any output."""
    check()
    if tuple(part.role for part in parts) != tuple(part.role for part in contract.parts):
        raise invalid("deviation required parts are missing, extra or reordered")
    inputs, state = _decode(parts)
    declaration = next(p for p in contract.signature.parts if isinstance(p, FitInputsPart))
    if (
        declaration.fit_id != inputs.fit_id
        or declaration.version != "v1"
        or not any(
            isinstance(p, FitStatePart)
            and p.fit_id == state.fit_id
            and p.version == "v1"
            and p.binding == declaration.binding
            for p in contract.signature.parts
        )
    ):
        raise invalid("deviation declaration or fit state identity differs")
    subjects = tuple(p for p in declaration.original_parts if isinstance(p, SubjectPart))
    grids = tuple(p for p in contract.signature.parts if isinstance(p, GridCellsPart))
    mappings = tuple(p for p in contract.signature.parts if isinstance(p, SubjectMapPart))
    if (
        len(grids) != int(declaration.input_domain.time_grid is not None)
        or any(
            p.version != "v1"
            or p.fit_id != declaration.fit_id
            or p.binding != declaration.binding
            or p.input_domain != declaration.input_domain
            for p in grids
        )
        or tuple(p.subject for p in mappings) != subjects
        or any(
            p.version != "v1" or p.fit_id != declaration.fit_id or p.binding != declaration.binding
            for p in mappings
        )
    ):
        raise invalid(
            "deviation grid or Subject mapping declaration differs from its original authority"
        )
    table, views = load(inputs.primary), load(state.views)
    typ = parse_type(declaration.input_type)
    key_fields = tuple(contract.schema.field(key) for key in inputs.keys)
    input_fields = (
        *key_fields,
        pa.field("value", arrow_scalar_type(typ)),
        pa.field("cell_tag", pa.string()),
        pa.field("cell_reason", pa.string()),
    )
    view_fields = (
        *key_fields,
        *(
            pa.field(field + "__" + component, carrier)
            for field in ("observed", "reference", "deviation", "score")
            for component, carrier in (
                ("value", arrow_scalar_type(output_type(field, typ))),
                ("cell_tag", pa.string()),
                ("cell_reason", pa.string()),
            )
        ),
    )
    for retained, fields in ((table, input_fields), (views, view_fields)):
        if retained.column_names != [field.name for field in fields] or any(
            retained.schema.field(field.name).type != field.type for field in fields
        ):
            raise invalid("deviation retained schema differs from its typed original authority")
    rows = table.to_pylist()
    view_rows = views.to_pylist()
    original = _index(table, inputs.keys)
    if _index(views, inputs.keys) != original or contract.key_fields != inputs.keys:
        raise invalid("deviation original keys or view correspondence differs")
    if (
        inputs.signature.domain != declaration.input_domain
        or inputs.signature.quantity != declaration.input_quantity
        or inputs.signature.parts != declaration.original_parts
        or len(inputs.categories) != len(declaration.partition_ids)
        or tuple(p.role for p in inputs.parts)
        != tuple(part_role(p) for p in declaration.original_parts)
    ):
        raise invalid("deviation original signature, parts or partition binding differs")
    retained_fit = any(isinstance(p, FitInputsPart) for p in inputs.signature.parts)
    if retained_fit:
        nested_parts = tuple(ExchangePart(p.role, load(p.table)) for p in inputs.parts)
        reasons: dict[str, set[str]] = {}
        for row in rows:
            if row["cell_tag"] != "defined":
                reasons.setdefault(row["cell_tag"], set()).add(row["cell_reason"])
        nested_contract = ExchangeContract(
            inputs.signature,
            MethodKey("deviation.read"),
            inputs.input_binding,
            table.schema,
            inputs.keys,
            tuple(
                PartContract(p.role, saved.table.schema, p.keys)
                for p, saved in zip(inputs.parts, nested_parts, strict=True)
            ),
            tuple((tag, tuple(sorted(values))) for tag, values in sorted(reasons.items())),
            allow_empty_singleton=not inputs.keys and table.num_rows == 0,
        )
        _validate(nested_contract, table, nested_parts)
    category_columns = []
    for saved in inputs.categories:
        categorical = load(saved)
        if _index(categorical, inputs.keys) != original or any(
            row["cell_tag"] not in ("defined", "null") for row in categorical.to_pylist()
        ):
            raise invalid("deviation retained categorical keys or Cell states differ")
        category_columns.append(categorical["value"].to_pylist())
    groups: set[tuple[object, ...]] = set()
    witnesses = {w.index: w.root for w in state.scores}
    if len(witnesses) != len(state.scores):
        raise invalid("deviation has duplicate score witnesses")
    scored_indices: set[int] = set()
    seen: set[int] = set()
    for partition in state.partitions:
        check()
        if any(i in seen or not 0 <= i < len(rows) for i in partition.indices):
            raise invalid("deviation partition has duplicate or escaping rows")
        seen.update(partition.indices)
        labels = {tuple(column[i] for column in category_columns) for i in partition.indices}
        if (
            len(labels) > 1
            or (labels and labels <= groups)
            or (not partition.indices and (rows or category_columns or len(state.partitions) != 1))
        ):
            raise invalid("deviation partitions differ from the captured classification tuples")
        groups.update(labels)
        facts = {
            i: arithmetic.exact(rows[i]["value"], typ)
            for i in partition.indices
            if rows[i]["cell_tag"] == "defined"
        }
        counts = tuple(
            sum(rows[i]["cell_tag"] == tag for i in partition.indices)
            for tag in ("defined", "null", "undefined", "unknown")
        )
        fitted = partition.fit
        if (
            counts != (partition.defined, partition.null, partition.undefined, partition.unknown)
            or sum(counts) != len(partition.indices)
            or fitted.n != len(facts)
            or fitted.method != declaration.method
            or fitted.version != "v1"
            or fitted.numeric_policy != "r8_numeric_v1"
        ):
            raise invalid("deviation sample/state counts or numeric policy differs")
        if (
            set(partition.order) != set(facts)
            or len(partition.order) != len(facts)
            or any(facts[a] > facts[b] for a, b in pairwise(partition.order))
        ):
            raise invalid("deviation order witnesses differ")
        if not facts:
            if (
                fitted.center is not None
                or fitted.raw_scale is not None
                or fitted.lower_middle is not None
                or fitted.upper_middle is not None
                or partition.absolute_order
                or fitted.branch
                != ("population_stddev" if fitted.method == "zscore" else "scaled_mad")
            ):
                raise invalid("empty deviation partition has synthetic fit facts")
            center = None
            scale = None
        elif fitted.center is None or fitted.raw_scale is None:
            raise invalid("Defined deviation partition is missing its fit facts")
        else:
            center = fitted.center.value()
            scale = fitted.raw_scale.value()
        if facts and fitted.method == "zscore":
            assert center is not None
            if fitted.lower_middle is not None or fitted.upper_middle is not None:
                raise invalid("zscore unexpectedly contains median witnesses")
            if (
                set(partition.absolute_order) != set(facts)
                or len(partition.absolute_order) != len(facts)
                or any(
                    abs(facts[a] - center) > abs(facts[b] - center)
                    for a, b in pairwise(partition.absolute_order)
                )
            ):
                raise invalid("zscore absolute-order witnesses differ")
            expected_center = sum(facts.values(), Fraction()) / len(facts)
            scale = sum(((x - center) ** 2 for x in facts.values()), Fraction()) / len(facts)
            branch = "population_stddev"
        elif facts:
            assert center is not None
            n = len(facts)
            lower, upper = facts[partition.order[(n - 1) // 2]], facts[partition.order[n // 2]]
            expected_center = (lower + upper) / 2
            if (
                fitted.lower_middle != arithmetic.RationalFact.capture(lower)
                or fitted.upper_middle != arithmetic.RationalFact.capture(upper)
                or set(partition.absolute_order) != set(facts)
                or len(partition.absolute_order) != n
                or any(
                    abs(facts[a] - center) > abs(facts[b] - center)
                    for a, b in pairwise(partition.absolute_order)
                )
            ):
                raise invalid("deviation median/absolute-order witnesses differ")
            raw = (
                abs(facts[partition.absolute_order[(n - 1) // 2]] - center)
                + abs(facts[partition.absolute_order[n // 2]] - center)
            ) / 2
            scale = (
                raw * Fraction(7413, 5000)
                if raw
                else sum((abs(x - center) for x in facts.values()), Fraction()) / n
            )
            branch = "scaled_mad" if raw else "mean_absolute_deviation"
        if facts and (
            center != expected_center
            or fitted.raw_scale is None
            or fitted.raw_scale.value() != scale
            or fitted.branch != branch
        ):
            raise invalid("deviation center or scale equation differs")
        for i in partition.indices:
            check()
            row, view = rows[i], view_rows[i]
            for component in ("value", "cell_tag", "cell_reason"):
                if view["observed__" + component] != row[component]:
                    raise invalid("observed owned view differs from its exact original Cell")
            if type(row["value"]) is float and struct.pack(
                "!d", view["observed__value"]
            ) != struct.pack("!d", row["value"]):
                raise invalid("observed owned view changes original binary64 bits")
            expected_reference = (
                (arithmetic.finish(center, arithmetic.unit_type(typ)), "defined", None)
                if center is not None
                else (None, "undefined", "no_valid_samples")
            )
            if (
                tuple(view["reference__" + c] for c in ("value", "cell_tag", "cell_reason"))
                != expected_reference
            ):
                raise invalid("reference owned view differs from the unrounded center")
            if i not in facts:
                for field in ("deviation", "score"):
                    if tuple(
                        view[field + "__" + c] for c in ("value", "cell_tag", "cell_reason")
                    ) != (None, row["cell_tag"], row["cell_reason"]):
                        raise invalid("non-Defined owned view changes the original Cell state")
                continue
            assert center is not None and scale is not None
            delta = facts[i] - center
            if tuple(view["deviation__" + c] for c in ("value", "cell_tag", "cell_reason")) != (
                arithmetic.finish(delta, arithmetic.unit_type(typ)),
                "defined",
                None,
            ):
                raise invalid("deviation owned view differs from the unrounded delta")
            if fitted.n < 2 or scale == 0:
                if tuple(view["score__" + c] for c in ("value", "cell_tag", "cell_reason")) != (
                    None,
                    "undefined",
                    "insufficient_samples" if fitted.n < 2 else "zero_scale",
                ):
                    raise invalid(
                        "score unavailable state differs from the original sample/scale facts"
                    )
            else:
                if (
                    i not in witnesses
                    or view["score__cell_tag"] != "defined"
                    or view["score__cell_reason"] is not None
                    or not arithmetic.verify_score(
                        delta, fitted, view["score__value"], witnesses[i]
                    )
                ):
                    raise invalid("score owned view differs from its retained rounding certificate")
                scored_indices.add(i)
    if seen != set(range(len(rows))):
        raise invalid("deviation fit scope is incomplete")
    if set(witnesses) != scored_indices:
        raise invalid("deviation score witnesses differ from its usable scale rows")
    current = _index(primary, inputs.keys)
    if not set(current) <= set(original):
        raise invalid("selected deviation output escapes its original fit scope")
    field = "score" if declaration.view == "result" else declaration.view
    retained = _primary(
        views.take(pa.array([original[key] for key in current], type=pa.int64())),
        field,
        inputs.keys,
    )
    if declaration.table_views:
        for i, column_field in enumerate(declaration.table_views):
            expected_column = _primary(
                views.take(pa.array([original[key] for key in current], type=pa.int64())),
                column_field,
                inputs.keys,
            )
            actual_column = primary.select(
                (*inputs.keys, *(f"column_{i}__{c}" for c in ("value", "cell_tag", "cell_reason")))
            ).rename_columns((*inputs.keys, "value", "cell_tag", "cell_reason"))
            if not actual_column.equals(expected_column):
                raise invalid("terminal table column differs from its retained owned fit view")
    else:
        values_part = next((p for p in parts if p.role == "values"), None)
        actual_view = (
            values_part.table.select(
                (*inputs.keys, *("values__" + c for c in ("value", "cell_tag", "cell_reason")))
            ).rename_columns((*inputs.keys, "value", "cell_tag", "cell_reason"))
            if values_part is not None
            else primary
        )
        if not actual_view.equals(retained):
            raise invalid("deviation output differs from its retained owned view")
    for saved_part in inputs.parts:
        if retained_fit and saved_part.role in (
            "fit_inputs",
            "fit_state",
            "grid_cells",
            "subject_map",
            "finding_policy",
        ):
            continue
        original_part = load(saved_part.table)
        if saved_part.role == "coverage" and any(
            x is not True for x in original_part["coverage__complete"].to_pylist()
        ):
            raise invalid("deviation captured coverage is false")
        actual_part = next((p for p in parts if p.role == saved_part.role), None)
        if actual_part is not None:
            if saved_part.keys == inputs.keys:
                lookup = _index(original_part, inputs.keys)
                if set(lookup) != set(original):
                    raise invalid(
                        "deviation original component keys differ from its complete input"
                    )
                original_part = original_part.take(
                    pa.array([lookup[key] for key in current], type=pa.int64())
                )
            if not actual_part.table.equals(original_part):
                raise invalid(
                    "deviation retained original component differs from its captured authority"
                )
    for expected in _mapping_parts(inputs, declaration):
        actual = next((part for part in parts if part.role == expected.role), None)
        if actual is None or not actual.table.equals(expected.table):
            raise invalid("deviation original grid or Subject mapping authority differs")
    from marivo.analysis.materialization.graph_findings import POLICY, Policy

    policy_table = next(p.table for p in parts if p.role == "finding_policy")
    policy = POLICY.validate_json(policy_table["finding_policy__retained"][0].as_py(), strict=True)
    expected_policy = Policy(
        "deviation.zscore" if declaration.method == "zscore" else "deviation.mad",
        "v1",
        "graph.no_findings@v1",
        "zero_findings@v1",
        ("capture:" + state.input_digest,),
        0,
        0,
        0,
    )
    if policy != expected_policy:
        raise invalid("deviation zero-Findings policy is not bound to the original fit")


def execute(node: MethodNode, values: tuple[ExchangeResult, ...], binding: str) -> ExchangeResult:
    try:
        return _execute(node, values, binding)
    except (ValueError, OverflowError, IntegrityError, pa.ArrowInvalid, KeyError) as error:
        raise StatisticalRelationError(
            code="r8.numeric_overflow" if isinstance(error, OverflowError) else "r8.cell_policy",
            operation="deviation",
            method=str(node.method),
            input_identity=node.inputs[0].node.fingerprint,
            expected="complete typed keys, valid finite Cells, coverage and representable fitted fields",
            received=str(error),
            repair="Repair the named input or choose values whose final declared fields fit, then execute a new deviation.",
        ) from error


def validate(
    contract: ExchangeContract, primary: pa.Table, parts: tuple[ExchangePart, ...]
) -> None:
    try:
        _validate(contract, primary, parts)
    except (
        ValueError,
        ArithmeticError,
        IntegrityError,
        pa.ArrowInvalid,
        KeyError,
        IndexError,
    ) as error:
        declaration = next(p for p in contract.signature.parts if isinstance(p, FitInputsPart))
        raise StatisticalRelationError(
            code="r8.retained_part",
            operation="recover",
            method=f"deviation.{declaration.method}@v1",
            input_identity=declaration.fit_id,
            expected="the original fit scope, closed versions, receipts, equations and owned views",
            received=str(error),
            repair=f"Restore all original parts for fit {declaration.fit_id}; otherwise execute its original source input in a new Run.",
        ) from error
