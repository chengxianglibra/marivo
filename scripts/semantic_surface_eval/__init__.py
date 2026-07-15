"""Deterministic evaluation models, fixtures, instrumentation, and scorer.

Private script package for the cold-agent semantic surface evaluation gate.
Not part of the public ``marivo`` distribution.
"""

from __future__ import annotations

from scripts.semantic_surface_eval.fixture import (
    FixtureProject,
    build_clean_readiness_fixture,
    build_dependency_order_fixture,
    build_environment_skew_fixture,
    build_preview_before_readiness_fixture,
    build_scope_guard_fixture,
    build_unresolved_meaning_fixture,
    build_verify_before_preview_fixture,
)
from scripts.semantic_surface_eval.instrumentation import generate_sitecustomize
from scripts.semantic_surface_eval.model import (
    ALL_CASE_IDS,
    EvalEvent,
    EvalEventKind,
    EvaluationProfile,
    EvaluationReport,
    TrialScore,
    load_profile,
)
from scripts.semantic_surface_eval.scorer import score_evaluation, score_trial

__all__ = [
    "ALL_CASE_IDS",
    "EvalEvent",
    "EvalEventKind",
    "EvaluationProfile",
    "EvaluationReport",
    "FixtureProject",
    "TrialScore",
    "build_clean_readiness_fixture",
    "build_dependency_order_fixture",
    "build_environment_skew_fixture",
    "build_preview_before_readiness_fixture",
    "build_scope_guard_fixture",
    "build_unresolved_meaning_fixture",
    "build_verify_before_preview_fixture",
    "generate_sitecustomize",
    "load_profile",
    "score_evaluation",
    "score_trial",
]
