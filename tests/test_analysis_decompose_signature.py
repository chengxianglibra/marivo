"""decompose's public signature exposes no private/internal parameters."""

import inspect

from marivo.analysis.intents.decompose import decompose


def test_decompose_signature_has_no_triggered_by():
    params = inspect.signature(decompose).parameters
    assert "_triggered_by" not in params


def test_decompose_signature_is_frame_axis_session_only():
    params = list(inspect.signature(decompose).parameters)
    assert params == ["frame", "axis", "session"]
