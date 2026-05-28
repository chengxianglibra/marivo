import pytest

from marivo.analysis.errors import WindowInvalidError
from marivo.analysis.windows.spec import AbsoluteWindow, RelativeWindow, normalize_window_input


def test_normalize_window_input_accepts_concrete_instances():
    absolute = AbsoluteWindow(start="2026-05-01", end="2026-05-24")
    relative = RelativeWindow(expr="mtd")
    assert normalize_window_input(absolute) is absolute
    assert normalize_window_input(relative) is relative


def test_normalize_window_input_string_is_relative_shortcut():
    out = normalize_window_input("last 7 days")
    assert isinstance(out, RelativeWindow)
    assert out.expr == "last 7 days"


def test_normalize_window_input_dict_routes_to_relative_or_absolute():
    relative = normalize_window_input({"expr": "mtd", "grain": "day"})
    absolute = normalize_window_input({"start": "2026-05-01", "end": "2026-05-24"})
    assert isinstance(relative, RelativeWindow)
    assert relative.grain == "day"
    assert isinstance(absolute, AbsoluteWindow)


def test_normalize_window_input_rejects_mixed_shape():
    with pytest.raises(WindowInvalidError) as exc_info:
        normalize_window_input({"expr": "mtd", "start": "2026-05-01", "end": "2026-05-24"})
    assert exc_info.value.details["kind"] == "MixedWindowForm"


def test_normalize_window_input_rejects_invalid_type():
    with pytest.raises(WindowInvalidError) as exc_info:
        normalize_window_input(123)
    assert exc_info.value.details["kind"] == "WindowTypeInvalid"


def test_normalize_window_input_rejects_invalid_absolute_model():
    with pytest.raises(WindowInvalidError) as exc_info:
        normalize_window_input({"start": "2026-05-01"})
    assert exc_info.value.details["kind"] == "WindowModelInvalid"
    assert exc_info.value.details["model"] == "absolute"


def test_normalize_window_input_rejects_invalid_relative_model():
    with pytest.raises(WindowInvalidError) as exc_info:
        normalize_window_input({"expr": {"raw": "mtd"}, "grain": "decade"})
    assert exc_info.value.details["kind"] == "WindowModelInvalid"
    assert exc_info.value.details["model"] == "relative"
