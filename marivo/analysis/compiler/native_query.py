"""Deterministic output handoff for qualified source unions."""

from __future__ import annotations

from dataclasses import dataclass

from marivo.analysis.binding.correspondence import ComparisonEndpoint
from marivo.analysis.core.data_types import DataColumn


@dataclass(frozen=True, slots=True)
class NativeQueryFinish:
    """Restore source-union row order and its exact complete-axis layout."""

    columns: tuple[DataColumn, ...]
    order: tuple[str, ...]
    layout: ComparisonEndpoint | None
