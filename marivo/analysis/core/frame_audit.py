"""Closed non-executable result facts and one current evaluation record."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, TypeAlias

from marivo.analysis.core.artifact_data import OutputEvidence, OutputFinding
from marivo.analysis.core.model import reject

FrameOperationName: TypeAlias = Literal[
    "source", "literal", "select", "where", "top", "reduce", "align", "compare"
]


def _identity(value: str) -> None:
    if type(value) is not str or not value:
        reject(
            "an exact inert identity", repr(value), "Preserve the captured identity.", "frame.audit"
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
    checks: tuple[OutputEvidence, ...] = ()

    def __post_init__(self) -> None:
        if type(self.completed_at) is not datetime or self.completed_at.tzinfo is not timezone.utc:
            reject(
                "a plain UTC evaluation completion time",
                repr(self.completed_at),
                "Record completion after cleanup.",
                "frame.audit",
            )
        if type(self.inputs) is not tuple or any(
            type(value) not in (SourceInput, LiteralInput, MemoryInput, StoredInput)
            for value in self.inputs
        ):
            reject(
                "closed inert input facts",
                repr(self.inputs),
                "Record only explicit evaluated inputs.",
                "frame.audit",
            )
        if type(self.operations) is not tuple or any(
            value
            not in ("source", "literal", "select", "where", "top", "reduce", "align", "compare")
            for value in self.operations
        ):
            reject(
                "closed current operation categories",
                repr(self.operations),
                "Record this evaluation only.",
                "frame.audit",
            )
        if type(self.checks) is not tuple or any(
            type(value) is not OutputEvidence or value.kind != "check" for value in self.checks
        ):
            reject(
                "actual current check facts",
                repr(self.checks),
                "Retain checks from this successful evaluation.",
                "frame.audit",
            )


@dataclass(frozen=True, slots=True)
class FrameAudit:
    source_facts: tuple[OutputEvidence, ...]
    evaluation: EvaluationRecord

    def __post_init__(self) -> None:
        if (
            type(self.source_facts) is not tuple
            or any(
                type(value) is not OutputEvidence or value.kind not in ("read", "check")
                for value in self.source_facts
            )
            or type(self.evaluation) is not EvaluationRecord
        ):
            reject(
                "inert source facts and one current evaluation",
                type(self).__name__,
                "Keep audit data separate from producer definitions.",
                "frame.audit",
            )
        if len(set(self.source_facts)) != len(self.source_facts):
            reject(
                "exact-value deduplicated source facts",
                str(len(self.source_facts)),
                "Deduplicate identical facts in encounter order.",
                "frame.audit",
            )


@dataclass(frozen=True, slots=True)
class FrameResultMetadata:
    result_id: str
    audit: FrameAudit
    findings: tuple[OutputFinding, ...] = ()

    def __post_init__(self) -> None:
        _identity(self.result_id)
        if (
            type(self.audit) is not FrameAudit
            or type(self.findings) is not tuple
            or any(type(value) is not OutputFinding for value in self.findings)
        ):
            reject(
                "complete audit and actual output Findings",
                type(self).__name__,
                "Extract pure result facts before returning memory.",
                "frame.result",
            )

    @property
    def evaluated_at(self) -> datetime:
        return self.audit.evaluation.completed_at
