"""Closed ranking and terminal-column methods over the shared graph exchange."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from fractions import Fraction
from typing import Literal

import ibis.expr.datatypes as dt
import pandas as pd
import pyarrow as pa

from marivo.analysis.core.graph import (
    Edge,
    FixedLeaf,
    MethodNode,
    Node,
    method_node,
    retained_inclusion,
    topology,
)
from marivo.analysis.core.model import (
    Coordinate,
    DisplayPart,
    Part,
    PartRole,
    TableFitsPart,
    part_role,
)
from marivo.analysis.core.rules import DisplayRank, DisplayTable, PartsTransport
from marivo.analysis.datasets.errors import DatasetConstructionError
from marivo.analysis.materialization.cell_arrow import binding as cell_table_binding
from marivo.analysis.materialization.cell_arrow import logical_schema
from marivo.analysis.materialization.cell_arrow import project as cell_project
from marivo.analysis.materialization.cell_arrow import rename as cell_rename
from marivo.analysis.materialization.cell_arrow import rows as cell_rows
from marivo.analysis.materialization.cell_arrow import validate as validate_cells
from marivo.analysis.materialization.graph_exchange import (
    ArrowPart,
    ExchangeContract,
    ExchangePart,
    ExchangeResult,
    PartContract,
    _pandas_records,
    from_pandas,
    numeric_primary,
    to_arrow,
)
from marivo.analysis.materialization.graph_relation import (
    FrozenBinding,
    LiveBinding,
    Relation,
    _retained_definition,
    _shared_root,
)
from marivo.analysis.methods.physical import DurationType, ScalarType


def invalid(
    received: str,
    *,
    expected: str = "complete typed display inputs and retained ranking scope",
    repair: str = "Use corresponding Relations in one Session and source/fixed mode; preserve the original ranking parts.",
    target: str = "dsl.table",
) -> DatasetConstructionError:
    return DatasetConstructionError(
        expected=expected,
        received=received,
        repair=repair,
        location="analysis.display",
        help_target=target,
    )


def bind(inputs: tuple[Relation, ...], params: DisplayRank | DisplayTable) -> Relation:
    if not inputs:
        raise invalid("empty table columns")
    first = inputs[0]
    if (
        isinstance(params, DisplayRank)
        and isinstance(first.root.value_type, DurationType)
        and any(part_role(p) == "condition_cells" for p in first.root.signature.parts)
    ):
        raise invalid(
            "time.runs Duration view",
            expected="the count view for run ranking",
            repair="Rank runs.count, or filter runs.duration using an exact elapsed threshold.",
            target="actions.rank",
        )
    if any(
        item.runtime.session_ref != first.runtime.session_ref
        or item.runtime.store.store_id != first.runtime.store.store_id
        for item in inputs
    ):
        raise invalid("cross-Session columns")
    if any(
        isinstance(item.binding, LiveBinding) != isinstance(first.binding, LiveBinding)
        for item in inputs
    ):
        raise invalid("mixed source/fixed columns")
    scopes = {
        item.root.signature.quantity.time_scope
        for item in inputs
        if item.root.signature.quantity is not None
    }
    if len(scopes) > 1:
        raise invalid("different column time meaning")
    known = {node.identity: node for node in topology(first.root)}
    roots = (first.root, *(_shared_root(first.root, item.root, known) for item in inputs[1:]))
    if isinstance(params, DisplayRank):
        params = replace(
            params,
            inclusion_inputs=tuple(
                i
                for i, item in enumerate(inputs[1:], 1)
                if retained_inclusion(first.root, item.root)
                or retained_inclusion(first.definition, item.definition)
            ),
        )
    endpoints: tuple[MethodNode, ...] = ()
    if isinstance(first.binding, FrozenBinding):
        closures: dict[tuple[str, ...], dict[str, tuple[Node, Node]]] = {}
        retained: list[MethodNode] = []
        execution: list[Node] = []
        for item in inputs:
            receipts = tuple(
                sorted(
                    {
                        node.artifact.ref
                        for node in topology(item.root)
                        if isinstance(node, FixedLeaf)
                    }
                )
            )
            is_view = item.root is item.definition
            endpoint = _retained_definition(
                item.definition,
                preserve_fixed=is_view,
                shared=closures.setdefault(receipts, {}),
            )
            retained.append(endpoint)
            execution.append(endpoint if is_view else item.root)
        endpoints = tuple(retained)
        roots = tuple(execution)
    node = method_node(
        tuple(
            Edge("subject" if root.signature.quantity is None else "quantity", root)
            for root in roots
        ),
        params,
        value_type=first.root.value_type,
        retained_endpoints=endpoints,
    )
    result = first._with(node)
    for item in inputs[1:]:
        result = result._with_sources(item)
    return result


def _view_roles(parts: tuple[Part, ...], name: Literal["values", "ranks"]) -> tuple[PartRole, ...]:
    return tuple(
        part_role(p)
        for p in parts
        if name == "values"
        or isinstance(p, DisplayPart)
        or part_role(p)
        in (
            "condition_cells",
            "run_cells",
            "fit_inputs",
            "fit_state",
            "pair_inputs",
            "association_state",
            "training_inputs",
            "forecast_state",
            "future_cells",
            "grid_cells",
            "subject_map",
            "finding_policy",
        )
    )


def view(relation: Relation, name: Literal["values", "ranks"]) -> Relation:
    roles = _view_roles(relation.root.signature.parts, name)
    node = method_node(
        (relation._edge(),),
        PartsTransport("view", relation.root.signature.domain, roles, True, display_view=name),
        value_type=relation.root.value_type if name == "values" else ScalarType("int64"),
    )
    return relation._with(node)


def limit(relation: Relation, count: int) -> Relation:
    if type(count) is not int or not 1 <= count <= 100000:
        raise invalid(
            repr(count),
            expected="an integer global prefix count 1..100000, excluding bool",
            repair="Choose an integer in 1..100000; use two explicit rank filters for per-partition Top-K.",
            target="dsl.Ranking.limit",
        )
    return relation._with(
        method_node(
            (relation._edge(),),
            PartsTransport(
                "limit",
                relation.root.signature.domain,
                tuple(part_role(p) for p in relation.root.signature.parts),
                True,
                limit_count=count,
            ),
            value_type=relation.root.value_type,
        )
    )


def scalar_key(
    value: object,
) -> tuple[int, int | float | Decimal | str | date | datetime | timedelta]:
    if value is None:
        return (0, 0)
    if isinstance(value, timedelta):
        nanoseconds = (
            int(value.value)
            if isinstance(value, pd.Timedelta)
            else (value.days * 86400 + value.seconds) * 1_000_000_000 + value.microseconds * 1_000
        )
        return (1, nanoseconds)
    if isinstance(value, (int, float, Decimal, str, date, datetime)) and not isinstance(
        value, bool
    ):
        return (1, value)
    if type(value) is bool:
        return (1, int(value))
    raise invalid("unsupported canonical coordinate")


def ranked(
    rows: list[dict[str, object]],
    keys: tuple[str, ...],
    order: str,
    ties: str,
    partitions: tuple[str, ...],
    coordinates: tuple[Coordinate, ...] = (),
) -> dict[tuple[object, ...], tuple[int | None, int]]:
    groups: dict[tuple[object, ...], list[dict[str, object]]] = {}
    for row in rows:
        groups.setdefault(tuple(row[p] for p in partitions), []).append(row)
    output: dict[tuple[object, ...], tuple[int | None, int]] = {}
    axes = tuple(i for i, c in enumerate(coordinates) if c.field.startswith("attribution:axis:"))
    mask_index = next(
        (i for i, c in enumerate(coordinates) if c.field == "attribution:other_mask"), None
    )

    def instance_key(
        row: dict[str, object],
    ) -> tuple[tuple[int, int | float | Decimal | str | date | datetime | timedelta], ...]:
        mask = row[keys[mask_index]] if mask_index is not None else 0
        assert isinstance(mask, int)
        return tuple(
            (2, 0) if i in axes and mask & (1 << axes.index(i)) else scalar_key(row[k])
            for i, k in enumerate(keys)
        )

    position = 0
    for partition in sorted(groups, key=lambda key: tuple(scalar_key(v) for v in key)):
        group = sorted(groups[partition], key=instance_key)
        defined = [row for row in group if row["cell_tag"] == "defined"]

        def score(row: dict[str, object]) -> Fraction:
            value = row["value"]
            if not isinstance(value, (int, float, Decimal)) or isinstance(value, bool):
                raise invalid("a Defined rank operand is not numeric")
            try:
                return Fraction(value)
            except (ValueError, OverflowError) as error:
                raise invalid("nonfinite Defined rank operand") from error

        defined.sort(key=score, reverse=order == "descending")
        index = dense = 0
        while index < len(defined):
            end = index + 1
            while end < len(defined) and score(defined[end]) == score(defined[index]):
                end += 1
            dense += 1
            for i in range(index, end):
                position += 1
                rank = (
                    i + 1
                    if ties == "ordinal"
                    else dense
                    if ties == "dense"
                    else index + 1
                    if ties == "min"
                    else end
                )
                key = tuple(defined[i][k] for k in keys)
                if key in output:
                    raise invalid("duplicate complete key during rank index insertion")
                output[key] = rank, position
            index = end
        for row in group:
            if row["cell_tag"] != "defined":
                position += 1
                key = tuple(row[k] for k in keys)
                if key in output:
                    raise invalid("duplicate complete key during rank index insertion")
                output[key] = None, position
    return output


def finish(node: MethodNode, table: pd.DataFrame, schema: pa.Schema) -> pd.DataFrame:
    """Finish only controlled display preparation; no source or Catalog access."""
    params = node.parameters
    if not isinstance(params, DisplayRank):
        return table
    keys = tuple(f"key_{i}" for i in range(len(node.signature.domain.instance_key)))
    partitions = tuple(
        f"partitions__partition_{i}" for i in range(len(params.partition_types))
    ) or ("partitions__partition",)
    rows = _pandas_records(table, schema)
    order = ranked(
        rows, keys, params.order, params.ties, partitions, node.signature.domain.instance_key
    )
    ranks = [order[tuple(row[k] for k in keys)][0] for row in rows]
    positions = [order[tuple(row[k] for k in keys)][1] for row in rows]
    table["ranks__value"] = ranks
    table["ranking_domain__rank_value"] = ranks
    table["ranking_domain__position"] = positions
    table["ordering__position"] = positions
    return table


def _logical_rows(frame: pd.DataFrame, schema: pa.Schema) -> list[dict[str, object]]:
    if cell_table_binding(schema).cells:
        return cell_rows(numeric_primary(to_arrow(frame, schema)))
    return _pandas_records(frame, schema)


def flatten(result: ExchangeResult) -> pd.DataFrame:
    primary_schema = result.contract.schema
    table_rows = _logical_rows(result.primary, primary_schema)
    keys = result.contract.key_fields
    columns = list(logical_schema(primary_schema).names)
    for part in result.parts:
        if part.role == "grid_cells" and any(
            p.role in ("condition_cells", "pair_inputs", "training_inputs") for p in result.parts
        ):
            continue
        declared_part = next(p for p in result.contract.parts if p.role == part.role)
        if declared_part.key_fields != keys:
            continue
        part_rows = _logical_rows(part.table, declared_part.schema)
        indexed = {tuple(row[k] for k in keys): row for row in part_rows}
        part_columns = logical_schema(declared_part.schema).names
        for name in part_columns:
            if name in keys or name in columns:
                continue
            columns.append(name)
            for row in table_rows:
                identity = tuple(row[k] for k in keys)
                if identity not in indexed:
                    raise invalid("retained display input is missing a complete receiver key")
                row[name] = indexed[identity][name]
    return pd.DataFrame(table_rows, columns=columns, dtype="object")


def _fixed_rows(result: ExchangeResult) -> dict[tuple[object, ...], dict[str, object]]:
    """Index decoded Cells from the checked pandas result."""
    rows: dict[tuple[object, ...], dict[str, object]] = {}
    for row in _logical_rows(result.primary, result.contract.schema):
        key = tuple(row[k] for k in result.contract.key_fields)
        if key in rows:
            raise invalid("duplicate complete key during required display index insertion")
        rows[key] = row
    return rows


def fixed(node: MethodNode, inputs: tuple[ExchangeResult, ...], binding: str) -> ExchangeResult:
    params = node.parameters
    assert isinstance(params, (DisplayRank, DisplayTable))
    first = inputs[0]
    keys = first.contract.key_fields
    table = (
        flatten(first)
        if isinstance(params, DisplayRank)
        else pd.DataFrame(
            [
                {key: row[key] for key in keys}
                for row in _logical_rows(first.primary, first.contract.schema)
            ],
            columns=keys,
            index=range(len(first.primary)),
        )
    )
    if isinstance(params, DisplayRank):
        previous_display = {
            f"{part.role}__{component}"
            for part in first.contract.signature.parts
            if isinstance(part, DisplayPart)
            for component in part.components
        }
        table = table.loc[
            :, [name for name in table.columns if name not in previous_display]
        ].copy()
    ordered_keys = tuple(
        tuple(row[k] for k in keys) for row in _logical_rows(first.primary, first.contract.schema)
    )
    receiver = set(ordered_keys)
    for i, source in enumerate(inputs):
        rows = _fixed_rows(source)
        if source.contract.key_fields != keys or (
            not receiver <= set(rows)
            if isinstance(params, DisplayRank) and i in params.inclusion_inputs
            else receiver != set(rows)
        ):
            raise invalid("display input complete keys differ")
        if isinstance(params, DisplayTable):
            for field in ("value", "cell_tag", "cell_reason"):
                table[f"column_{i}__{field}"] = pd.Series(
                    [rows[key][field] for key in ordered_keys],
                    index=table.index,
                    dtype="object",
                )
        elif i:
            if any(row["cell_tag"] not in ("defined", "null") for row in rows.values()):
                raise invalid("partition is not a complete category map")
            table[f"partitions__partition_{i - 1}"] = pd.Series(
                [rows[tuple(row[k] for k in keys)]["value"] for row in table.to_dict("records")],
                index=table.index,
            )
    if isinstance(params, DisplayRank) and not params.partition_types:
        table["partitions__partition"] = [0] * len(table)
    # Same closed component layout as source preparation.
    for signature_part in node.signature.parts:
        if not isinstance(signature_part, DisplayPart):
            continue
        for component, _dtype in zip(signature_part.components, signature_part.types, strict=True):
            name = f"{signature_part.role}__{component}"
            if name in table.columns:
                continue
            source_name = (
                component
                if signature_part.role in ("values", "ranking_domain")
                and component in ("value", "cell_tag", "cell_reason")
                else "cell_tag"
                if component in ("cell_tag", "rank_tag")
                else "cell_reason"
                if component in ("cell_reason", "rank_reason")
                else f"partitions__{component}"
                if signature_part.role == "ranking_domain" and component.startswith("partition")
                else component
                if signature_part.role == "columns"
                else None
            )
            values = (
                table[source_name].tolist()
                if source_name is not None
                else [signature_part.identity] * len(table)
                if signature_part.role == "column_bindings" and isinstance(params, DisplayTable)
                else [0] * len(table)
            )
            table[name] = pd.Series(values, index=table.index, dtype="object")
    table = finish(node, table, pa.Schema.from_pandas(table, preserve_index=False))
    primary_columns = (
        (*keys, "value", "cell_tag", "cell_reason")
        if isinstance(params, DisplayRank)
        else tuple(
            [*keys]
            + [
                f"column_{i}__{field}"
                for i in range(len(params.labels))
                for field in ("value", "cell_tag", "cell_reason")
            ]
        )
    )
    primary = table.loc[:, primary_columns].copy()
    from marivo.analysis.materialization.deviation_execution import retain_table_fits

    parts: list[ExchangePart] = []
    part_schemas: list[PartContract] = []
    logical_primary_schema = logical_schema(first.contract.schema)
    for declaration in node.signature.parts:
        role = part_role(declaration)
        if isinstance(declaration, DisplayPart):
            component_columns = tuple(
                f"{role}__{component}" for component in declaration.components
            )
            part_frame = table.loc[:, (*keys, *component_columns)].copy()
            schema = pa.schema(
                [
                    *(logical_primary_schema.field(key) for key in keys),
                    *(
                        pa.field(f"{role}__{component}", dt.dtype(dtype).to_pyarrow())
                        for component, dtype in zip(
                            declaration.components, declaration.types, strict=True
                        )
                    ),
                ]
            )
            key_fields = next(
                (item.key_fields for item in first.contract.parts if item.role == role), keys
            )
        elif isinstance(declaration, TableFitsPart):
            retained_fit = retain_table_fits(inputs, declaration)
            schema = pa.schema([pa.field("table_fits__retained", pa.string())])
            part_frame = retained_fit.table.to_pandas()
            key_fields = ()
        else:
            retained_exchange = next(item for item in first.parts if item.role == role)
            schema = next(item.schema for item in first.contract.parts if item.role == role)
            part_frame = retained_exchange.table
            key_fields = next(item.key_fields for item in first.contract.parts if item.role == role)
        parts.append(ExchangePart(role, part_frame))
        part_schemas.append(PartContract(role, schema, key_fields))
    from marivo.analysis.methods.coordinate_state import declarations as coordinate_declarations

    children = {role for role, _, _, _ in coordinate_declarations(node.signature)}
    for exchange_part in first.parts:
        if exchange_part.role in children and not any(
            item.role == exchange_part.role for item in parts
        ):
            part_schemas.append(
                next(item for item in first.contract.parts if item.role == exchange_part.role)
            )
            parts.append(exchange_part)
    key_fields_schema = [logical_primary_schema.field(key) for key in keys]
    if isinstance(params, DisplayRank):
        primary_schema = pa.schema(
            [
                *key_fields_schema,
                logical_primary_schema.field("value"),
                pa.field("cell_tag", pa.string()),
                pa.field("cell_reason", pa.string()),
            ]
        )
    else:
        primary_schema = pa.schema(
            [
                *key_fields_schema,
                *(
                    pa.field(
                        f"column_{i}__{field}",
                        logical_schema(source.contract.schema).field(field).type,
                    )
                    for i, source in enumerate(inputs)
                    for field in ("value", "cell_tag", "cell_reason")
                ),
            ]
        )
    state = pd.DataFrame(
        {
            **{key: primary[key].tolist() for key in keys},
            "status": primary["cell_tag"].tolist()
            if isinstance(params, DisplayRank)
            else ["accepted"] * len(primary),
        }
    )
    state_schema = pa.schema([*key_fields_schema, pa.field("status", pa.string())])
    contract = ExchangeContract(
        node.signature,
        node.method,
        binding,
        primary_schema,
        keys,
        tuple(part_schemas),
        first.contract.cell_reasons,
        "ranking" if isinstance(params, DisplayRank) else "table",
        state_schema,
        allow_empty_singleton=not keys,
        column_reasons=tuple(item.contract.cell_reasons for item in inputs)
        if isinstance(params, DisplayTable)
        else (),
    )
    return from_pandas(primary, contract, parts=tuple(parts), method_state=state, validate=False)


def validate(contract: ExchangeContract, primary: pa.Table, parts: tuple[ArrowPart, ...]) -> None:
    """Reproduce original ranks and verify every selected view and column."""
    by_role = {p.role: p.table for p in parts}
    keys = contract.key_fields
    declarations = {p.role: p for p in contract.signature.parts if isinstance(p, DisplayPart)}
    for role, declaration in declarations.items():
        table = by_role[role]
        schema = logical_schema(table.schema)
        if schema.names != [*keys, *(f"{role}__{c}" for c in declaration.components)]:
            raise invalid("display part columns differ")
        for component, dtype in zip(declaration.components, declaration.types, strict=True):
            if schema.field(f"{role}__{component}").type != dt.dtype(dtype).to_pyarrow():
                raise invalid(
                    f"{role}.{component}: expected {dt.dtype(dtype).to_pyarrow()}, received {schema.field(f'{role}__{component}').type}"
                )
    if "ranking_domain" in by_role:
        declaration = declarations["ranking_domain"]
        full = numeric_primary(
            cell_rename(by_role["ranking_domain"], [*keys, *(c for c in declaration.components)])
        )
        # Duration tick validation is explicit; Arrow retains the original unit.
        if pa.types.is_duration(full.schema.field("value").type):
            full = full.set_column(
                full.schema.get_field_index("value"), "value", full["value"].cast(pa.int64())
            )
        checked_cells = cell_project(full, (*keys, "value", "cell_tag", "cell_reason"))
        validate_cells(checked_cells)
        rows = cell_rows(full)
        nullable_keys = frozenset(
            f"key_{i}"
            for i, c in enumerate(contract.signature.domain.instance_key)
            if c.field.startswith("attribution:axis:")
        )
        _validate_rows(rows, keys, nullable_keys, contract.cell_reasons)
        partitions = tuple(c for c in declaration.components if c.startswith("partition"))
        expected = ranked(
            rows,
            keys,
            declaration.order,
            declaration.ties,
            partitions,
            contract.signature.domain.instance_key,
        )
        original = {tuple(row[k] for k in keys): row for row in rows}
        for role in ("partitions", "ordering"):
            role_rows = cell_rows(by_role[role])
            _validate_keys(role_rows, keys, nullable_keys)
            if {tuple(row[k] for k in keys) for row in role_rows} != set(original):
                raise invalid(
                    "independent partition or ordering keys differ from the full ranking domain"
                )
        for row in rows:
            rank, position = expected[tuple(row[k] for k in keys)]
            if (row["rank_value"], row["position"], row["rank_tag"], row["rank_reason"]) != (
                rank,
                position,
                row["cell_tag"],
                row["cell_reason"],
            ):
                raise invalid("retained ranks differ from original ranking domain")
        for role in ("values", "ranks", "partitions", "ordering"):
            actual_rows = by_role[role]
            if role == "values":
                actual_rows = cell_rename(actual_rows, [*keys, *declarations[role].components])
                actual_rows = numeric_primary(actual_rows)
            component_rows = cell_rows(actual_rows)
            _validate_keys(component_rows, keys, nullable_keys)
            if role == "values":
                _validate_cells(component_rows, contract.cell_reasons)
            for row in component_rows:
                prior = original[tuple(row[k] for k in keys)]
                for component in declarations[role].components:
                    field = (
                        "rank_value"
                        if role == "ranks" and component == "value"
                        else "rank_tag"
                        if role == "ranks" and component == "cell_tag"
                        else "rank_reason"
                        if role == "ranks" and component == "cell_reason"
                        else component
                    )
                    actual = row[component if role == "values" else f"{role}__{component}"]
                    wanted = prior[field]
                    if actual != wanted:
                        raise invalid("ranking view or ordering differs from retained scope")
        role = (
            "ranks"
            if contract.signature.quantity is not None
            and contract.signature.quantity.method_version == "display.ranks@v1"
            else "values"
        )
        view_rows = {tuple(row[k] for k in keys): row for row in cell_rows(by_role[role])}
        primary_rows = cell_rows(primary)
        _validate_rows(primary_rows, keys, nullable_keys, contract.cell_reasons)
        for row in primary_rows:
            if any(
                row[f] != view_rows[tuple(row[k] for k in keys)][f"{role}__{f}"]
                for f in ("value", "cell_tag", "cell_reason")
            ):
                raise invalid("primary differs from its exact numeric view")
    elif "columns" in by_role:
        count = len(declarations["columns"].components) // 3
        if len(contract.column_reasons) != count:
            raise invalid("terminal column Cell policies differ from ordered columns")
        bindings = declarations["column_bindings"].identity
        if any(
            value != bindings
            for value in by_role["column_bindings"]["column_bindings__identity"].to_pylist()
        ):
            raise invalid("terminal ordered column binding differs")
        for i in range(count):
            column = cell_rename(
                cell_project(
                    primary,
                    (*keys, *(f"column_{i}__{f}" for f in ("value", "cell_tag", "cell_reason"))),
                ),
                [*keys, "value", "cell_tag", "cell_reason"],
            )
            reasons = contract.column_reasons[i]
            nullable = frozenset(
                f"key_{i}"
                for i, coordinate in enumerate(contract.signature.domain.instance_key)
                if coordinate.field.startswith("attribution:axis:")
            )
            validate_cells(column)
            _validate_rows(cell_rows(column), keys, nullable, reasons)
        if cell_rows(
            cell_rename(by_role["columns"], logical_schema(primary.schema).names)
        ) != cell_rows(primary):
            raise invalid("terminal columns differ from their retained Cells")


def _validate_rows(
    rows: list[dict[str, object]],
    keys: tuple[str, ...],
    nullable_keys: frozenset[str],
    cell_reasons: tuple[tuple[str, tuple[str, ...]], ...],
) -> None:
    _validate_keys(rows, keys, nullable_keys)
    _validate_cells(rows, cell_reasons)


def _validate_keys(
    rows: list[dict[str, object]], keys: tuple[str, ...], nullable_keys: frozenset[str]
) -> None:
    seen: set[tuple[object, ...]] = set()
    for row in rows:
        identity = tuple(row[key] for key in keys)
        if any(
            value is None and key not in nullable_keys
            for key, value in zip(keys, identity, strict=True)
        ):
            raise invalid("ranking contains a null complete key")
        if identity in seen:
            raise invalid("ranking contains a duplicate complete key")
        seen.add(identity)


def _validate_cells(
    rows: list[dict[str, object]], cell_reasons: tuple[tuple[str, tuple[str, ...]], ...]
) -> None:
    reasons = dict(cell_reasons)
    for row in rows:
        value, tag, reason = row["value"], row["cell_tag"], row["cell_reason"]
        if tag == "defined":
            if value is None or reason is not None:
                raise invalid("ranking contains an invalid Defined Cell")
        elif (
            tag not in ("null", "undefined", "unknown")
            or value is not None
            or reason not in reasons.get(str(tag), ())
        ):
            raise invalid("ranking contains an invalid non-Defined Cell")


def project(source: ExchangeResult, name: Literal["values", "ranks"]) -> ExchangeResult:
    from marivo.analysis.methods.registry import REGISTRY
    from marivo.analysis.methods.semantics import MethodKey

    roles = _view_roles(source.contract.signature.parts, name)
    signature = REGISTRY.derive(
        (source.contract.signature,),
        PartsTransport("view", source.contract.signature.domain, roles, True, display_view=name),
    ).output
    from marivo.analysis.methods.coordinate_state import declarations

    retained_roles = (
        *roles,
        *(role for role, _, _, _ in declarations(signature) if role not in roles),
    )
    parts = tuple(p for p in source.parts if p.role in retained_roles)
    selected = next(p for p in parts if p.role == name)
    selected_contract = next(p for p in source.contract.parts if p.role == name)
    selected_schema = logical_schema(selected_contract.schema)
    primary = pd.DataFrame(
        [
            {
                **{key: row[key] for key in source.contract.key_fields},
                **{
                    field: row[f"{name}__{field}"] for field in ("value", "cell_tag", "cell_reason")
                },
            }
            for row in _logical_rows(selected.table, selected_contract.schema)
        ],
        columns=(*source.contract.key_fields, "value", "cell_tag", "cell_reason"),
        dtype="object",
    )
    primary_schema = pa.schema(
        [
            *(selected_schema.field(key) for key in source.contract.key_fields),
            pa.field("value", selected_schema.field(f"{name}__value").type),
            pa.field("cell_tag", pa.string()),
            pa.field("cell_reason", pa.string()),
        ]
    )
    contract = replace(
        source.contract,
        signature=signature,
        method=MethodKey("parts_transport"),
        schema=primary_schema,
        parts=tuple(p for p in source.contract.parts if p.role in retained_roles),
        state_kind="none",
        state_schema=None,
        pending_checks=(),
        _frozen=None,
    )
    return from_pandas(primary, contract, parts=parts, validate=False)
