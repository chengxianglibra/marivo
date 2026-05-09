"""Intent dispatch use case module.

Absorbs SemanticLayerService.run_intent. One function per intent;
MarivoRuntime methods delegate here.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from marivo.contracts.ids import SessionId
from marivo.intents.attribute import run_attribute_intent
from marivo.intents.compare import run_compare_intent
from marivo.intents.correlate import run_correlate_intent
from marivo.intents.decompose import run_decompose_intent
from marivo.intents.detect import run_detect_intent
from marivo.intents.diagnose import run_diagnose_intent
from marivo.intents.forecast import run_forecast_intent
from marivo.intents.observe import run_observe_intent
from marivo.intents.test import run_test_intent
from marivo.intents.validate import run_validate_intent

if TYPE_CHECKING:
    from marivo.runtime.runtime import MarivoRuntime

# Type alias: intent runner signature.
_IntentRunner = Callable[[Any, str, dict[str, Any] | None], dict[str, Any]]


def observe(
    runtime: MarivoRuntime, session_id: SessionId, params: dict[str, Any]
) -> dict[str, Any]:
    return _run(runtime, run_observe_intent, session_id, params)


def compare(
    runtime: MarivoRuntime, session_id: SessionId, params: dict[str, Any]
) -> dict[str, Any]:
    return _run(runtime, run_compare_intent, session_id, params)


def decompose(
    runtime: MarivoRuntime, session_id: SessionId, params: dict[str, Any]
) -> dict[str, Any]:
    return _run(runtime, run_decompose_intent, session_id, params)


def correlate(
    runtime: MarivoRuntime, session_id: SessionId, params: dict[str, Any]
) -> dict[str, Any]:
    return _run(runtime, run_correlate_intent, session_id, params)


def detect(runtime: MarivoRuntime, session_id: SessionId, params: dict[str, Any]) -> dict[str, Any]:
    return _run(runtime, run_detect_intent, session_id, params)


def test(runtime: MarivoRuntime, session_id: SessionId, params: dict[str, Any]) -> dict[str, Any]:
    return _run(runtime, run_test_intent, session_id, params)


def forecast(
    runtime: MarivoRuntime, session_id: SessionId, params: dict[str, Any]
) -> dict[str, Any]:
    return _run(runtime, run_forecast_intent, session_id, params)


def attribute(
    runtime: MarivoRuntime, session_id: SessionId, params: dict[str, Any]
) -> dict[str, Any]:
    return _run(runtime, run_attribute_intent, session_id, params)


def diagnose(
    runtime: MarivoRuntime, session_id: SessionId, params: dict[str, Any]
) -> dict[str, Any]:
    return _run(runtime, run_diagnose_intent, session_id, params)


def validate(
    runtime: MarivoRuntime, session_id: SessionId, params: dict[str, Any]
) -> dict[str, Any]:
    return _run(runtime, run_validate_intent, session_id, params)


# Mapping from intent type string to wrapper function.
INTENT_DISPATCHERS: dict[str, _IntentRunner] = {}
for _name, _fn in [
    ("observe", observe),
    ("compare", compare),
    ("decompose", decompose),
    ("correlate", correlate),
    ("detect", detect),
    ("test", test),
    ("forecast", forecast),
    ("attribute", attribute),
    ("diagnose", diagnose),
    ("validate", validate),
]:
    INTENT_DISPATCHERS[_name] = _fn  # type: ignore[assignment]

del _name, _fn


def _run(
    runtime: MarivoRuntime,
    intent_runner: _IntentRunner,
    session_id: SessionId,
    params: dict[str, Any],
) -> dict[str, Any]:
    """Common pre/post-execution logic for all intents.

    Replicates the cross-cutting work from SemanticLayerService.run_intent:
    - Assert the session exists and is open
    - Call the intent runner with (runtime, session_id, params)

    The session assertion uses the session_store port to
    load events and verify the session is active.  If the port is not
    yet available, falls back to session_ops.assert_session_is_open.
    """
    _assert_session_is_open(runtime, session_id)
    return intent_runner(runtime, session_id, params)


def _assert_session_is_open(runtime: MarivoRuntime, session_id: SessionId) -> None:
    """Assert that a session exists and is open (active).

    Uses the session_store port when available. Falls back to
    session_ops.assert_session_is_open when the port does not
    support the operation.
    """
    session_store = runtime.ports.session_store
    try:
        from marivo.contracts.errors import NotFoundError

        events = session_store.load_events(session_id)
        from marivo.core.session.rebuild import rebuild_session_state

        state = rebuild_session_state(events)
        if state.status != "active":
            raise ValueError(
                f"Session {session_id!r} is not open (status={state.status!r}). "
                "Write operations require an open session."
            )
    except (NotImplementedError, AttributeError):
        # Session store does not support load_events; fall back to
        # session_ops (retained for compatibility during migration).
        try:
            from marivo.runtime import session as session_ops

            session_ops.assert_session_is_open(runtime, session_id)
        except (NotImplementedError, AttributeError):
            # If session_ops cannot assert either, skip -- intent runner
            # will fail naturally on a bad session_id.
            pass
    except NotFoundError as exc:
        raise KeyError(f"Unknown session: {session_id}") from exc
