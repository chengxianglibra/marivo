import marivo.analysis.windows.spec as window_spec
from marivo.analysis.windows.grain import Grain
from marivo.analysis.windows.spec import (
    AbsoluteWindow,
    TimeScope,
    dump_window,
    make_absolute_window,
)


def test_window_normalizes_grain_input():
    w = AbsoluteWindow(start="2026-06-03", end="2026-06-04", grain=(5, "minute"))
    assert w.grain == Grain(count=5, unit="minute")


def test_window_serializes_grain_to_token():
    w = AbsoluteWindow(start="2026-06-03", end="2026-06-04", grain=(5, "minute"))
    assert w.model_dump(mode="json")["grain"] == "5minute"
    assert dump_window(w)["grain"] == "5minute"


def test_window_roundtrips_token():
    raw = {"start": "2026-06-03", "end": "2026-06-04", "grain": "5minute"}
    w = AbsoluteWindow.model_validate(raw)
    assert w.grain == Grain(count=5, unit="minute")


def test_make_absolute_window_normalizes():
    w = make_absolute_window(TimeScope(start="2026-06-03", end="2026-06-04"), grain=(10, "minute"))
    assert w is not None
    assert w.grain == Grain(count=10, unit="minute")


def test_timegrain_alias_is_removed():
    assert not hasattr(window_spec, "TimeGrain")
