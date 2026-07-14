"""Deterministic evaluation models, fixtures, instrumentation, and scorer.

Private script package for the cold-agent analysis surface evaluation gate.
Not part of the public ``marivo`` distribution.
"""

from __future__ import annotations

from scripts.analysis_surface_eval.fixture import (
    FixtureProject,
    build_convergence_fixture,
    build_skew_fixture,
)
from scripts.analysis_surface_eval.instrumentation import generate_sitecustomize
from scripts.analysis_surface_eval.model import (
    EvalEvent,
    EvalEventKind,
    EvaluationProfile,
    EvaluationReport,
    TrialScore,
    load_profile,
)
from scripts.analysis_surface_eval.scorer import score_evaluation, score_trial

__all__ = [
    "EvalEvent",
    "EvalEventKind",
    "EvaluationProfile",
    "EvaluationReport",
    "FixtureProject",
    "TrialScore",
    "build_convergence_fixture",
    "build_skew_fixture",
    "generate_sitecustomize",
    "load_profile",
    "score_evaluation",
    "score_trial",
]
