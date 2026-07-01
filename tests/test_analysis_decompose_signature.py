"""decompose's public signature exposes no private/internal parameters."""

import inspect

from marivo.analysis.intents.decompose import decompose


def test_decompose_signature_has_no_triggered_by():
    params = inspect.signature(decompose).parameters
    assert "_triggered_by" not in params


def test_decompose_signature_is_frame_axes_axis_session_with_internal_kwargs():
    params = list(inspect.signature(decompose).parameters)
    assert params == ["frame", "axes", "axis", "session", "_intent", "_params_extra"]
