"""Lower closed Attribute fibers into physical aggregates and bounded diagnostics."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, NoReturn

import ibis
import ibis.expr.operations as ops
import ibis.expr.types as ir

from marivo._definition.expressions import (
    EntityCount,
    SourceAggregate,
    SourceWeightedMean,
)
from marivo.analysis.compiler.observation import lower_observation
from marivo.analysis.compiler.query_stages import (
    AttributeCheckLayout,
    AttributeLeafInput,
    AttributeRootLayout,
    read_reduction,
    require_reduction,
)
from marivo.analysis.core.attribute import (
    AttributeInputError,
    AttributeInputReason,
)
from marivo.analysis.core.value_types import ibis_type
from marivo.semantic.spi import (
    NumericCheck,
    NumericValue,
    lower_metric_formula,
    lower_row,
    lower_source_reduction,
    numeric_binary,
)

if TYPE_CHECKING:
    from marivo.analysis.model.attribute_failures import AttributeFailureContext


def input_failure(
    reason: AttributeInputReason, received: str, *, context: AttributeFailureContext | None = None
) -> NoReturn:
    raise AttributeInputError(reason=reason, received=received, context=context)


def original_input_slot(kind: int, occurrence: tuple[int, ...], name: str) -> str:
    return f"attribute.original.{kind}.{'.'.join(map(str, occurrence))}.{name}"


def lower_attribute_inputs(
    layout: AttributeRootLayout,
    table: ir.Table,
    field_columns: dict[str, str],
    preparation: ir.Table,
    preparation_checks: tuple[tuple[str, str, str], ...],
) -> tuple[ir.Table, tuple[tuple[str, NumericCheck], ...], tuple[AttributeCheckLayout, ...]]:
    """Lower every captured fiber into one actual root statement."""
    groups: dict[int, list[AttributeLeafInput]] = {}
    for leaf in layout.leaves:
        groups.setdefault(leaf.fiber, []).append(leaf)
    relations: list[ir.Table] = []
    checks: list[tuple[str, NumericCheck]] = []
    scopes: list[AttributeCheckLayout] = []
    for fiber, leaves in groups.items():
        local = replace(layout, leaves=tuple(leaves))
        source = table.filter(table[layout.fiber] == fiber)
        relation, local_checks, local_scopes = _lower_attribute_fiber(local, source, field_columns)
        scopes.extend(local_scopes)
        relations.append(relation.mutate(**{layout.fiber: ibis.literal(fiber)}))
        checks.extend(local_checks)
        if preparation_checks:
            prepared = preparation.filter(preparation[layout.fiber] == fiber)
            assert read_reduction(leaves[0].read) is not None
            axes = {slot: prepared[name] for slot, name in layout.scoped_columns}
            axes.update(
                (slot, prepared[field_columns[path]])
                for slot, path in require_reduction(leaves[0].read).axes
                if field_columns[path] in prepared.columns
            )
            actual_columns = tuple(column for column in layout.columns if column.slot_id in axes)
            for name, expected, received in preparation_checks:
                failed = prepared.select(**axes, **{name: prepared[name]})
                failed = failed.filter(failed[name])
                relations.append(
                    _first_failure(failed, tuple(axes)).mutate(
                        **{layout.kind: ibis.literal(4), layout.fiber: ibis.literal(fiber)}
                    )
                )
                checks.append((name, NumericCheck(prepared[name], expected, received)))
                scopes.append(
                    AttributeCheckLayout(name, "preparation", tuple(leaves), actual_columns)
                )
    return _union_attribute_relations(tuple(relations)), tuple(checks), tuple(scopes)


def _union_attribute_relations(relations: tuple[ir.Table, ...]) -> ir.Table:
    names = tuple(dict.fromkeys(name for relation in relations for name in relation.columns))
    dtypes = {
        name: next(relation[name].type() for relation in relations if name in relation.columns)
        for name in names
    }
    aligned = tuple(
        relation.select(
            **{
                name: relation[name].cast(dtypes[name].copy(nullable=True))
                if name in relation.columns
                else (
                    ibis.literal(0).cast(dtypes[name].copy(nullable=True))
                    if name.startswith("__original_id.")
                    else ibis.null().cast(dtypes[name].copy(nullable=True))
                )
                for name in names
            }
        )
        for relation in relations
    )
    return aligned[0].union(*aligned[1:], distinct=False) if len(aligned) > 1 else aligned[0]


@dataclass(slots=True)
class _FiberAggregation:
    layout: AttributeRootLayout
    field_columns: dict[str, str]
    parent_active: ir.BooleanValue
    checks: list[tuple[str, NumericCheck]] = field(default_factory=list, init=False)
    check_scopes: list[AttributeCheckLayout] = field(default_factory=list, init=False)

    def original_read(
        self, leaf: AttributeLeafInput, source: ir.Table, keys: dict[str, ir.Value], kind: int
    ) -> ir.Table:
        reduction = require_reduction(leaf.read)
        original_reduction = replace(
            reduction,
            axes=tuple((slot, path) for slot, path in reduction.axes if slot in keys),
            calendars=tuple(
                (slot, calendar) for slot, calendar in reduction.calendars if slot in keys
            ),
        )
        original_table, original_checks = lower_observation(
            original_reduction, source, self.field_columns, ()
        )
        aliases = {
            name: name
            if name in keys or name == reduction.value_slot
            else original_input_slot(kind, leaf.read.occurrence, name)
            for name in original_table.columns
        }
        self.checks.extend((aliases[name], check) for name, check in original_checks)
        self.check_scopes.extend(
            AttributeCheckLayout(
                aliases[name],
                "parent" if kind else "fine",
                (leaf,),
                tuple(column for column in self.layout.columns if column.slot_id in keys),
            )
            for name, _ in original_checks
        )
        return original_table.rename(
            **{target: name for name, target in aliases.items() if name != target}
        )

    def leaf_values(
        self, leaf: AttributeLeafInput, source: ir.Table, keys: dict[str, ir.Value], kind: int
    ) -> dict[str, ir.Value]:
        node = require_reduction(leaf.read).node
        local_fields = {path: source[column] for path, column in self.field_columns.items()}
        values: dict[str, ir.Value] = {}
        original = lower_source_reduction(node, source, local_fields)
        values[leaf.support.slot_id] = source.count()
        if isinstance(node, EntityCount):
            effective = source.count()
        elif isinstance(node, SourceAggregate):
            item = lower_row(node.operand, local_fields).value
            effective = item.nunique() if node.reducer == "count_distinct" else item.count()
        else:
            value_item = lower_row(node.value, local_fields).value
            weight_item = lower_row(node.weight, local_fields).value
            effective = (
                (value_item.notnull() & weight_item.notnull()).ifelse(1, ibis.null()).count()
            )
        values[leaf.effective.slot_id] = effective
        for index, check in enumerate(original.checks):
            name = f"attribute.check.{kind}.{'.'.join(map(str, leaf.read.occurrence))}.{index}"
            values[name] = check.violation
            self.checks.append((name, check))
            self.check_scopes.append(
                AttributeCheckLayout(
                    name,
                    "parent" if kind else "fine",
                    (leaf,),
                    tuple(column for column in self.layout.columns if column.slot_id in keys),
                )
            )
        for role, column in leaf.roles:
            if role == "additive_value":
                value = original.value
            elif isinstance(node, SourceAggregate):
                component = replace(
                    node,
                    reducer="count" if role == "valid_value_count" else "sum",
                )
                reduced = lower_source_reduction(component, source, local_fields)
                value = reduced.value.fill_null(0)
                component_checks: tuple[NumericCheck, ...]
                if node.reducer == "mean" and role == "valid_value_sum":
                    item = lower_row(node.operand, local_fields).value
                    value = (
                        (item.cast("decimal(38,0)") if item.type().is_integer() else item)
                        .sum()
                        .fill_null(0)
                    )
                    component_checks = ()
                else:
                    component_checks = reduced.checks
                for index, check in enumerate(component_checks):
                    name = f"attribute.componentcheck.{kind}.{'.'.join(map(str, leaf.read.occurrence))}.{role}.{index}"
                    values[name] = check.violation
                    self.checks.append((name, check))
                    self.check_scopes.append(
                        AttributeCheckLayout(
                            name,
                            "parent" if kind else "fine",
                            (leaf,),
                            tuple(
                                column for column in self.layout.columns if column.slot_id in keys
                            ),
                        )
                    )
            elif isinstance(node, SourceWeightedMean):
                value_item = lower_row(node.value, local_fields).value
                weight = lower_row(node.weight, local_fields).value
                pair = value_item.notnull() & weight.notnull()
                if role == "pair_count":
                    value = pair.ifelse(1, ibis.null()).count()
                elif role == "pair_negative_count":
                    value = (pair & (weight < 0)).ifelse(1, ibis.null()).count()
                else:
                    item = (
                        numeric_binary(
                            "multiply",
                            lower_row(node.value, local_fields),
                            lower_row(node.weight, local_fields),
                        ).value
                        if role == "pair_product_sum"
                        else weight
                    )
                    if item.type().is_integer():
                        item = item.cast("decimal(38,0)")
                    value = pair.ifelse(item, ibis.null().cast(item.type())).sum().fill_null(0)
            else:
                assert isinstance(node, EntityCount)
                value = source.count()
            values[column.slot_id] = value
        return values

    def native_values(self, result: ir.Table, keys: dict[str, ir.Value], kind: int) -> ir.Table:
        for native in self.layout.native_formulas:
            if native.fiber != self.layout.leaves[0].fiber:
                continue
            bindings = {
                value.occurrence: NumericValue(result[value.column.slot_id])
                for value in native.program.values
                if value.column.slot_id in result.columns
            }
            original = lower_metric_formula(
                native.node, native.occurrence, bindings, dict(native.program.declared)
            )
            result = result.mutate(**{native.column.slot_id: original.value})
            for check_index, check in enumerate(original.checks):
                name = original_input_slot(kind, native.path, f"native_check.{check_index}")
                result = result.mutate(**{name: check.violation})
                self.checks.append((name, check))
                self.check_scopes.append(
                    AttributeCheckLayout(
                        name,
                        "parent" if kind else "fine",
                        tuple(
                            leaf
                            for leaf in self.layout.leaves
                            if leaf.use.metric == native.use.metric
                            and leaf.use.occurrence[: len(native.occurrence)] == native.occurrence
                        ),
                        tuple(column for column in self.layout.columns if column.slot_id in keys),
                    )
                )
        return result

    def aggregate(self, source: ir.Table, keys: dict[str, ir.Value], kind: int) -> ir.Table:
        values: dict[str, ir.Value] = {}
        originals: list[ir.Table] = []
        for leaf in self.layout.leaves:
            originals.append(self.original_read(leaf, source, keys, kind))
            values.update(self.leaf_values(leaf, source, keys, kind))
        result = source.group_by(**keys).aggregate(**values) if keys else source.aggregate(**values)
        for original_table in originals:
            joined = result.inner_join(
                original_table,
                [result[key].identical_to(original_table[key]) for key in keys]
                if keys
                else ibis.literal(True),
            )
            result = joined.select(
                result,
                *(original_table[name] for name in original_table.columns if name not in keys),
            )
        result = self.native_values(result, keys, kind)
        if kind == 1 and not self.layout.parent_keys:
            result = result.filter(self.parent_active)
        return result.mutate(**{self.layout.kind: ibis.literal(kind)})


def _lower_attribute_fiber(
    layout: AttributeRootLayout, table: ir.Table, field_columns: dict[str, str]
) -> tuple[ir.Table, tuple[tuple[str, NumericCheck], ...], tuple[AttributeCheckLayout, ...]]:
    """Return aggregates and bounded witnesses for operation-specific constraints."""
    fields = {path: table[column] for path, column in field_columns.items()}
    representative = layout.leaves[0].read
    assert read_reduction(representative) is not None
    axes = {
        slot: fields[path]
        for slot, path in require_reduction(representative).axes
        if layout.scope == "fine" or slot in layout.parent_keys
    }
    axes.update(
        (slot, table[column])
        for slot, column in layout.scoped_columns
        if slot in {slot for slot, _ in require_reduction(representative).calendars}
    )
    parent_axes = {slot: table[column] for slot, column in layout.scoped_columns}
    parent_active: ir.BooleanValue = ibis.literal(True)
    if not layout.parent_keys:
        selected_parent = next(
            (
                leaf
                for leaf in table.op().find(ops.UnboundTable)
                if leaf.name == layout.parent_parameter.placeholder
            ),
            None,
        )
        if selected_parent is None:
            input_failure(
                "missing_component_proof", "the captured complete parent parameter is missing"
            )
        parent_active = selected_parent.to_expr().count() > 0
    aggregation = _FiberAggregation(layout, field_columns, parent_active)

    if layout.scope == "parent":
        relations: tuple[ir.Table, ...] = (aggregation.aggregate(table, parent_axes, 1),)
    else:
        fine_source = table.filter(table[layout.assigned]) if layout.partition else table
        fine = aggregation.aggregate(
            fine_source,
            {slot: fine_source[value.get_name()] for slot, value in axes.items()},
            0,
        )
        diagnostics: list[ir.Table] = []
        if layout.population is not None:
            domain = ibis.table(
                {column.slot_id: ibis_type(column.dtype) for column in layout.population.columns},
                name=layout.population.placeholder,
            )
            missing = fine.anti_join(
                domain,
                [
                    fine[key].identical_to(domain[column.slot_id])
                    for key, column in zip(layout.keys, layout.population.columns, strict=True)
                ],
            )
            diagnostics.append(
                _first_failure(missing.select(*layout.keys), layout.keys).mutate(
                    **{layout.kind: ibis.literal(6)}
                )
            )
        identities = {column.slot_id: table[name] for column, name in layout.identity}
        if layout.partition:
            roots = table.select(**parent_axes, **identities).distinct()
            assigned = fine_source.select(
                **{slot: fine_source[value.get_name()] for slot, value in axes.items()},
                **{column.slot_id: fine_source[name] for column, name in layout.identity},
            ).distinct()
            root_keys = (*layout.parent_keys, *identities)
            missing = roots.anti_join(
                assigned,
                [roots[key].identical_to(assigned[key]) for key in root_keys],
            )
            diagnostics.append(
                _first_failure(missing, root_keys).mutate(**{layout.kind: ibis.literal(2)})
            )
            diagnostics.append(
                _partition_failure(assigned, root_keys, tuple(axes)).mutate(
                    **{layout.kind: ibis.literal(3)}
                )
            )
        for index, leaf in enumerate(layout.leaves):
            if leaf.distinct is None:
                continue
            node = require_reduction(leaf.read).node
            assert isinstance(node, SourceAggregate)
            value = lower_row(
                node.operand, {path: fine_source[column] for path, column in field_columns.items()}
            ).value
            assigned = fine_source.select(
                **{slot: fine_source[item.get_name()] for slot, item in axes.items()},
                **{leaf.distinct.slot_id: value},
            ).distinct()
            assigned = assigned.filter(assigned[leaf.distinct.slot_id].notnull())
            diagnostics.append(
                _partition_failure(
                    assigned, (*layout.parent_keys, leaf.distinct.slot_id), tuple(axes)
                ).mutate(**{layout.kind: ibis.literal(5), "__attribute_leaf": ibis.literal(index)})
            )
        relations = (fine, *diagnostics)
    return (
        _union_attribute_relations(relations),
        tuple(aggregation.checks),
        tuple(aggregation.check_scopes),
    )


def _first_failure(table: ir.Table, keys: tuple[str, ...]) -> ir.Table:
    """Sort failed candidates only, selecting one exact native key."""
    return (
        table.order_by(*(table[key].asc(nulls_first=True) for key in keys)).limit(1)
        if keys
        else table.limit(1)
    )


def _partition_failure(
    assigned: ir.Table, keys: tuple[str, ...], groups: tuple[str, ...]
) -> ir.Table:
    counts = assigned.group_by(*keys).aggregate(__attribute_groups=assigned.count())
    failed = _first_failure(counts.filter(counts.__attribute_groups > 1), keys)
    samples = assigned.semi_join(failed, [assigned[key].identical_to(failed[key]) for key in keys])
    return samples.order_by(*(samples[key].asc(nulls_first=True) for key in groups)).limit(6)
