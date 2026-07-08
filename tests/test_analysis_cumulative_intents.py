"""Intent gates for cumulative frames."""

from __future__ import annotations

from datetime import UTC, datetime

import ibis
import pandas as pd
import pytest

import marivo.analysis.session as session_attach
from marivo.analysis.errors import CumulativeFrameUnsupportedError
from marivo.analysis.frames.delta import DeltaFrame, DeltaFrameMeta
from marivo.analysis.intents.attribute import attribute
from marivo.analysis.intents.compare import compare
from marivo.analysis.intents.decompose import decompose
from marivo.analysis.intents.forecast import forecast
from marivo.analysis.lineage import Lineage, LineageStep
from tests.shared_fixtures import make_metric_frame


def _cum_marker() -> dict:
    return {
        "kind": "cumulative",
        "base": "sales.gmv",
        "over": "sales.orders.event_time",
        "anchor": "all_history",
        "components": None,
    }


def _bootstrap_project(tmp_path) -> None:
    """Create a minimal semantic project on disk for analysis tests."""
    (tmp_path / "marivo.toml").write_text('[project]\nname = "test"\n')
    semantic_dir = tmp_path / "models" / "semantic" / "sales"
    semantic_dir.mkdir(parents=True)
    (semantic_dir / "__init__.py").write_text("")
    (semantic_dir / "_domain.py").write_text(
        "import marivo.datasource as md\n"
        "import marivo.semantic as ms\n"
        "ms.domain(name='sales', owner='Data')\n",
        encoding="utf-8",
    )
    datasource_dir = tmp_path / "models" / "datasources"
    datasource_dir.mkdir(parents=True, exist_ok=True)
    (datasource_dir / "warehouse.py").write_text(
        "import marivo.datasource as md\nmd.duckdb(name='warehouse', path=':memory:')\n",
        encoding="utf-8",
    )
    (semantic_dir / "datasets.py").write_text(
        "import marivo.datasource as md\n"
        "import marivo.semantic as ms\n"
        "warehouse = md.ref('datasource.warehouse')\n"
        "orders = ms.entity(name='orders', datasource=warehouse, source=ms.table('orders'))\n"
        "order_date = ms.time_dimension_column("
        "name='order_date', entity=orders, column='created_at', granularity='day')\n"
        "region = ms.dimension_column(name='region', entity=orders, column='region')\n"
        "amount = ms.measure_column("
        "name='amount', entity=orders, column='amount', additivity='additive', unit='USD')\n"
        "gmv = ms.aggregate(name='gmv', measure=amount, agg='sum')\n"
        "cum_gmv = ms.cumulative(name='cum_gmv', base=gmv, over=order_date)\n",
        encoding="utf-8",
    )


def _seed(con) -> None:
    con.create_table(
        "orders",
        pd.DataFrame(
            {
                "order_id": [1, 2, 3],
                "created_at": pd.to_datetime(["2026-07-01", "2026-07-02", "2026-07-03"]),
                "amount": [10.0, 12.0, 18.0],
                "region": ["US", "US", "CA"],
            }
        ),
        overwrite=True,
    )


def _session(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _bootstrap_project(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    return session_attach.get_or_create(name="cum_gates", backends={"warehouse": lambda: con})


def _history(session):
    frame = make_metric_frame(
        pd.DataFrame(
            {
                "bucket_start": pd.to_datetime(["2026-07-01", "2026-07-02", "2026-07-03"]),
                "value": [10.0, 12.0, 18.0],
            }
        ),
        metric_id="sales.cum_gmv",
        axes={"time": {"role": "time", "column": "bucket_start", "grain": "1day"}},
        measure={"name": "cum_gmv"},
        semantic_kind="time_series",
        semantic_model="sales",
        window={"start": "2026-07-01", "end": "2026-07-04", "grain": "day"},
        session=session,
    )
    frame.meta = frame.meta.model_copy(update={"cumulative": _cum_marker()})
    return frame


def _now():
    return datetime(2026, 7, 8, 10, 0, 0, tzinfo=UTC)


def _delta(session, *, cumulative: dict | None = None) -> DeltaFrame:
    meta = DeltaFrameMeta(
        kind="delta_frame",
        ref="frame_delta",
        session_id=session.id,
        project_root=str(session.project_root),
        produced_by_job="job_delta",
        created_at=_now(),
        row_count=1,
        byte_size=0,
        lineage=Lineage(
            steps=[
                LineageStep(
                    intent="compare",
                    job_ref="job_delta",
                    inputs=["frame_a", "frame_b"],
                    params_digest="sha256:compare",
                )
            ]
        ),
        metric_id="sales.cum_gmv",
        source_current_ref="frame_a",
        source_baseline_ref="frame_b",
        alignment={"kind": "window_bucket"},
        semantic_kind="segmented",
        semantic_model="sales",
        cumulative=cumulative,
    )
    return DeltaFrame(_df=pd.DataFrame({"region": ["US"], "delta": [1.0]}), meta=meta)


def test_compare_rejects_cumulative_metric_frame(tmp_path, monkeypatch) -> None:
    session = _session(tmp_path, monkeypatch)
    current = _history(session)
    baseline = _history(session)

    with pytest.raises(CumulativeFrameUnsupportedError) as exc_info:
        compare(current, baseline, session=session)

    assert exc_info.value.details["intent"] == "compare"
    assert exc_info.value.details["base_metric_id"] == "sales.gmv"


def test_forecast_rejects_cumulative_history(tmp_path, monkeypatch) -> None:
    session = _session(tmp_path, monkeypatch)
    history = _history(session)

    with pytest.raises(CumulativeFrameUnsupportedError) as exc_info:
        forecast(history, horizon=2, session=session)

    assert "forecast the base flow" in exc_info.value.hint.lower()


def test_decompose_rejects_cumulative_delta(tmp_path, monkeypatch) -> None:
    session = _session(tmp_path, monkeypatch)
    delta = _delta(session, cumulative=_cum_marker())

    with pytest.raises(CumulativeFrameUnsupportedError) as exc_info:
        decompose(delta, axis="sales.orders.region", session=session)

    assert exc_info.value.details["intent"] == "decompose"
    assert exc_info.value.details["base_metric_id"] == "sales.gmv"


def test_attribute_rejects_cumulative_delta(tmp_path, monkeypatch) -> None:
    session = _session(tmp_path, monkeypatch)
    delta = _delta(session, cumulative=_cum_marker())

    with pytest.raises(CumulativeFrameUnsupportedError) as exc_info:
        attribute(delta, axes=["sales.orders.region"], session=session)

    assert exc_info.value.details["intent"] == "attribute"
    assert exc_info.value.details["base_metric_id"] == "sales.gmv"
