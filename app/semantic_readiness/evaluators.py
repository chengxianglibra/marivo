"""Placeholder readiness evaluators for Phase A.

These evaluators preserve the simple status-to-readiness mapping used
in Phase A: published → active + ready, draft → draft + not_ready,
deprecated → deprecated + not_ready.

Phase B will replace these with object-specific evaluators that compute
blocking_requirements and capabilities based on dependencies, bindings,
and physical grounding requirements.
"""

from __future__ import annotations

from .context import ReadinessEvaluationContext
from .types import (
    ReadinessResult,
    ReadinessTraceItem,
    derive_lifecycle_status,
    derive_readiness_status,
)


class PlaceholderSemanticReadinessEvaluator:
    """Placeholder evaluator that preserves Phase A readiness semantics.

    This evaluator is used for all object kinds in Phase A. It simply
    derives lifecycle_status and readiness_status from the storage status,
    with empty blocking_requirements and capabilities.

    The trace entry identifies the placeholder source for debugging and
    helps distinguish Phase A behavior from Phase B object-specific rules.
    """

    def __init__(self, object_kind: str) -> None:
        """Initialize placeholder evaluator for a specific object kind.

        Args:
            object_kind: The semantic object type this evaluator handles.
        """
        self.object_kind = object_kind

    def evaluate(self, context: ReadinessEvaluationContext) -> ReadinessResult:
        """Evaluate readiness using Phase A simple mapping.

        Returns lifecycle_status and readiness_status derived from storage
        status, with empty blockers/capabilities and a trace entry.
        """
        snapshot = context.snapshot
        return ReadinessResult(
            lifecycle_status=derive_lifecycle_status(snapshot.status),
            readiness_status=derive_readiness_status(snapshot.status),
            capabilities={},
            blocking_requirements=[],
            trace=[
                ReadinessTraceItem(
                    stage="compat_placeholder",
                    detail=(
                        "Placeholder evaluator preserves Phase A readiness semantics until "
                        "object-specific rules are implemented."
                    ),
                    source=f"{self.object_kind}_placeholder_evaluator",
                    subject_ref=snapshot.ref,
                )
            ],
            had_ready_predecessor=context.previously_ready(),
        )
