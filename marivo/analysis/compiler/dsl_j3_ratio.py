"""Private J3 component-ratio lowering on the existing J1 execution seam."""

from __future__ import annotations

import json
from collections.abc import Mapping

import ibis
import ibis.expr.types as ir

from marivo.analysis.compiler.dsl_j1_source import J1SourcePlan, _utc_bound, lower_j1_source
from marivo.analysis.compiler.errors import DatasetCompilationError
from marivo.analysis.datasets.handles import LogicalRootHandle, _RunNodeBindings
from marivo.analysis.observation.coordinates import relationship_columns
from marivo.analysis.observation.dsl_j1 import J3_RATIO_CHECKS, J1Context
from marivo.refs import SemanticKind
from marivo.semantic.metric_graph import AggregateNodeV1, TargetMetricComponent, component_node
from marivo.semantic.metric_graph_lowering import normalize_target_metric
from marivo.semantic.validator import normalize_target_dimension, normalize_target_entity


def _fail(expected: str, received: str) -> DatasetCompilationError:
    return DatasetCompilationError(
        expected=expected,
        received=received,
        repair="Use the exact declared ratio components, source routes and complete typed inputs.",
        location="dataset.compiler.dsl_j3_ratio",
    )


def _identity_check(source: ir.Table, column: str) -> ir.Table:
    counts = source.group_by(source[column]).aggregate(identity_count=source.count())
    invalid = counts.filter((counts.identity_count > 1) | counts[column].isnull())
    return invalid.aggregate(invalid=invalid.count())


def _combine_checks(checks: list[ir.Table]) -> ir.Table:
    combined = checks[0]
    for check in checks[1:]:
        combined = combined.cross_join(check).select(invalid=combined.invalid + check.invalid)
    return combined


def _path_rows(
    context: J1Context,
    component: TargetMetricComponent,
    node: AggregateNodeV1,
    route: tuple[str, ...],
    coordinates: tuple[str, ...],
    tables: Mapping[str, ir.Table],
    start: object,
    end: object,
) -> tuple[ir.Table, ir.Table, ir.Table]:
    registry = context.registry
    root_path = component.computation_root.path
    root = normalize_target_entity(registry, root_path)
    if len(root.primary_key) != 1 or root_path not in tables:
        raise _fail("single-key bound component source", root_path)
    source = tables[root_path]
    partition_checks = [_identity_check(source, root.primary_key[0])]
    fields: dict[str, ir.Value] = {"root_id": source[root.primary_key[0]]}
    if node.agg == "sum":
        body = next(
            (
                body
                for ref, body in context.sidecar.bodies.items()
                if ref.kind is SemanticKind.MEASURE and ref.path == node.target_ref.path
            ),
            None,
        )
        if body is None or body.source_column is None or body.source_column not in source.columns:
            raise _fail("direct numeric Measure column", node.target_ref.path)
        if str(source[body.source_column].type()) not in ("int64", "float64"):
            raise _fail("int64 or float64 numerator", str(source[body.source_column].type()))
        fields["amount"] = source[body.source_column]
    time_ref = component.event_time_dimension
    if time_ref is None:
        raise _fail("declared component event time", root_path)
    time_owner = registry.dimensions[time_ref.path].entity
    if time_owner == root_path:
        axis = normalize_target_dimension(registry, time_ref.path)
        if axis.timezone != "UTC":
            raise _fail("UTC component event time", time_ref.path)
        fields["event_time"] = source[axis.source_column]
    for index, path in enumerate(coordinates):
        axis = normalize_target_dimension(registry, path)
        if axis.entity_ref.path == root_path:
            if (
                axis.source_column not in source.columns
                or str(source[axis.source_column].type()) != "string"
            ):
                raise _fail("string contribution coordinate", path)
            fields[f"coord_{index}"] = source[axis.source_column]
    if route:
        first = registry.relationships[route[0]]
        fields["next_key"] = source[relationship_columns(registry, first)[0][0]]
    else:
        fields["member"] = source[root.primary_key[0]]
    rows = source.select(**fields)
    missing_checks: list[ir.Table] = []
    current = root_path
    for index, relationship_id in enumerate(route):
        relationship = registry.relationships.get(relationship_id)
        if (
            relationship is None
            or relationship.from_entity != current
            or len(relationship_columns(registry, relationship)) != 1
        ):
            raise _fail("one continuous functional relationship path", relationship_id)
        destination = normalize_target_entity(registry, relationship.to_entity)
        target = tables.get(destination.ref.path)
        if target is None or len(destination.primary_key) != 1:
            raise _fail("bound single-key path destination", destination.ref.path)
        partition_checks.append(_identity_check(target, destination.primary_key[0]))
        _from_column, to_column = relationship_columns(registry, relationship)[0]
        if to_column != destination.primary_key[0]:
            raise _fail("to-one destination identity", relationship_id)
        joined = rows.left_join(target, rows.next_key == target[to_column])
        missing = joined.filter(target[to_column].isnull())
        missing_checks.append(missing.aggregate(invalid=missing.count()))
        selected: dict[str, ir.Value] = {
            name: rows[name] for name in rows.columns if name != "next_key"
        }
        if destination.ref.path == time_owner:
            axis = normalize_target_dimension(registry, time_ref.path)
            if axis.timezone != "UTC" or axis.source_column not in target.columns:
                raise _fail("UTC component time on path", time_ref.path)
            selected["event_time"] = target[axis.source_column]
        for coord_index, path in enumerate(coordinates):
            axis = normalize_target_dimension(registry, path)
            if axis.entity_ref.path == destination.ref.path:
                if (
                    axis.source_column not in target.columns
                    or str(target[axis.source_column].type()) != "string"
                ):
                    raise _fail("string contribution coordinate", path)
                selected[f"coord_{coord_index}"] = target[axis.source_column]
        if index + 1 < len(route):
            next_relation = registry.relationships[route[index + 1]]
            selected["next_key"] = target[relationship_columns(registry, next_relation)[0][0]]
        else:
            selected["member"] = target[to_column]
        rows = joined.select(**selected)
        current = destination.ref.path
    if (
        "event_time" not in rows.columns
        or "member" not in rows.columns
        or any(f"coord_{index}" not in rows.columns for index in range(len(coordinates)))
    ):
        raise _fail("complete time, member and coordinate path", root_path)
    rows = rows.filter((rows.event_time >= start) & (rows.event_time < end))
    invalid = rows.filter(rows.member.isnull())
    for index in range(len(coordinates)):
        invalid = invalid.union(rows.filter(rows[f"coord_{index}"].isnull()), distinct=True)
    coverage = _combine_checks([invalid.aggregate(invalid=invalid.count()), *missing_checks])
    return rows, coverage, _combine_checks(partition_checks)


def _finish(state: ir.Table, keys: tuple[str, ...]) -> ir.Table:
    denominator = state.denominator_count
    value = ibis.ifelse(
        denominator > 0,
        state.numerator_sum.cast("float64") / denominator.cast("float64"),
        ibis.null().cast("float64"),
    )
    return state.select(
        *(state[key] for key in keys),
        value=value,
        cell_tag=ibis.ifelse(denominator > 0, "defined", "undefined"),
        cell_reason=ibis.ifelse(denominator > 0, ibis.null(), "zero_denominator"),
        numerator_sum=state.numerator_sum,
        numerator_non_null_count=state.numerator_non_null_count,
        numerator_row_count=state.numerator_row_count,
        denominator_count=denominator,
        denominator_row_count=state.denominator_row_count,
    )


def lower_j3_ratio(
    context: J1Context,
    root: LogicalRootHandle,
    tables: Mapping[str, ir.Table],
    memo: _RunNodeBindings[J1SourcePlan],
) -> J1SourcePlan:
    """Lower one admitted ratio observation or original-state rollup with Ibis."""
    if root.operator_id == "dsl.j1.ratio_rollup":
        if len(root.inputs) != 1 or not isinstance(root.inputs[0].root, LogicalRootHandle):
            raise _fail("one ratio predecessor", root.operator_id)
        observed = lower_j1_source(context, root.inputs[0].root, tables, memo=memo)
        params = root.parameters
        if type(params) is not tuple or len(params) != 3:
            raise _fail("bound ratio rollup target", root.operator_id)
        _metric_path, target, path = params
        source = observed.primary
        keys: tuple[str, ...] = ()
        if target == "group" and isinstance(path, str):
            origin = root.inputs[0].root
            original = origin.parameters if type(origin.parameters) is tuple else ()
            coordinates = original[4] if len(original) == 5 else ()
            if not isinstance(coordinates, tuple) or path not in coordinates:
                raise _fail("retained ratio coordinate", str(path))
            index = coordinates.index(path)
            source = source.mutate(group=source[f"coord_{index}"])
            keys = ("group",)
        elif target != "singleton":
            raise _fail("group or singleton ratio rollup", str(target))
        grouped = (
            source.group_by(*(source[key] for key in keys)).aggregate(
                numerator_sum=source.numerator_sum.sum(),
                numerator_non_null_count=source.numerator_non_null_count.sum(),
                numerator_row_count=source.numerator_row_count.sum(),
                denominator_count=source.denominator_count.sum(),
                denominator_row_count=source.denominator_row_count.sum(),
            )
            if keys
            else source.aggregate(
                numerator_sum=source.numerator_sum.sum(),
                numerator_non_null_count=source.numerator_non_null_count.sum(),
                numerator_row_count=source.numerator_row_count.sum(),
                denominator_count=source.denominator_count.sum(),
                denominator_row_count=source.denominator_row_count.sum(),
            )
        )
        state = grouped.mutate(
            numerator_sum=grouped.numerator_sum.fill_null(0),
            numerator_non_null_count=grouped.numerator_non_null_count.fill_null(0),
            numerator_row_count=grouped.numerator_row_count.fill_null(0),
            denominator_count=grouped.denominator_count.fill_null(0),
            denominator_row_count=grouped.denominator_row_count.fill_null(0),
        )
        return J1SourcePlan(_finish(state, keys), checks=observed.checks)
    params = root.parameters
    if type(params) is not tuple or len(params) != 5:
        raise _fail("canonical ratio observation parameters", root.operator_id)
    metric_path, dependency, routes_json, scope_json, coordinate_paths = params
    if (
        not isinstance(metric_path, str)
        or not isinstance(routes_json, str)
        or not isinstance(scope_json, str)
        or type(coordinate_paths) is not tuple
        or any(not isinstance(path, str) for path in coordinate_paths)
    ):
        raise _fail("canonical ratio observation parameters", root.operator_id)
    coordinate_names = tuple(path for path in coordinate_paths if isinstance(path, str))
    metric = normalize_target_metric(context.registry, metric_path, sidecar=context.sidecar)
    if len(metric.components) != 2:
        raise _fail("two exact ratio components", metric_path)
    if dependency != metric.dependency_fingerprint:
        raise _fail("exact ratio dependency binding", str(dependency))
    routes_raw = json.loads(routes_json)
    routes = {name: tuple(path) for name, path in routes_raw}
    if (
        set(routes) != {component.computation_root.path for component in metric.components}
        or len(routes_raw) != 2
    ):
        raise _fail("one bound route for each component root", routes_json)
    scope = json.loads(scope_json)
    if (
        not isinstance(scope, dict)
        or not isinstance(scope.get("start"), str)
        or not isinstance(scope.get("end"), str)
    ):
        raise _fail("fixed half-open ratio scope", scope_json)
    start, end = _utc_bound(scope["start"]), _utc_bound(scope["end"])
    members_root = root.inputs[0].root if root.inputs else None
    if not isinstance(members_root, LogicalRootHandle):
        raise _fail("logical member input", root.operator_id)
    members = lower_j1_source(context, members_root, tables, memo=memo)
    keys = ("member", *(f"coord_{index}" for index in range(len(coordinate_names))))
    aggregates: list[ir.Table] = []
    coverage_checks: list[ir.Table] = []
    partition_checks: list[ir.Table] = []
    for component in metric.components:
        node = component_node(metric.graph, component.node_id)
        if not isinstance(node, AggregateNodeV1) or node.agg not in ("sum", "count"):
            raise _fail("direct sum/count components", component.role)
        component_rows, coverage, partition = _path_rows(
            context,
            component,
            node,
            routes[component.computation_root.path],
            coordinate_names,
            tables,
            start,
            end,
        )
        coverage_checks.append(coverage)
        partition_checks.append(partition)
        selected = component_rows.join(
            members.primary, component_rows.member == members.primary.member, how="inner"
        )
        if node.agg == "sum":
            if str(selected.amount.type()) == "float64":
                bad = selected.filter(selected.amount.isnan() | selected.amount.isinf())
                coverage_checks.append(bad.aggregate(invalid=bad.count()))
            result = selected.group_by(*(selected[key] for key in keys)).aggregate(
                numerator_sum=selected.amount.sum(),
                numerator_non_null_count=selected.amount.count(),
                numerator_row_count=selected.count(),
            )
        else:
            result = selected.group_by(*(selected[key] for key in keys)).aggregate(
                denominator_count=selected.count(),
                denominator_row_count=selected.count(),
            )
        aggregates.append(result)
    numerator, denominator = aggregates
    if len(keys) == 1:
        domain = members.primary.select(member=members.primary.member)
    else:
        domain = numerator.select(*keys).union(denominator.select(*keys), distinct=True)
    left_joined = domain.left_join(numerator, [domain[key] == numerator[key] for key in keys])
    left = left_joined.select(
        *(domain[key] for key in keys),
        numerator_sum=numerator.numerator_sum.fill_null(0),
        numerator_non_null_count=numerator.numerator_non_null_count.fill_null(0),
        numerator_row_count=numerator.numerator_row_count.fill_null(0),
    )
    right_joined = left.left_join(denominator, [left[key] == denominator[key] for key in keys])
    state = right_joined.select(
        *(left[key] for key in keys),
        numerator_sum=left.numerator_sum,
        numerator_non_null_count=left.numerator_non_null_count,
        numerator_row_count=left.numerator_row_count,
        denominator_count=denominator.denominator_count.fill_null(0),
        denominator_row_count=denominator.denominator_row_count.fill_null(0),
    )
    combined = _combine_checks(coverage_checks)
    impossible = domain.filter(ibis.literal(False))
    no_bad = impossible.aggregate(invalid=impossible.count())
    return J1SourcePlan(
        _finish(state, keys),
        checks=(
            *members.checks,
            (J3_RATIO_CHECKS[0], combined),
            (J3_RATIO_CHECKS[1], _combine_checks(partition_checks)),
            (J3_RATIO_CHECKS[2], no_bad),
        ),
    )
