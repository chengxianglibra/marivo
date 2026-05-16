"""Intent dispatch use case module.

Absorbs SemanticLayerService.run_intent. One function per intent;
MarivoRuntime methods delegate here.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from marivo.contracts.aoi_runtime import (
    AoiAtomicRequest,
    AoiDerivedRequest,
    assert_request_matches_intent,
)
from marivo.contracts.ids import SessionId
from marivo.runtime.aoi_lowering import lower_aoi_derived_request, lower_aoi_request
from marivo.runtime.intents.attribute import run_attribute_intent
from marivo.runtime.intents.compare import run_compare_intent
from marivo.runtime.intents.correlate import run_correlate_intent
from marivo.runtime.intents.decompose import run_decompose_intent
from marivo.runtime.intents.detect import run_detect_intent
from marivo.runtime.intents.diagnose import run_diagnose_intent
from marivo.runtime.intents.forecast import run_forecast_intent
from marivo.runtime.intents.observe import run_observe_intent
from marivo.runtime.intents.test import run_test_intent
from marivo.runtime.intents.validate import run_validate_intent

if TYPE_CHECKING:
    from marivo.runtime.runtime import MarivoRuntime

# Type alias: intent runner signature.
_IntentRunner = Callable[[Any, str, dict[str, Any] | None], dict[str, Any]]


def observe(
    runtime: MarivoRuntime, session_id: SessionId, request: AoiAtomicRequest
) -> dict[str, Any]:
    return _run_aoi(runtime, "observe", session_id, request)


def compare(
    runtime: MarivoRuntime, session_id: SessionId, request: AoiAtomicRequest
) -> dict[str, Any]:
    return _run_aoi(runtime, "compare", session_id, request)


def decompose(
    runtime: MarivoRuntime, session_id: SessionId, request: AoiAtomicRequest
) -> dict[str, Any]:
    return _run_aoi(runtime, "decompose", session_id, request)


def correlate(
    runtime: MarivoRuntime, session_id: SessionId, request: AoiAtomicRequest
) -> dict[str, Any]:
    return _run_aoi(runtime, "correlate", session_id, request)


def detect(
    runtime: MarivoRuntime, session_id: SessionId, request: AoiAtomicRequest
) -> dict[str, Any]:
    return _run_aoi(runtime, "detect", session_id, request)


def forecast(
    runtime: MarivoRuntime, session_id: SessionId, request: AoiAtomicRequest
) -> dict[str, Any]:
    return _run_aoi(runtime, "forecast", session_id, request)


def attribute(
    runtime: MarivoRuntime, session_id: SessionId, request: AoiDerivedRequest
) -> dict[str, Any]:
    return _run_aoi_derived(runtime, "attribute", session_id, request)


def diagnose(
    runtime: MarivoRuntime, session_id: SessionId, params: dict[str, Any]
) -> dict[str, Any]:
    return _run_derived(runtime, "diagnose", session_id, params)


def test(
    runtime: MarivoRuntime, session_id: SessionId, request: AoiAtomicRequest
) -> dict[str, Any]:
    return _run_aoi(runtime, "test", session_id, request)


def validate(
    runtime: MarivoRuntime, session_id: SessionId, request: AoiDerivedRequest
) -> dict[str, Any]:
    return _run_aoi_derived(runtime, "validate", session_id, request)


AOI_RUNNERS: dict[str, _IntentRunner] = {
    "observe": run_observe_intent,
    "compare": run_compare_intent,
    "decompose": run_decompose_intent,
    "correlate": run_correlate_intent,
    "detect": run_detect_intent,
    "forecast": run_forecast_intent,
    "test": run_test_intent,
}

DERIVED_RUNNERS: dict[str, _IntentRunner] = {
    "attribute": run_attribute_intent,
    "diagnose": run_diagnose_intent,
    "validate": run_validate_intent,
}

# Mapping from intent type string to wrapper function.
INTENT_DISPATCHERS: dict[str, _IntentRunner] = {}
for _name, _fn in [
    ("observe", observe),
    ("compare", compare),
    ("decompose", decompose),
    ("correlate", correlate),
    ("detect", detect),
    ("forecast", forecast),
    ("attribute", attribute),
    ("diagnose", diagnose),
    ("test", test),
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


def _run_aoi(
    runtime: MarivoRuntime,
    intent_type: str,
    session_id: SessionId,
    request: AoiAtomicRequest,
) -> dict[str, Any]:
    _assert_session_is_open(runtime, session_id)
    assert_request_matches_intent(intent_type, request)
    params = lower_aoi_request(intent_type, request)
    return AOI_RUNNERS[intent_type](runtime, str(session_id), params)


def _run_derived(
    runtime: MarivoRuntime,
    intent_type: str,
    session_id: SessionId,
    params: dict[str, Any],
) -> dict[str, Any]:
    _assert_session_is_open(runtime, session_id)
    return DERIVED_RUNNERS[intent_type](runtime, str(session_id), params)


def _run_aoi_derived(
    runtime: MarivoRuntime,
    intent_type: str,
    session_id: SessionId,
    request: AoiDerivedRequest,
) -> dict[str, Any]:
    _assert_session_is_open(runtime, session_id)
    params = lower_aoi_derived_request(intent_type, request)
    return DERIVED_RUNNERS[intent_type](runtime, str(session_id), params)


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
