# DEPRECATED: use marivo.core.intent.step_registry
# Types re-exported from core.intent.step_registry; this module is a compatibility shim.
from __future__ import annotations

from marivo.core.intent.step_registry import (
    StepRunner,
    StepRunnerRegistry,
)

__all__ = [
    "StepRunner",
    "StepRunnerRegistry",
]
