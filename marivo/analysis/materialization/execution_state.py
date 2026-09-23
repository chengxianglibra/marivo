"""Private state passed between Dataset execution phases."""

from __future__ import annotations

from dataclasses import dataclass

from marivo.analysis.compiler.nodes import CompiledDataset
from marivo.analysis.materialization.contracts import StorageReceipt
from marivo.analysis.materialization.storage import DatasetWriteResult


@dataclass(slots=True)
class ExecutionProgress:
    phase: str = "authority_resolution"


@dataclass(frozen=True, slots=True)
class StageResult:
    artifact_ref: str
    storage: DatasetWriteResult[StorageReceipt]
    recipes: tuple[CompiledDataset, ...]
