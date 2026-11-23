"""Compiled Frame payloads shared by lowering and execution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic

from marivo.analysis.compiler.lowered_query import LoweredQuery
from marivo.analysis.compiler.query_stages import SourceReadStage
from marivo.analysis.core.frame_operations import FrameOperation
from marivo.analysis.model.data_schema import DataSchema
from marivo.analysis.model.frame_contracts import CheckedStageInputT


@dataclass(frozen=True, slots=True)
class CompiledSourceFrame(Generic[CheckedStageInputT]):
    lowered: LoweredQuery
    schema: DataSchema
    native: bool = False
    native_reads: tuple[SourceReadStage, ...] = ()
    native_value_slots: tuple[str, ...] = ()
    checked_inputs: tuple[tuple[str, CheckedStageInputT], ...] = ()


@dataclass(frozen=True, slots=True)
class CompiledFramePlan(Generic[CheckedStageInputT]):
    lowered: LoweredQuery
    schema: DataSchema
    source_stages: tuple[tuple[str, str], ...]
    source_outputs: tuple[tuple[str, str], ...]
    frame_operations: tuple[tuple[str, FrameOperation], ...]
    checked_inputs: tuple[tuple[str, CheckedStageInputT], ...] = ()
