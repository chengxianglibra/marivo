"""Deterministic output handoff for qualified source unions."""

from __future__ import annotations

from dataclasses import dataclass

from marivo.analysis.binding.correspondence import ComparisonEndpoint
from marivo.analysis.core.data_types import DataColumn


@dataclass(frozen=True, slots=True)
class NativeFrameTopFinish:
    """Keep full-input integrity summaries separate from bounded business rows."""

    presence: str
    integrity_checks: tuple[tuple[str, str, str], ...]
    numeric_witnesses: tuple[tuple[str, tuple[str, ...]], ...] = ()


@dataclass(frozen=True, slots=True)
class NativeQueryFinish:
    """Restore source-union row order and its exact complete-axis layout."""

    columns: tuple[DataColumn, ...]
    order: tuple[str, ...]
    layout: ComparisonEndpoint | None
    frame_top: NativeFrameTopFinish | None = None
