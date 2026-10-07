"""Closed ranking and terminal-column methods over the shared graph exchange."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from fractions import Fraction
from typing import Literal

import ibis.expr.datatypes as dt
import pandas as pd
import pyarrow as pa

from marivo.analysis.core.graph import (
    Edge,
    MethodNode,
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
from marivo.analysis.materialization.graph_exchange import (
    ExchangeContract,
    ExchangePart,
    ExchangeResult,
    PartContract,
    from_arrow,
    numeric_primary,
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
    node = method_node(
        tuple(
            Edge("subject" if root.signature.quantity is None else "quantity", root)
            for root in roots
        ),
        params,
        value_type=first.root.value_type,
        retained_endpoints=tuple(_retained_definition(item.definition) for item in inputs)
        if isinstance(first.binding, FrozenBinding)
        else (),
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


def scalar_key(value: object) -> tuple[int, int | float | Decimal | str | date | datetime]:
    if value is None:
        return (0, 0)
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
    ) -> tuple[tuple[int, int | float | Decimal | str | date | datetime], ...]:
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
                output[tuple(defined[i][k] for k in keys)] = rank, position
            index = end
        for row in group:
            if row["cell_tag"] != "defined":
                position += 1
                output[tuple(row[k] for k in keys)] = None, position
    return output


def finish(node: MethodNode, table: pa.Table) -> pa.Table:
    """Finish only controlled display preparation; no source or Catalog access."""
    params = node.parameters
    if not isinstance(params, DisplayRank):
        return table
    keys = tuple(f"key_{i}" for i in range(len(node.signature.domain.instance_key)))
    partitions = tuple(
        f"partitions__partition_{i}" for i in range(len(params.partition_types))
    ) or ("partitions__partition",)
    rows = numeric_primary(table).to_pylist()
    order = ranked(
        rows, keys, params.order, params.ties, partitions, node.signature.domain.instance_key
    )
    ranks = [order[tuple(row[k] for k in keys)][0] for row in rows]
    positions = [order[tuple(row[k] for k in keys)][1] for row in rows]
    for name, data, dtype in (
        ("ranks__value", ranks, pa.int64()),
        ("ranking_domain__rank_value", ranks, pa.int64()),
        ("ranking_domain__position", positions, pa.int64()),
        ("ordering__position", positions, pa.int64()),
    ):
        table = table.set_column(
            table.schema.get_field_index(name), name, pa.array(data, type=dtype)
        )
    return table


def flatten(result: ExchangeResult) -> pa.Table:
    table = result.primary
    keys = result.contract.key_fields
    for part in result.parts:
        if part.role == "grid_cells" and any(
            p.role in ("condition_cells", "pair_inputs", "training_inputs") for p in result.parts
        ):
            continue
        if next(p.key_fields for p in result.contract.parts if p.role == part.role) != keys:
            continue
        rows = {tuple(row[k] for k in keys): row for row in part.table.to_pylist()}
        for field in part.table.schema:
            if field.name not in keys and field.name not in table.column_names:
                table = table.append_column(
                    field,
                    pa.array(
                        [
                            rows[tuple(row[k] for k in keys)][field.name]
                            for row in table.to_pylist()
                        ],
                        type=field.type,
                    ),
                )
    return table


def _fixed_rows(result: ExchangeResult) -> dict[tuple[object, ...], dict[str, object]]:
    """Read checked Arrow Cells through the controlled exact pandas carrier."""
    frame = numeric_primary(result.primary).to_pandas(types_mapper=pd.ArrowDtype)
    rows: dict[tuple[object, ...], dict[str, object]] = {}
    for index in range(len(frame)):
        row: dict[str, object] = {}
        for name in result.primary.column_names:
            value: object = frame.at[index, name]
            row[name] = None if value is pd.NA or value is pd.NaT else value
        rows[tuple(row[k] for k in result.contract.key_fields)] = row
    return rows


def fixed(node: MethodNode, inputs: tuple[ExchangeResult, ...], binding: str) -> ExchangeResult:
    params = node.parameters
    assert isinstance(params, (DisplayRank, DisplayTable))
    first = inputs[0]
    keys = first.contract.key_fields
    table = flatten(first) if isinstance(params, DisplayRank) else first.primary.select(keys)
    if isinstance(params, DisplayRank):
        previous_display = {
            f"{part.role}__{component}"
            for part in first.contract.signature.parts
            if isinstance(part, DisplayPart)
            for component in part.components
        }
        table = table.select([name for name in table.column_names if name not in previous_display])
    receiver = {tuple(row[k] for k in keys) for row in first.primary.to_pylist()}
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
                table = table.append_column(
                    f"column_{i}__{field}",
                    pa.array(
                        [rows[tuple(row[k] for k in keys)][field] for row in table.to_pylist()],
                        type=source.primary.schema.field(field).type,
                    ),
                )
        elif i:
            if any(row["cell_tag"] not in ("defined", "null") for row in rows.values()):
                raise invalid("partition is not a complete category map")
            table = table.append_column(
                f"partitions__partition_{i - 1}",
                pa.array(
                    [rows[tuple(row[k] for k in keys)]["value"] for row in table.to_pylist()],
                    type=source.primary.schema.field("value").type,
                ),
            )
    if isinstance(params, DisplayRank) and not params.partition_types:
        table = table.append_column(
            "partitions__partition", pa.array([0] * table.num_rows, type=pa.int64())
        )
    # Same closed component layout as source preparation.
    for part in node.signature.parts:
        if not isinstance(part, DisplayPart):
            continue
        for component, dtype in zip(part.components, part.types, strict=True):
            name = f"{part.role}__{component}"
            if name in table.column_names:
                continue
            source_name = (
                component
                if part.role in ("values", "ranking_domain")
                and component in ("value", "cell_tag", "cell_reason")
                else "cell_tag"
                if component in ("cell_tag", "rank_tag")
                else "cell_reason"
                if component in ("cell_reason", "rank_reason")
                else f"partitions__{component}"
                if part.role == "ranking_domain" and component.startswith("partition")
                else component
                if part.role == "columns"
                else None
            )
            values = (
                table[source_name]
                if source_name is not None
                else pa.array([part.identity] * table.num_rows, type=pa.string())
                if part.role == "column_bindings" and isinstance(params, DisplayTable)
                else pa.array([0] * table.num_rows, type=dt.dtype(dtype).to_pyarrow())
            )
            table = table.append_column(name, values)
    table = finish(node, table)
    primary_columns = (
        (*keys, "value", "cell_tag", "cell_reason")
        if isinstance(params, DisplayRank)
        else (
            *keys,
            *(
                f"column_{i}__{f}"
                for i in range(len(params.labels))
                for f in ("value", "cell_tag", "cell_reason")
            ),
        )
    )
    primary = table.select(primary_columns)
    from marivo.analysis.materialization.deviation_execution import retain_table_fits

    parts = tuple(
        ExchangePart(
            part_role(p), table.select((*keys, *(f"{part_role(p)}__{c}" for c in p.components)))
        )
        if isinstance(p, DisplayPart)
        else retain_table_fits(inputs, p)
        if isinstance(p, TableFitsPart)
        else next(part for part in first.parts if part.role == part_role(p))
        for p in node.signature.parts
    )
    state = pa.table(
        {
            **{k: primary[k] for k in keys},
            "status": primary["cell_tag"]
            if isinstance(params, DisplayRank)
            else pa.array(["accepted"] * primary.num_rows, type=pa.string()),
        }
    )
    reasons = first.contract.cell_reasons
    contract = ExchangeContract(
        node.signature,
        node.method,
        binding,
        primary.schema,
        keys,
        tuple(
            PartContract(
                p.role,
                p.table.schema,
                next(
                    (item.key_fields for item in first.contract.parts if item.role == p.role),
                    () if p.role == "table_fits" else keys,
                ),
            )
            for p in parts
        ),
        reasons,
        "ranking" if isinstance(params, DisplayRank) else "table",
        state.schema,
        allow_empty_singleton=not keys,
        column_reasons=tuple(item.contract.cell_reasons for item in inputs)
        if isinstance(params, DisplayTable)
        else (),
    )
    return from_arrow(primary, contract, parts=parts, method_state=state)


def validate(
    contract: ExchangeContract, primary: pa.Table, parts: tuple[ExchangePart, ...]
) -> None:
    """Reproduce original ranks and verify every selected view and column."""
    from marivo.analysis.materialization.graph_exchange import CheckedStream, _TableStream

    by_role = {p.role: p.table for p in parts}
    keys = contract.key_fields
    declarations = {p.role: p for p in contract.signature.parts if isinstance(p, DisplayPart)}
    for role, declaration in declarations.items():
        table = by_role[role]
        if table.column_names != [*keys, *(f"{role}__{c}" for c in declaration.components)]:
            raise invalid("display part columns differ")
        for component, dtype in zip(declaration.components, declaration.types, strict=True):
            if table.schema.field(f"{role}__{component}").type != dt.dtype(dtype).to_pyarrow():
                raise invalid(
                    f"{role}.{component}: expected {dt.dtype(dtype).to_pyarrow()}, received {table.schema.field(f'{role}__{component}').type}"
                )
    if "ranking_domain" in by_role:
        declaration = declarations["ranking_domain"]
        full = numeric_primary(
            by_role["ranking_domain"].rename_columns([*keys, *(c for c in declaration.components)])
        )
        # Duration tick validation is explicit; Arrow retains the original unit.
        if pa.types.is_duration(full.schema.field("value").type):
            full = full.set_column(
                full.schema.get_field_index("value"), "value", full["value"].cast(pa.int64())
            )
        checked_cells = full.select((*keys, "value", "cell_tag", "cell_reason"))
        stream = CheckedStream(
            _TableStream(checked_cells),
            checked_cells.schema,
            keys,
            contract.cell_reasons,
            nullable_keys=frozenset(
                f"key_{i}"
                for i, c in enumerate(contract.signature.domain.instance_key)
                if c.field.startswith("attribution:axis:")
            ),
        )
        tuple(stream)
        rows = full.to_pylist()
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
            if {tuple(row[k] for k in keys) for row in by_role[role].to_pylist()} != set(original):
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
                actual_rows = actual_rows.rename_columns([*keys, *declarations[role].components])
                actual_rows = numeric_primary(actual_rows)
            for row in actual_rows.to_pylist():
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
        view_rows = {tuple(row[k] for k in keys): row for row in by_role[role].to_pylist()}
        for row in primary.to_pylist():
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
            column = primary.select(
                (*keys, *(f"column_{i}__{f}" for f in ("value", "cell_tag", "cell_reason")))
            ).rename_columns([*keys, "value", "cell_tag", "cell_reason"])
            reasons = contract.column_reasons[i]
            nullable = frozenset(
                f"key_{i}"
                for i, coordinate in enumerate(contract.signature.domain.instance_key)
                if coordinate.field.startswith("attribution:axis:")
            )
            tuple(
                CheckedStream(
                    _TableStream(column), column.schema, keys, reasons, nullable_keys=nullable
                )
            )
        if (
            by_role["columns"].rename_columns(primary.column_names).to_pylist()
            != primary.to_pylist()
        ):
            raise invalid("terminal columns differ from their retained Cells")


def project(source: ExchangeResult, name: Literal["values", "ranks"]) -> ExchangeResult:
    from marivo.analysis.methods.registry import REGISTRY
    from marivo.analysis.methods.semantics import MethodKey

    roles = _view_roles(source.contract.signature.parts, name)
    signature = REGISTRY.derive(
        (source.contract.signature,),
        PartsTransport("view", source.contract.signature.domain, roles, True, display_view=name),
    ).output
    parts = tuple(p for p in source.parts if p.role in roles)
    selected = next(p.table for p in parts if p.role == name)
    primary = selected.rename_columns(
        [*source.contract.key_fields, "value", "cell_tag", "cell_reason"]
    )
    contract = replace(
        source.contract,
        signature=signature,
        method=MethodKey("parts_transport"),
        schema=primary.schema,
        parts=tuple(p for p in source.contract.parts if p.role in roles),
        state_kind="none",
        state_schema=None,
        pending_checks=(),
        _frozen=None,
    )
    return from_arrow(primary, contract, parts=parts, validate=False)
