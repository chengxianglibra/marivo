"""BaseFrameMeta + BaseFrame: thin pandas wrapper with explicit boundaries."""

from datetime import UTC, datetime

import pandas as pd
import pytest

from marivo.analysis_py.errors import FrameMutationError
from marivo.analysis_py.frames.base import BaseFrame, BaseFrameMeta
from marivo.analysis_py.lineage import Lineage


def _meta(**overrides) -> BaseFrameMeta:
    defaults = {
        "kind": "metric_frame",
        "ref": "frame_abc12345",
        "session_id": "sess_a3b21c89",
        "project_root": "/tmp/proj",
        "produced_by_job": "job_e7c4f8a1",
        "created_at": datetime(2026, 5, 24, 10, 23, 11, tzinfo=UTC),
        "row_count": 2,
        "byte_size": 128,
        "lineage": Lineage(),
    }
    defaults.update(overrides)
    return BaseFrameMeta(**defaults)


def test_meta_construction_minimum_fields():
    meta = _meta()
    assert meta.ref == "frame_abc12345"
    assert meta.session_id == "sess_a3b21c89"
    assert meta.row_count == 2


def test_meta_kind_required():
    with pytest.raises(Exception):
        BaseFrameMeta()  # type: ignore[call-arg]


def test_frame_construction_wraps_df_and_meta():
    df = pd.DataFrame({"x": [1, 2]})
    f = BaseFrame(_df=df, meta=_meta())
    assert f.ref == "frame_abc12345"
    assert f.lineage is f.meta.lineage


def test_to_pandas_returns_copy():
    df = pd.DataFrame({"x": [1, 2]})
    f = BaseFrame(_df=df, meta=_meta())
    out = f.to_pandas()
    out.loc[0, "x"] = 999
    assert df.loc[0, "x"] == 1


def test_getitem_delegates_to_df():
    df = pd.DataFrame({"x": [1, 2]})
    f = BaseFrame(_df=df, meta=_meta())
    assert list(f["x"]) == [1, 2]


def test_head_delegates_to_df():
    df = pd.DataFrame({"x": [1, 2, 3, 4, 5]})
    f = BaseFrame(_df=df, meta=_meta())
    assert len(f.head(2)) == 2


def test_shape_columns_len_iter():
    df = pd.DataFrame({"x": [1, 2], "y": [3, 4]})
    f = BaseFrame(_df=df, meta=_meta())
    assert f.shape == (2, 2)
    assert f.columns == ["x", "y"]
    assert len(f) == 2
    rows = list(f)
    assert rows == ["x", "y"]


def test_setitem_raises_frame_mutation_error():
    df = pd.DataFrame({"x": [1, 2]})
    f = BaseFrame(_df=df, meta=_meta())
    with pytest.raises(FrameMutationError):
        f["x"] = [99, 99]


def test_arithmetic_raises_frame_mutation_error():
    df = pd.DataFrame({"x": [1, 2]})
    f = BaseFrame(_df=df, meta=_meta())
    with pytest.raises(FrameMutationError):
        f + 1


def test_repr_includes_kind_ref_and_row_count():
    df = pd.DataFrame({"x": [1, 2]})
    f = BaseFrame(_df=df, meta=_meta())
    r = repr(f)
    assert "metric_frame" in r
    assert "frame_abc12345" in r
    assert "row_count=2" in r or "n=2" in r
