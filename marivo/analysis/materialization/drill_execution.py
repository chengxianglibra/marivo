"""Run checked new-attribute reads over selected original contribution fibers."""

from __future__ import annotations

import pandas as pd

from marivo._definition.query import ComponentKeys, DrillValues
from marivo.analysis.compiler.graph_plan import LocalCompute
from marivo.analysis.materialization.graph_exchange import StageOutput
from marivo.analysis.methods.query_original import drill_components, original_inputs
from marivo.semantic._numeric_lowering import numeric_failure


def run_drill(stage: LocalCompute, inputs: tuple[StageOutput, ...]) -> StageOutput:
    """Select full original identities or finish their checked classifications."""
    program = stage.result
    assert program is not None and program.original is not None
    source = inputs[0]
    key_columns = tuple(column for column in program.columns if column.slot_id in program.keys)
    original, components = original_inputs(
        program.original, source.components, program.keys, source.population_frames, key_columns
    )
    operation = program.operation
    selected = source.table.loc[:, list(program.keys)].drop_duplicates()

    def selected_raw(value_slot: str) -> pd.DataFrame:
        component = next(
            (item for item in components if item.layout.reduction.value_slot == value_slot), None
        )
        if component is None:
            numeric_failure("complete original contribution identities", value_slot)
        identity = tuple(column.slot_id for column, _ in component.layout.identity)
        raw = component.table.loc[:, [*program.keys, *identity]].drop_duplicates()
        return (
            raw.merge(selected, on=list(program.keys), how="inner", validate="many_to_one")
            if program.keys
            else raw.merge(selected, how="cross")
        )

    if isinstance(operation, ComponentKeys):
        frame = selected_raw(operation.value_slot).loc[:, list(stage.keys)].drop_duplicates()
        return StageOutput.from_frame(frame.reset_index(drop=True), stage.columns, stage.keys)
    assert isinstance(operation, DrillValues)
    frames = {
        schema.value_slot: frame.table
        for schema, frame in zip(program.drills, inputs[1:], strict=True)
    }
    mappings = []
    for binding in original.values:
        schema = next(item for item in program.drills if item.value_slot == binding.column.slot_id)
        identity = tuple(column.slot_id for column, _ in schema.identity)
        raw = selected_raw(schema.value_slot)
        classified = raw.merge(
            frames[schema.value_slot], on=list(identity), how="inner", validate="many_to_one"
        )
        for index, key in enumerate(program.keys):
            classified[f"__target.{index}"] = classified[key]
        attributes = tuple(
            column.slot_id for column in schema.columns if column.slot_id not in schema.keys
        )
        classified = classified.rename(
            columns={
                slot: f"__target.{len(program.keys) + index}"
                for index, slot in enumerate(attributes)
            }
        )
        mappings.append(classified)
    frame, retained = drill_components(
        original,
        components,
        source.table,
        program.keys,
        stage.keys,
        tuple(mappings),
        tuple(column for column in stage.columns if column.slot_id not in stage.keys),
        population_frames=source.population_frames,
        source_columns=key_columns,
        target_columns=tuple(column for column in stage.columns if column.slot_id in stage.keys),
    )
    return StageOutput.from_frame(
        frame.loc[:, [column.slot_id for column in stage.columns]],
        stage.columns,
        stage.keys,
        layout=program.output_layout,
        components=retained,
        population_frames=source.population_frames,
    )
