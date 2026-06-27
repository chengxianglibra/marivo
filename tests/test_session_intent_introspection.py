"""Session intent methods carry real docstrings and match intent public signatures.

The agent-facing execution surface is ``session.observe`` / ``compare`` / ... .
Those methods delegate to the intent functions in ``marivo.analysis.intents.*``.
Docstrings are authored directly on the Session methods in ``core.py``; this
guards that they exist and that the method signatures stay aligned with the
intent functions they forward to. The methods intentionally hide the plumbing
parameters ``session`` and ``_triggered_by``.
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest

import marivo.analysis.intents as intents
from marivo.analysis.session.core import (
    Session,
    SessionDiscoverNamespace,
    SessionTransformNamespace,
)

_HIDDEN = {"self", "session", "_triggered_by"}

_INTENT_TO_METHOD: dict[str, str] = {}

# Discover and transform are exposed as namespace properties, not plain methods.
_NAMESPACE_CLASSES = (SessionDiscoverNamespace, SessionTransformNamespace)


def _delegating_methods() -> list[tuple[str, Any, Any]]:
    """Return (method_name, session_method, intent_function) for each delegating surface method."""
    results: list[tuple[str, Any, Any]] = []
    for intent_name in intents.__all__:
        intent_func = getattr(intents, intent_name)
        method_name = _INTENT_TO_METHOD.get(intent_name, intent_name)
        member = inspect.getattr_static(Session, method_name, None)
        if not inspect.isfunction(member):
            continue
        results.append((method_name, member, intent_func))
    return results


_CASES = [pytest.param(method, intent, id=name) for name, method, intent in _delegating_methods()]


def _public_params(func: Any) -> dict[str, inspect.Parameter]:
    return {
        name: parameter
        for name, parameter in inspect.signature(func).parameters.items()
        if name not in _HIDDEN
    }


@pytest.mark.parametrize(("method", "intent"), _CASES)
def test_method_has_docstring(method: Any, intent: Any) -> None:
    method_doc = inspect.getdoc(method)
    assert method_doc, f"{method.__name__} has no docstring"


@pytest.mark.parametrize(("method", "intent"), _CASES)
def test_signature_matches_intent_public_params(method: Any, intent: Any) -> None:
    method_params = _public_params(method)
    intent_params = _public_params(intent)

    assert list(method_params) == list(intent_params)
    for name, intent_param in intent_params.items():
        method_param = method_params[name]
        assert method_param.kind == intent_param.kind, name
        assert method_param.default == intent_param.default, name
        assert str(method_param.annotation) == str(intent_param.annotation), name
        assert method_param.annotation != "Any", name

    method_signature = inspect.signature(method)
    assert "session" not in method_signature.parameters
    assert "_triggered_by" not in method_signature.parameters

    method_return = method_signature.return_annotation
    assert method_return == inspect.signature(intent).return_annotation
    assert method_return != "Any"


def test_discover_namespace_methods_have_docstrings() -> None:
    for name in (
        "point_anomalies",
        "period_shifts",
        "driver_axes",
        "interesting_slices",
        "interesting_windows",
        "cross_sectional_outliers",
    ):
        method = getattr(SessionDiscoverNamespace, name)
        assert inspect.getdoc(method), f"SessionDiscoverNamespace.{name} has no docstring"


def test_transform_namespace_methods_have_docstrings() -> None:
    for name in (
        "filter",
        "slice",
        "rollup",
        "topk",
        "bottomk",
        "rank",
        "normalize",
        "window",
    ):
        method = getattr(SessionTransformNamespace, name)
        assert inspect.getdoc(method), f"SessionTransformNamespace.{name} has no docstring"
