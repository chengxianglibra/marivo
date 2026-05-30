"""Window normalization for MetricFrame.from_dataframe."""

import pandas as pd
import pytest

import marivo.analysis.session.attach as session_attach
from marivo.analysis.frames.metric import MetricFrame
from marivo.analysis.windows import RelativeWindow


@pytest.fixture(autouse=True)
def _chdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TZ", "Asia/Shanghai")
    session_attach._reset_process_state()
    yield


def test_from_dataframe_accepts_absolute_window_dict():
    session = session_attach.get_or_create(name="demo")
    frame = MetricFrame.from_dataframe(
        pd.DataFrame({"value": [1.0]}),
        metric_id="custom.metric",
        axes={},
        measure={"name": "value"},
        semantic_kind="scalar",
        semantic_model="custom",
        window={"start": "2026-05-01", "end": "2026-05-24"},
        session=session,
    )

    assert frame.meta.window is not None
    assert frame.meta.window["kind"] == "absolute"


def test_from_dataframe_accepts_relative_window_instance():
    session = session_attach.get_or_create(name="demo")
    frame = MetricFrame.from_dataframe(
        pd.DataFrame({"value": [1.0]}),
        metric_id="custom.metric",
        axes={},
        measure={"name": "value"},
        semantic_kind="scalar",
        semantic_model="custom",
        window=RelativeWindow(expr="ytd", as_of="2026-05-24T13:42:11+08:00"),
        session=session,
    )

    assert frame.meta.window is not None
    assert frame.meta.window["kind"] == "absolute"
    assert frame.meta.window["start"] == "2026-01-01"


def test_from_dataframe_relative_window_uses_session_timezone():
    session = session_attach.get_or_create(name="demo")
    frame = MetricFrame.from_dataframe(
        pd.DataFrame({"value": [1.0]}),
        metric_id="custom.metric",
        axes={},
        measure={"name": "value"},
        semantic_kind="scalar",
        semantic_model="custom",
        window=RelativeWindow(
            expr="ytd",
            as_of="2025-12-31T20:30:00+00:00",
        ),
        session=session,
    )

    assert frame.meta.window is not None
    assert frame.meta.window["kind"] == "absolute"
    assert frame.meta.window["start"] == "2026-01-01"
    assert frame.meta.window["end"] == "2026-01-01"
