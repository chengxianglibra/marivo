"""Closed endpoint transport for source-joined Query relation fragments."""

from __future__ import annotations

from dataclasses import dataclass

from marivo.analysis.binding.correspondence import ComparisonEndpoint
from marivo.analysis.compiler.query_program import ResultProgram
from marivo.analysis.core.data_types import DataColumn


@dataclass(frozen=True, slots=True)
class NativeQueryInput:
    endpoint: ComparisonEndpoint
    columns: tuple[tuple[str, str], ...]
    present: str
    duplicate: str


@dataclass(frozen=True, slots=True)
class NativeQueryFinish:
    """Finish completed endpoint values using the existing local operation owner."""

    program: ResultProgram
    inputs: tuple[NativeQueryInput, ...]
    columns: tuple[DataColumn, ...]
    keys: tuple[str, ...]
    order: tuple[str, ...] = ()
    passthrough: bool = False
