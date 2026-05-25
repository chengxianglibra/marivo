"""v1.2 frame loading compatibility checks."""

import json

import pandas as pd
import pytest

import marivo.analysis_py as mv
import marivo.analysis_py.session.attach as session_attach
from marivo.analysis_py.errors import FrameMetaInvalidError
from marivo.analysis_py.frames.metric import MetricFrame


@pytest.fixture(autouse=True)
def _chdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    yield


def test_load_frame_coerces_legacy_window_dict():
    session = session_attach.create(name="demo")
    frame = MetricFrame.from_dataframe(
        pd.DataFrame({"value": [1.0]}),
        metric_id="custom.metric",
        axes={},
        measure={"name": "value"},
        semantic_kind="scalar",
        semantic_model="custom",
        session=session,
    )
    meta_file = session.layout.frames_dir / frame.ref / "meta.json"
    meta = json.loads(meta_file.read_text())
    meta["window"] = {
        "start": "2026-05-01",
        "end": "2026-05-24",
        "rogue_key": "drop-me",
    }
    meta_file.write_text(json.dumps(meta, ensure_ascii=False, indent=2))

    loaded = mv.load_frame(frame.ref, session=session)

    assert loaded.meta.window is not None
    assert loaded.meta.window["kind"] == "absolute"
    assert loaded.meta.window["start"] == "2026-05-01"
    assert loaded.meta.window["end"] == "2026-05-24"
    assert "rogue_key" not in loaded.meta.window


def test_load_frame_rejects_unparseable_legacy_window():
    session = session_attach.create(name="demo")
    frame = MetricFrame.from_dataframe(
        pd.DataFrame({"value": [1.0]}),
        metric_id="custom.metric",
        axes={},
        measure={"name": "value"},
        semantic_kind="scalar",
        semantic_model="custom",
        session=session,
    )
    meta_file = session.layout.frames_dir / frame.ref / "meta.json"
    meta = json.loads(meta_file.read_text())
    meta["window"] = {"foo": "bar"}
    meta_file.write_text(json.dumps(meta, ensure_ascii=False, indent=2))

    with pytest.raises(FrameMetaInvalidError) as exc_info:
        mv.load_frame(frame.ref, session=session)

    assert exc_info.value.details.get("kind") == "LegacyWindowShapeInvalid"


def test_load_frame_wraps_legacy_window_validation_error():
    session = session_attach.create(name="demo")
    frame = MetricFrame.from_dataframe(
        pd.DataFrame({"value": [1.0]}),
        metric_id="custom.metric",
        axes={},
        measure={"name": "value"},
        semantic_kind="scalar",
        semantic_model="custom",
        session=session,
    )
    meta_file = session.layout.frames_dir / frame.ref / "meta.json"
    meta = json.loads(meta_file.read_text())
    meta["window"] = {
        "start": "2026-05-01",
        "end": "2026-05-24",
        "grain": "invalid-grain",
    }
    meta_file.write_text(json.dumps(meta, ensure_ascii=False, indent=2))

    with pytest.raises(FrameMetaInvalidError) as exc_info:
        mv.load_frame(frame.ref, session=session)

    assert exc_info.value.details.get("kind") == "LegacyWindowShapeInvalid"
