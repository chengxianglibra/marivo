# tests/test_forecast_dynamic_grain_rejected.py

from marivo.analysis.intents.forecast import _time_axis


class _Meta:
    axes = {"time": {"role": "time", "column": "bucket_start", "grain": "5minute"}}


class _Frame:
    meta = _Meta()


def test_time_axis_reports_dynamic_grain_token():
    _col, grain = _time_axis(_Frame())
    assert grain == "5minute"


def test_forecast_freq_excludes_dynamic_grain():
    from marivo.analysis.intents.forecast import _FREQ

    assert "5minute" not in _FREQ
    assert "minute" not in _FREQ
