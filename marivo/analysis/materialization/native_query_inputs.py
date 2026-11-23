"""Recognize exact captured Query inputs for terminal native relations."""

from collections.abc import Mapping

from marivo._definition.query import KeyImage, PopulationValues, ReadFields
from marivo._definition.relations import AttributeKeys, PopulationProgram
from marivo.analysis.binding.programs import FormulaProgram, PopulationInput
from marivo.analysis.compiler.query_program import ResultProgram
from marivo.analysis.compiler.query_stages import FrameKeysInput, LocalCompute, QueryStage


def terminal_attribute_inputs(
    endpoint: QueryStage,
    by_id: Mapping[str, QueryStage],
    terminal: bool,
) -> tuple[PopulationInput | None, FrameKeysInput | None]:
    attribute_parent: PopulationInput | None = None
    attribute_leaf: FrameKeysInput | None = None
    if (
        terminal
        and isinstance(endpoint, LocalCompute)
        and isinstance(endpoint.program, FormulaProgram)
    ):
        formula = endpoint.program
        scope = formula.parent_scope
        if (
            formula.values
            and formula.calendar is None
            and not formula.counts
            and formula.output_population is None
            and isinstance(scope, PopulationProgram)
            and isinstance(scope.root, AttributeKeys)
            and scope.root.calendar_token is None
            and len(formula.populations) == 1
            and formula.populations[0].definition == scope
        ):
            binding = formula.populations[0]
            projected = by_id[binding.stage_id]
            if (
                isinstance(projected, LocalCompute)
                and isinstance(projected.program, ResultProgram)
                and len(projected.inputs) == 1
                and isinstance(leaf := by_id[projected.inputs[0]], FrameKeysInput)
                and leaf.parameter.definition == scope
                and projected.keys == binding.keys
                and tuple(column.slot_id for column in projected.columns) == binding.keys
            ):
                result = projected.program
                direct_image = (
                    isinstance(result.operation, KeyImage)
                    and result.keys == leaf.keys
                    and result.columns == leaf.columns
                )
                direct_population = (
                    isinstance(result.operation, PopulationValues)
                    and result.operation.definition == scope
                    and result.population_inputs == (scope,)
                    and len(result.population_layouts) == 1
                    and tuple(key for group in result.population_layouts[0] for key in group)
                    == leaf.keys
                    and tuple(key for group in result.output_groups for key in group)
                    == binding.keys
                )
                if direct_image or direct_population:
                    attribute_parent, attribute_leaf = binding, leaf
    return attribute_parent, attribute_leaf


def field_inputs(by_id: Mapping[str, QueryStage], internal: set[str]) -> set[str]:
    field_inputs: set[str] = set()
    for item in by_id.values():
        if (
            item.stage_id not in internal
            or not isinstance(item, LocalCompute)
            or not isinstance(item.program, ResultProgram)
            or not isinstance(item.program.operation, ReadFields)
        ):
            continue
        pending = list(item.inputs)
        while pending:
            identifier = pending.pop()
            if identifier not in field_inputs:
                field_inputs.add(identifier)
                pending.extend(by_id[identifier].inputs)
    return field_inputs
