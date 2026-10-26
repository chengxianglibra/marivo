"""Capture and plan complete Population dependencies over Query stage outputs."""

from __future__ import annotations

from dataclasses import replace

from marivo._definition.graph import computation_fingerprint, walk
from marivo._definition.query import PopulationValues
from marivo._definition.relations import (
    AttributeKeys,
    FrameKeys,
    LiteralPopulation,
    PopulationDependency,
    PopulationFilter,
    PopulationProduct,
    PopulationProject,
    PopulationSet,
    condition_populations,
)
from marivo.analysis.compiler.graph_plan import FrameCompute, LocalCompute
from marivo.analysis.compiler.query_program import FrameParameter, ResultProgram
from marivo.analysis.core.data_schema import DataSchema
from marivo.analysis.core.data_types import DataColumn, ScalarDataType
from marivo.analysis.core.frame_structure import axis_role, column_kind
from marivo.analysis.core.model import reject
from marivo.refs import SemanticKind


def population_atoms(definition: PopulationDependency) -> tuple[PopulationDependency, ...]:
    """Keep independent domains intact; split only operations consuming Query keys."""
    if isinstance(definition, LiteralPopulation):
        return (definition,)
    closure = walk((definition,), resolve=lambda _: None)
    if not any(
        isinstance(node, (FrameKeys, AttributeKeys)) for node in closure.order
    ) or isinstance(definition.root, FrameKeys):
        return (definition,)
    node = definition.root
    operands: tuple[PopulationDependency, ...]
    if isinstance(node, PopulationSet):
        operands = (node.left, node.right)
    elif isinstance(node, PopulationProduct):
        operands = node.factors
    elif isinstance(node, PopulationFilter):
        operands = (node.operand, *condition_populations(node.predicate))
    elif isinstance(node, PopulationProject):
        operands = (node.operand,)
    else:
        return (definition,)  # Source lookups own their checked Query-key parameters.
    return tuple(
        {
            computation_fingerprint(atom): atom
            for operand in operands
            for atom in population_atoms(operand)
        }.values()
    )


def frame_key_groups(keys: FrameKeys | AttributeKeys) -> tuple[tuple[str, ...], ...]:
    """Return complete target parameter groups without inspecting data."""
    groups = tuple(
        tuple(f"__population_{axis}_{component}" for component in range(len(binding.key_names)))
        for axis, binding in enumerate(keys.bindings)
    )
    return (
        (*groups, (f"__population_{len(groups)}_0",))
        if isinstance(keys, AttributeKeys) and keys.calendar is not None
        else groups
    )


def frame_key_columns(
    keys: FrameKeys | AttributeKeys, schema: DataSchema
) -> tuple[DataColumn, ...]:
    """Admit the selected complete current groups with their exact physical types."""
    columns: list[DataColumn] = []
    for binding, group in zip(
        keys.bindings, frame_key_groups(keys)[: len(keys.bindings)], strict=True
    ):
        axis = next((axis for axis in schema.axes if axis.token == binding.token), None)
        if axis is None or (
            axis_role(axis) != binding.target.role
            or tuple(column.name for column in axis.columns) != binding.key_names
        ):
            reject(
                "the exact complete selected Frame coordinate",
                binding.token,
                "Retain the original Frame input and complete axis mapping.",
                "frame.population",
            )
        if (
            binding.target.field is not None
            and binding.target.field.ref.kind is SemanticKind.TIME_DIMENSION
            and column_kind(axis.columns[0]) not in ("date", "timestamp")
        ):
            reject(
                "a concrete date or timestamp Frame key for a TimeField",
                binding.token,
                "Select a time-valued ordinary coordinate before source binding.",
                "frame.population",
            )
        columns.extend(
            replace(column, slot_id=name, name=name)
            for column, name in zip(axis.columns, group, strict=True)
        )
    if isinstance(keys, AttributeKeys) and keys.calendar is not None:
        columns.append(
            DataColumn(frame_key_groups(keys)[-1][0], "calendar", ScalarDataType("int64"), False)
        )
    return tuple(columns)


def plan_frame_keys(
    parameter: FrameParameter, source: str, schema: DataSchema
) -> LocalCompute | FrameCompute:
    """Project checked rows jointly through the existing finite Population kernel."""
    keys = parameter.definition.root
    assert isinstance(keys, (FrameKeys, AttributeKeys))
    if frame_key_columns(keys, schema) != parameter.columns:
        reject(
            "the prepared complete Frame key schema",
            keys.input_id,
            "Preserve the selected complete key types through source preparation.",
            "frame.population",
        )
    if isinstance(keys, AttributeKeys):
        from marivo.analysis.core.attribute import AttributeProjectProgram
        from marivo.analysis.core.data_schema import DataAxis
        from marivo.analysis.core.frame_expression import AttributeProjectValues

        indices = tuple(
            next(i for i, axis in enumerate(schema.axes) if axis.token == binding.token)
            for binding in keys.bindings
        )
        calendar_index = next(
            (i for i, axis in enumerate(schema.axes) if axis.token == keys.calendar_token), None
        )
        groups = frame_key_groups(keys)
        output = DataSchema(
            tuple(
                DataAxis(
                    f"attribute.parameter.{i}",
                    tuple(
                        parameter.columns[
                            sum(len(g) for g in groups[:i]) : sum(len(g) for g in groups[: i + 1])
                        ]
                    ),
                )
                for i in range(len(groups))
            ),
            (),
        )
        parent_columns = tuple(c for axis in schema.axes for c in axis.columns)
        values = tuple(
            replace(c, slot_id=f"attribute.parent.{i}", name=f"parent.{i}")
            for i, c in enumerate(parent_columns)
        )
        output = replace(
            output, values=values, value_paths=tuple(("parent", str(i)) for i in range(len(values)))
        )
        program = AttributeProjectProgram(
            AttributeProjectValues(
                keys.side,
                indices,
                calendar_index,
                keys.calendar,
                keys.how,
                keys.comparison_alias,
                keys.presence,
            ),
            schema,
            output,
        )
        return FrameCompute(
            "attribute-bridge:" + computation_fingerprint(parameter.definition), (source,), program
        )
    selected = tuple(
        tuple(
            column.slot_id
            for column in next(axis for axis in schema.axes if axis.token == binding.token).columns
        )
        for binding in keys.bindings
    )
    return LocalCompute(
        "bridge:" + computation_fingerprint(parameter.definition),
        (source,),
        parameter.columns,
        tuple(column.slot_id for column in parameter.columns),
        ResultProgram(
            PopulationValues(parameter.definition),
            schema.columns,
            schema.key_fields,
            parameter.definition.axes,
            selected,
            frame_key_groups(keys),
            population_inputs=(parameter.definition,),
            population_layouts=(selected,),
        ),
    )
