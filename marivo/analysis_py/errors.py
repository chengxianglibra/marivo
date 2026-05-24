"""Structured error hierarchy for marivo.analysis_py."""

from __future__ import annotations

from typing import Any


class AnalysisError(Exception):
    """Base class for all analysis_py errors."""

    def __init__(
        self,
        *,
        message: str,
        hint: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.details = details or {}

    @property
    def kind(self) -> str:
        name = type(self).__name__
        return name[:-5] if name.endswith("Error") else name

    def __str__(self) -> str:
        head = f"[{self.kind}] {self.message}"
        return f"{head}\n  hint: {self.hint}" if self.hint else head


class MetricNotFoundError(AnalysisError): ...


class WindowInvalidError(AnalysisError): ...


class WindowAmbiguousError(AnalysisError): ...


class SliceInvalidError(AnalysisError): ...


class SliceAmbiguousError(AnalysisError): ...


class SemanticKindMismatchError(AnalysisError): ...


class AlignmentFailedError(AnalysisError): ...


class CrossBackendMetricError(AnalysisError): ...


class CrossSessionFrameError(AnalysisError): ...


class FrameMutationError(AnalysisError): ...


class FrameRefNotFound(AnalysisError): ...  # noqa: N818


class BackendError(AnalysisError): ...


class NoBackendFactoryError(AnalysisError): ...


class DuplicateSessionNameError(AnalysisError): ...


class NoActiveSessionError(AnalysisError): ...


class SessionStateError(AnalysisError): ...
