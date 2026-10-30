"""Closed non-executable result facts and one current evaluation record."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, TypeAlias

from marivo.analysis.core.frame_source_reads import FrameSourceRead, merge_source_reads
from marivo.analysis.core.lineage import FrameLineage
from marivo.analysis.core.model import reject

FrameOperationName: TypeAlias = Literal[
    "source", "literal", "fix", "select", "where", "top", "reduce", "align", "compare", "attribute"
]


def _identity(value: str) -> None:
    if type(value) is not str or not value:
        reject(
            "an exact inert identity",
            repr(value),
            "Preserve the captured identity.",
            "frame.metadata",
        )


@dataclass(frozen=True, slots=True)
class SourceInput:
    input_id: str

    def __post_init__(self) -> None:
        _identity(self.input_id)


@dataclass(frozen=True, slots=True)
class LiteralInput:
    input_id: str

    def __post_init__(self) -> None:
        _identity(self.input_id)


@dataclass(frozen=True, slots=True)
class MemoryInput:
    input_id: str
    result_id: str

    def __post_init__(self) -> None:
        _identity(self.input_id)
        _identity(self.result_id)


@dataclass(frozen=True, slots=True)
class StoredInput:
    input_id: str
    result_id: str
    store_id: str
    session_id: str
    artifact_id: str

    def __post_init__(self) -> None:
        for value in (
            self.input_id,
            self.result_id,
            self.store_id,
            self.session_id,
            self.artifact_id,
        ):
            _identity(value)


EvaluationInput: TypeAlias = SourceInput | LiteralInput | MemoryInput | StoredInput


@dataclass(frozen=True, slots=True)
class EvaluationRecord:
    completed_at: datetime
    inputs: tuple[EvaluationInput, ...]
    operations: tuple[FrameOperationName, ...]

    def __post_init__(self) -> None:
        if type(self.completed_at) is not datetime or self.completed_at.tzinfo is not timezone.utc:
            reject(
                "a plain UTC evaluation completion time",
                repr(self.completed_at),
                "Record completion after cleanup.",
                "frame.metadata",
            )
        if type(self.inputs) is not tuple or any(
            type(value) not in (SourceInput, LiteralInput, MemoryInput, StoredInput)
            for value in self.inputs
        ):
            reject(
                "closed inert input facts",
                repr(self.inputs),
                "Record only explicit evaluated inputs.",
                "frame.metadata",
            )
        if type(self.operations) is not tuple or any(
            value
            not in (
                "source",
                "literal",
                "select",
                "fix",
                "where",
                "top",
                "reduce",
                "align",
                "compare",
                "attribute",
            )
            for value in self.operations
        ):
            reject(
                "closed current operation categories",
                repr(self.operations),
                "Record this evaluation only.",
                "frame.metadata",
            )


@dataclass(frozen=True, slots=True)
class FrameResultMetadata:
    result_id: str
    evaluation: EvaluationRecord
    source_reads: tuple[FrameSourceRead, ...] = ()
    lineage: FrameLineage | None = None

    def __post_init__(self) -> None:
        _identity(self.result_id)
        if type(self.evaluation) is not EvaluationRecord:
            reject(
                "one complete current evaluation record",
                type(self.evaluation).__name__,
                "Extract pure result metadata before returning memory.",
                "frame.result",
            )
        _source_reads(self.source_reads)

    @property
    def evaluated_at(self) -> datetime:
        return self.evaluation.completed_at


@dataclass(frozen=True, slots=True)
class StoredFrameMetadata:
    """Complete captured commit description, independent of producer state."""

    result_id: str
    evaluated_at: datetime
    inputs: tuple[EvaluationInput, ...]
    operations: tuple[FrameOperationName, ...]
    source_reads: tuple[FrameSourceRead, ...]
    lineage: FrameLineage

    def __post_init__(self) -> None:
        _identity(self.result_id)
        # The existing closed evaluation record owns input and operation validation.
        EvaluationRecord(self.evaluated_at, self.inputs, self.operations)
        _source_reads(self.source_reads)
        if type(self.lineage) is not FrameLineage:
            reject(
                "complete saved lineage",
                type(self.lineage).__name__,
                "Preserve the captured lineage.",
                "frame.stored",
            )


def _source_reads(values: tuple[FrameSourceRead, ...]) -> None:
    if type(values) is not tuple or any(type(value) is not FrameSourceRead for value in values):
        reject(
            "an immutable tuple of complete source-read records",
            type(values).__name__,
            "Retain the captured typed source reads.",
            "frame.source_reads",
        )
    if merge_source_reads(values) != values:
        reject(
            "one source-read record per read identity",
            str(len(values)),
            "Deduplicate identical reads in encounter order.",
            "frame.source_reads",
        )
