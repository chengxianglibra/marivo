import pytest

from marivo.analysis import time_scope
from marivo.analysis.errors import WindowInvalidError
from marivo.analysis.windows.spec import TimeScope, normalize_timescope_input


def test_normalize_timescope_input_accepts_concrete_instances():
    scope = time_scope(start="2026-05-01", end="2026-05-24")
    assert normalize_timescope_input(scope) is scope


def test_timescope_direct_constructor_is_not_public():
    with pytest.raises(TypeError, match="direct construction is not supported"):
        TimeScope(start="2026-05-01", end="2026-05-24")


def test_normalize_timescope_input_rejects_strings():
    with pytest.raises(WindowInvalidError) as exc_info:
        normalize_timescope_input("last 7 days")
    assert exc_info.value._context["kind"] == "TimeScopeTypeInvalid"


def test_normalize_timescope_input_rejects_start_end_dict():
    with pytest.raises(WindowInvalidError) as exc_info:
        normalize_timescope_input({"start": "2026-05-01", "end": "2026-05-24"})
    assert exc_info.value._context["kind"] == "TimeScopeTypeInvalid"


@pytest.mark.parametrize(
    "raw",
    [
        {"expr": "mtd"},
        {"start": "2026-05-01", "end": "2026-05-24", "grain": "day"},
        {"start": "2026-05-01", "end": "2026-05-24", "time_dimension": "created_at"},
        {"start": "2026-05-01", "end": "2026-05-24", "extra": "nope"},
    ],
)
def test_normalize_timescope_input_rejects_expr_and_non_scope_keys(raw):
    with pytest.raises(WindowInvalidError) as exc_info:
        normalize_timescope_input(raw)
    assert exc_info.value._context["kind"] == "TimeScopeTypeInvalid"


def test_normalize_timescope_input_rejects_invalid_type():
    with pytest.raises(WindowInvalidError) as exc_info:
        normalize_timescope_input(123)
    assert exc_info.value._context["kind"] == "TimeScopeTypeInvalid"


def test_normalize_timescope_input_rejects_invalid_model():
    with pytest.raises(WindowInvalidError) as exc_info:
        normalize_timescope_input({"start": "2026-05-01"})
    assert exc_info.value._context["kind"] == "TimeScopeTypeInvalid"
