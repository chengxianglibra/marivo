# tests/test_forecast_dynamic_grain_rejected.py

from marivo.analysis._semantic_persistence import AxisBindingV1
from marivo.analysis.intents.forecast import _time_axis
from marivo.refs import RefPayloadV1
from marivo.refs import ref as ref_factory


class _Meta:
    axis_bindings = (
        AxisBindingV1(
            ref=RefPayloadV1.from_ref(ref_factory.time_dimension("sales.orders.created_at")),
            column="bucket_start",
            role="time_dimension",
            grain="5minute",
        ),
    )


class _Frame:
    meta = _Meta()


def test_time_axis_reports_dynamic_grain_token():
    _col, grain = _time_axis(_Frame())
    assert grain == "5minute"


def test_forecast_freq_excludes_dynamic_grain():
    from marivo.analysis.intents.forecast import _FREQ

    assert "5minute" not in _FREQ
    assert "minute" not in _FREQ
