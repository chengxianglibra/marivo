"""Session intent methods mirror their intent function's doc and public signature.

The agent-facing execution surface is ``session.observe`` / ``compare`` / ... .
Those methods delegate to the intent functions in ``marivo.analysis.intents.*``,
which own the canonical docstring and typed signature. This guards that the
methods stay self-documenting (real types written in ``core.py`` source plus the
docstring mirrored at import time) and never drift from the intent functions they
forward to. The methods intentionally hide the plumbing parameters ``session`` and
``_triggered_by``.
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest

import marivo.analysis.intents as intents
from marivo.analysis.session._introspection import intent_method_bindings
from marivo.analysis.session.core import Session

_HIDDEN = {"self", "session", "_triggered_by"}

# Single registry shared with the installer; pairing the method object with its
# intent function keeps this test and ``install_intent_docstrings`` in lockstep.
_CASES = [
    pytest.param(getattr(Session, method_name), intent, id=method_name)
    for method_name, intent in intent_method_bindings().items()
]

# ``intents.__all__`` exports the hypothesis test as ``test``; Session exposes it
# as ``hypothesis_test``. Everything else maps to a method by identity.
_INTENT_TO_METHOD = {"test": "hypothesis_test"}


def _public_params(func: Any) -> dict[str, inspect.Parameter]:
    return {
        name: parameter
        for name, parameter in inspect.signature(func).parameters.items()
        if name not in _HIDDEN
    }


@pytest.mark.parametrize(("method", "intent"), _CASES)
def test_doc_mirrors_intent(method: Any, intent: Any) -> None:
    method_doc = inspect.getdoc(method)
    assert method_doc, f"{method.__name__} has no docstring"
    assert method_doc == inspect.getdoc(intent)


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


def test_every_method_backed_intent_is_registered() -> None:
    """A new intent exposed as a plain Session method must be registered for mirroring.

    Guards the drift where someone adds an intent plus its delegating method but
    forgets ``intent_method_bindings``; without this the method would ship a blank
    docstring and nothing else would fail.
    """

    registered = set(intent_method_bindings())
    for intent_name in intents.__all__:
        method_name = _INTENT_TO_METHOD.get(intent_name, intent_name)
        member = inspect.getattr_static(Session, method_name, None)
        # discover / transform surface as namespace properties, not delegators.
        if not inspect.isfunction(member):
            continue
        assert method_name in registered, (
            f"Session.{method_name} delegates to intent {intent_name!r} but is missing "
            f"from intent_method_bindings(); its docstring will not be mirrored."
        )
