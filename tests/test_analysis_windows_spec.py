import pytest

from marivo.analysis.errors import WindowInvalidError
from marivo.analysis.windows.spec import AbsoluteWindow, TimeScope, normalize_timescope_input


def test_normalize_timescope_input_accepts_concrete_instances():
    absolute = AbsoluteWindow(start="2026-05-01", end="2026-05-24")
    scope = TimeScope(start="2026-05-01", end="2026-05-24")
    assert normalize_timescope_input(absolute) == scope
    assert normalize_timescope_input(scope) is scope


def test_normalize_timescope_input_rejects_strings():
    with pytest.raises(WindowInvalidError) as exc_info:
        normalize_timescope_input("last 7 days")
    assert exc_info.value._context["kind"] == "TimeScopeTypeInvalid"


def test_normalize_timescope_input_accepts_start_end_dict():
    scope = normalize_timescope_input({"start": "2026-05-01", "end": "2026-05-24"})
    assert scope == TimeScope(start="2026-05-01", end="2026-05-24")


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
    assert exc_info.value._context["kind"] == "TimeScopeModelInvalid"


def test_normalize_timescope_input_rejects_invalid_type():
    with pytest.raises(WindowInvalidError) as exc_info:
        normalize_timescope_input(123)
    assert exc_info.value._context["kind"] == "TimeScopeTypeInvalid"


def test_normalize_timescope_input_rejects_invalid_model():
    with pytest.raises(WindowInvalidError) as exc_info:
        normalize_timescope_input({"start": "2026-05-01"})
    assert exc_info.value._context["kind"] == "TimeScopeModelInvalid"
