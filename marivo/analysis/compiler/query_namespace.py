"""Prefix an inlined source schedule and remap every stage reference."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace

from marivo.analysis.binding.programs import FormulaProgram
from marivo.analysis.compiler.query_stages import (
    AttributeFrequencyRead,
    AttributeRead,
    CommonRead,
    FusedLocalProgram,
    LocalCompute,
    QueryStage,
    SourceReadStage,
)


def _name(names: Mapping[str, str], identifier: str, namespace: str) -> str:
    return names.get(identifier, namespace + "/" + identifier)


def _required_formula(
    program: FormulaProgram, names: Mapping[str, str], namespace: str
) -> FormulaProgram:
    return replace(
        program,
        values=tuple(
            replace(value, stage_id=_name(names, value.stage_id, namespace))
            for value in program.values
        ),
        populations=tuple(
            replace(value, stage_id=_name(names, value.stage_id, namespace))
            for value in program.populations
        ),
    )


def _prefix(stage: QueryStage, names: Mapping[str, str], namespace: str) -> QueryStage:
    inputs = tuple(_name(names, name, namespace) for name in stage.inputs)
    if isinstance(stage, SourceReadStage):
        source_program = stage.program
        if isinstance(source_program, (CommonRead, AttributeRead, AttributeFrequencyRead)):
            source_program = replace(
                source_program,
                members=tuple(_prefix_read(s, names, namespace) for s in source_program.members),
            )
        return replace(
            stage,
            stage_id=_name(names, stage.stage_id, namespace),
            inputs=inputs,
            parameters=tuple(
                replace(p, stage_id=_name(names, p.stage_id, namespace)) for p in stage.parameters
            ),
            native_reads=tuple(_prefix_read(s, names, namespace) for s in stage.native_reads),
            program=source_program,
        )
    if isinstance(stage, LocalCompute):
        program = stage.program
        if isinstance(program, FormulaProgram):
            program = _required_formula(program, names, namespace)
        elif isinstance(program, FusedLocalProgram):
            program = replace(
                program, steps=tuple(_prefix_local(s, names, namespace) for s in program.steps)
            )
        return replace(
            stage, stage_id=_name(names, stage.stage_id, namespace), inputs=inputs, program=program
        )
    return replace(stage, stage_id=_name(names, stage.stage_id, namespace), inputs=inputs)


def _prefix_read(
    stage: SourceReadStage, names: Mapping[str, str], namespace: str
) -> SourceReadStage:
    result = _prefix(stage, names, namespace)
    assert isinstance(result, SourceReadStage)
    return result


def _prefix_local(stage: LocalCompute, names: Mapping[str, str], namespace: str) -> LocalCompute:
    result = _prefix(stage, names, namespace)
    assert isinstance(result, LocalCompute)
    return result
