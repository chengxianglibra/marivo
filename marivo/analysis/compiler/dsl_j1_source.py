"""Private Ibis lowering for the admitted first J1 source shapes."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, timezone

import ibis
import ibis.expr.types as ir

from marivo.analysis.compiler.errors import DatasetCompilationError
from marivo.analysis.datasets.handles import LogicalRootHandle
from marivo.analysis.observation.coordinates import relationship_columns
from marivo.analysis.observation.dsl_j1 import J1Context
from marivo.semantic.metric_graph import AggregateNodeV1, component_node
from marivo.semantic.metric_graph_lowering import normalize_target_metric
from marivo.semantic.validator import normalize_target_dimension, normalize_target_entity


def _reject(expected: str, received: str) -> DatasetCompilationError:
    return DatasetCompilationError(
        expected=expected,
        received=received,
        repair="Use the declared J1 direct-column sum on one DuckDB source with complete keys and state.",
        location="dataset.compiler.dsl_j1",
    )


@dataclass(frozen=True, slots=True)
class J1SourcePlan:
    """Ibis relations and data checks for one explicit J1 root."""

    primary: ir.Table
    parts: tuple[tuple[str, ir.Table], ...] = ()
    checks: tuple[tuple[str, ir.Table], ...] = ()


def _parameters(root: LogicalRootHandle) -> tuple[object, ...]:
    if type(root.parameters) is not tuple:
        raise _reject("canonical J1 parameters", "invalid root parameters")
    return root.parameters


def _parent(root: LogicalRootHandle) -> LogicalRootHandle:
    if len(root.inputs) != 1 or not isinstance(root.inputs[0].root, LogicalRootHandle):
        raise _reject("one exact J1 logical input", root.operator_id)
    return root.inputs[0].root


def _table(tables: Mapping[str, ir.Table], entity: str) -> ir.Table:
    table = tables.get(entity)
    if table is None:
        raise _reject("one bound declared Entity source", entity)
    return table


def _physical(table: ir.Table, column: str, allowed: tuple[str, ...]) -> None:
    if column not in table.columns:
        raise _reject(f"declared physical column {column}", "missing column")
    actual = str(table[column].type())
    if actual not in allowed:
        raise _reject(f"physical {' or '.join(allowed)} {column}", actual)


def _utc_bound(raw: str) -> datetime:
    value = date.fromisoformat(raw) if len(raw) == 10 else datetime.fromisoformat(raw)
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return datetime.combine(value, time(), tzinfo=timezone.utc)


def _finished(table: ir.Table, keys: tuple[str, ...]) -> ir.Table:
    support = table["non_null_count"].fill_null(0)
    rows = table["row_count"].fill_null(0)
    value = ibis.ifelse(support > 0, table["state_sum"], ibis.null())
    return table.select(
        *(table[key] for key in keys),
        value=value,
        cell_tag=ibis.ifelse(support > 0, "defined", "null"),
        cell_reason=ibis.ifelse(support > 0, ibis.null(), "empty_contribution"),
        state_sum=table["state_sum"],
        non_null_count=support,
        row_count=rows,
    )


def _grouped_sum(rows: ir.Table, keys: tuple[str, ...]) -> ir.Table:
    return rows.group_by(*(rows[key] for key in keys)).aggregate(
        state_sum=rows["amount"].sum(),
        non_null_count=rows["amount"].count(),
        row_count=rows.count(),
    )


def _merge_state(rows: ir.Table, keys: tuple[str, ...]) -> ir.Table:
    return rows.group_by(*(rows[key] for key in keys)).aggregate(
        state_sum=rows.state_sum.sum(),
        non_null_count=rows.non_null_count.sum(),
        row_count=rows.row_count.sum(),
    )


def _member_group_mapping(
    context: J1Context, root: LogicalRootHandle, tables: Mapping[str, ir.Table]
) -> tuple[ir.Table, tuple[tuple[str, ir.Table], ...]]:
    parameters = _parameters(root)
    dimension_path = parameters[0]
    if not isinstance(dimension_path, str):
        raise _reject("bound group Dimension", root.operator_id)
    dimension = normalize_target_dimension(context.registry, dimension_path)
    member_root = _parent(root)
    if member_root.operator_id == "dsl.j1.read":
        read = lower_j1_source(context, member_root, tables)
        mapping = read.primary.select(member=read.primary.member, group=read.primary.value)
        checks = read.checks
    else:
        members = lower_j1_source(context, member_root, tables)
        entity = normalize_target_entity(context.registry, dimension.entity_ref.path)
        source = _table(tables, entity.ref.path)
        key = entity.primary_key[0]
        _physical(source, key, ("string", "int64"))
        _physical(source, dimension.source_column, ("string",))
        joined = members.primary.join(source, members.primary.member == source[key], how="inner")
        mapping = joined.select(
            member=members.primary.member, group=source[dimension.source_column]
        )
        checks = members.checks
    null_rows = mapping.filter(mapping.group.isnull())
    nulls = null_rows.aggregate(invalid=null_rows.count())
    return mapping, (*checks, ("strict_group_cell", nulls))


def lower_j1_source(
    context: J1Context, root: LogicalRootHandle, tables: Mapping[str, ir.Table]
) -> J1SourcePlan:
    """Lower a W1 root to Ibis without opening any business source."""
    if root.session_id != context.session_id or root.store_id != context.store_id:
        raise _reject("a J1 root from this Session", "foreign root")
    operation = root.operator_id
    parameters = _parameters(root)
    if operation == "dsl.j1.members":
        entity_path = parameters[0]
        if not isinstance(entity_path, str):
            raise _reject("bound Entity path", "invalid member root")
        entity = normalize_target_entity(context.registry, entity_path)
        table = _table(tables, entity_path)
        _physical(table, entity.primary_key[0], ("string", "int64"))
        return J1SourcePlan(table.select(member=table[entity.primary_key[0]]))
    if operation == "dsl.j1.read":
        dimension_path = parameters[0]
        if not isinstance(dimension_path, str):
            raise _reject("bound Dimension path", "invalid read root")
        dimension = normalize_target_dimension(context.registry, dimension_path)
        entity = normalize_target_entity(context.registry, dimension.entity_ref.path)
        source = _table(tables, entity.ref.path)
        _physical(source, entity.primary_key[0], ("string", "int64"))
        _physical(source, dimension.source_column, ("string",))
        members = lower_j1_source(context, _parent(root), tables)
        joined = members.primary.join(
            source, members.primary.member == source[entity.primary_key[0]], how="inner"
        )
        return J1SourcePlan(
            joined.select(
                member=members.primary.member,
                value=source[dimension.source_column],
                cell_tag=ibis.ifelse(source[dimension.source_column].notnull(), "defined", "null"),
                cell_reason=ibis.ifelse(
                    source[dimension.source_column].notnull(), ibis.null(), "source_null"
                ),
            ),
            checks=members.checks,
        )
    if operation == "dsl.j1.where":
        value = parameters[1]
        if not isinstance(value, str):
            raise _reject("bound string category", "invalid predicate")
        read = lower_j1_source(context, _parent(root), tables)
        invalid_rows = read.primary.filter(read.primary.cell_tag != "defined")
        invalid = invalid_rows.aggregate(invalid=invalid_rows.count())
        selected = read.primary.filter(read.primary.value == value).select(
            member=read.primary.member
        )
        return J1SourcePlan(selected, checks=(*read.checks, ("strict_category_cell", invalid)))
    if operation == "dsl.j1.group":
        if len(parameters) == 2 and parameters[1] == "contribution":
            observed = lower_j1_source(context, _parent(root), tables)
            coordinate = next((part for role, part in observed.parts if role == "coordinate"), None)
            if coordinate is None:
                raise _reject("retained contribution coordinate state", "missing coordinate")
            grouped = _merge_state(coordinate, ("group",))
            return J1SourcePlan(_finished(grouped, ("group",)), checks=observed.checks)
        mapping, checks = _member_group_mapping(context, root, tables)
        return J1SourcePlan(mapping.select(group=mapping.group).distinct(), checks=checks)
    if operation == "dsl.j1.observe":
        if len(parameters) != 5:
            raise _reject("five bound observation facts", "invalid observation")
        metric_path, _, relationship_path, scope_json, coordinates = parameters
        if (
            not isinstance(metric_path, str)
            or not isinstance(relationship_path, str)
            or not isinstance(scope_json, str)
        ):
            raise _reject("bound J1 Metric, path and scope", "invalid observation")
        if type(coordinates) is not tuple or any(not isinstance(item, str) for item in coordinates):
            raise _reject("bound coordinate paths", "invalid observation")
        coordinate_paths = tuple(item for item in coordinates if isinstance(item, str))
        metric = normalize_target_metric(context.registry, metric_path, sidecar=context.sidecar)
        if (
            metric.logical_type not in ("unknown", "int64", "float64")
            or len(metric.components) != 1
        ):
            raise _reject("int64/float64 single-root sum", metric.logical_type)
        node = component_node(metric.graph, metric.components[0].node_id)
        if not isinstance(node, AggregateNodeV1) or node.agg != "sum" or node.filter:
            raise _reject("direct-column unsliced sum", type(node).__name__)
        body = next(
            (
                body
                for ref, body in context.sidecar.bodies.items()
                if ref.path == node.target_ref.path and ref.kind == node.target_ref.kind
            ),
            None,
        )
        if body is None or body.source_column is None:
            raise _reject("a bound direct-column Measure", node.target_ref.path)
        relationship = context.registry.relationships[relationship_path]
        source = _table(tables, relationship.from_entity)
        _physical(source, body.source_column, ("int64", "float64"))
        if metric.event_time_dimension is None:
            raise _reject("declared event time", metric_path)
        event = normalize_target_dimension(context.registry, metric.event_time_dimension.path)
        if event.timezone != "UTC":
            raise _reject("UTC event time in first J1 source route", str(event.timezone))
        scope: object = json.loads(scope_json)
        if (
            not isinstance(scope, dict)
            or not isinstance(scope.get("start"), str)
            or not isinstance(scope.get("end"), str)
        ):
            raise _reject("fixed half-open scope", "invalid scope")
        start = scope["start"]
        end = scope["end"]
        if not isinstance(start, str) or not isinstance(end, str):
            raise _reject("fixed half-open scope", "invalid scope")
        source = source.filter(
            (source[event.source_column] >= _utc_bound(start))
            & (source[event.source_column] < _utc_bound(end))
        )
        input_root = _parent(root)
        if input_root.operator_id == "dsl.j1.group":
            mapping, checks = _member_group_mapping(context, input_root, tables)
            keys = ("group",)
            targets = mapping.select(group=mapping.group).distinct()
        else:
            members = lower_j1_source(context, input_root, tables)
            mapping = members.primary.select(member=members.primary.member)
            checks = members.checks
            keys = ("member",)
            targets = mapping
        join_columns = relationship_columns(context.registry, relationship)
        if len(join_columns) != 1:
            raise _reject("one declared Buyer key", relationship_path)
        from_column, _ = join_columns[0]
        _physical(source, from_column, ("string", "int64"))
        if len(coordinate_paths) > 1:
            raise _reject("one contribution coordinate", str(len(coordinate_paths)))
        coordinate_columns: tuple[ir.Value, ...] = ()
        if coordinate_paths:
            coordinate = normalize_target_dimension(context.registry, coordinate_paths[0])
            _physical(source, coordinate.source_column, ("string",))
            coordinate_columns = (source[coordinate.source_column].name("group"),)
        joined = source.join(mapping, source[from_column] == mapping.member, how="inner")
        amount = source[body.source_column]
        contributions = joined.select(
            *(mapping[key] for key in keys),
            *coordinate_columns,
            amount=amount,
        )
        summed = _grouped_sum(contributions, keys)
        dense = targets.left_join(summed, [targets[key] == summed[key] for key in keys]).select(
            *(targets[key] for key in keys),
            state_sum=summed.state_sum,
            non_null_count=summed.non_null_count,
            row_count=summed.row_count,
        )
        selected_source = source.filter(source[from_column].isin(mapping.member))
        actual_contributions = joined.aggregate(actual=joined.count())
        expected_contributions = selected_source.aggregate(expected=selected_source.count())
        partition_counts = actual_contributions.cross_join(expected_contributions)
        partition_check = partition_counts.select(
            invalid=(partition_counts.actual != partition_counts.expected).cast("int64")
        )
        actual_coverage = dense.aggregate(actual=dense.count())
        expected_coverage = targets.aggregate(expected=targets.count())
        coverage_counts = actual_coverage.cross_join(expected_coverage)
        coverage_check = coverage_counts.select(
            invalid=(coverage_counts.actual != coverage_counts.expected).cast("int64")
        )
        checks = (
            *checks,
            ("contribution_partition", partition_check),
            ("complete_coverage", coverage_check),
        )
        if str(contributions.amount.type()) == "float64":
            nonfinite = contributions.filter(
                contributions.amount.isnan() | contributions.amount.isinf()
            )
            checks = (
                *checks,
                ("finite_contribution", nonfinite.aggregate(invalid=nonfinite.count())),
            )
        parts: tuple[tuple[str, ir.Table], ...] = ()
        if coordinate_paths:
            invalid_rows = contributions.filter(contributions.group.isnull())
            invalid_coordinate = invalid_rows.aggregate(invalid=invalid_rows.count())
            checks = (*checks, ("strict_coordinate_cell", invalid_coordinate))
            parts = (("coordinate", _grouped_sum(contributions, ("member", "group"))),)
        return J1SourcePlan(_finished(dense, keys), parts, checks)
    if operation == "dsl.j1.rollup":
        prior = lower_j1_source(context, _parent(root), tables)
        table = prior.primary
        summed = table.aggregate(
            state_sum=table.state_sum.sum(),
            non_null_count=table.non_null_count.sum(),
            row_count=table.row_count.sum(),
        )
        return J1SourcePlan(_finished(summed, ()), checks=prior.checks)
    if operation == "dsl.j1.summarize":
        method = parameters[0]
        if method not in ("sum", "count", "mean"):
            raise _reject("current-row sum/count/mean", str(method))
        prior = lower_j1_source(context, _parent(root), tables)
        table = prior.primary
        checks = prior.checks
        if method != "count":
            invalid_rows = table.filter(table.cell_tag != "defined")
            invalid = invalid_rows.aggregate(invalid=invalid_rows.count())
            checks = (*checks, ("strict_current_row_cell", invalid))
        state = table.aggregate(current_sum=table.value.sum(), current_count=table.count())
        if method == "count":
            result = state.select(
                value=state.current_count,
                cell_tag=ibis.literal("defined"),
                cell_reason=ibis.null().cast("string"),
                current_count=state.current_count,
            )
        elif method == "sum":
            result = state.select(
                value=state.current_sum.fill_null(0),
                cell_tag=ibis.literal("defined"),
                cell_reason=ibis.null().cast("string"),
                current_sum=state.current_sum.fill_null(0),
            )
        else:
            result = state.select(
                value=ibis.ifelse(
                    state.current_count > 0,
                    state.current_sum.cast("float64") / state.current_count.cast("float64"),
                    ibis.null(),
                ),
                cell_tag=ibis.ifelse(state.current_count > 0, "defined", "undefined"),
                cell_reason=ibis.ifelse(state.current_count > 0, ibis.null(), "empty_mean"),
                current_sum=state.current_sum.fill_null(0),
                current_count=state.current_count,
            )
        return J1SourcePlan(result, checks=checks)
    raise _reject("an implemented J1 operator", operation)
