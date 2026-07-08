"""Observe execution for cumulative metrics."""

from __future__ import annotations

import ibis
import pandas as pd
import pytest

import marivo.analysis.session as session_attach
from marivo.analysis.intents.observe import observe
from marivo.semantic.catalog import SemanticKind
from marivo.semantic.refs import make_ref


def _bootstrap_project(tmp_path) -> None:
    """Create a semantic project with cumulative metrics on disk."""
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
    (tmp_path / "marivo.toml").write_text('[project]\nname = "test"\n')
    (semantic_dir / "metrics.py").write_text(
        "import marivo.datasource as md\n"
        "import marivo.semantic as ms\n"
        "warehouse = md.ref('datasource.warehouse')\n"
        "events = ms.entity(name='events', datasource=warehouse, source=ms.table('events'))\n"
        "event_time = ms.time_dimension_column("
        "name='event_time', entity=events, column='event_time', granularity='day')\n"
        "region = ms.dimension_column(name='region', entity=events, column='region')\n"
        "amount = ms.measure_column("
        "name='amount', entity=events, column='amount', additivity='additive', unit='USD')\n"
        "user_id = ms.measure_column("
        "name='user_id', entity=events, column='user_id', additivity='non_additive')\n"
        "gmv = ms.aggregate(name='gmv', measure=amount, agg='sum')\n"
        "active_users = ms.aggregate(name='active_users', measure=user_id, agg='count_distinct')\n"
        "cum_gmv = ms.cumulative(name='cum_gmv', base=gmv, over=event_time)\n"
        "cum_active_users = ms.cumulative(name='cum_active_users', base=active_users, over=event_time)\n"
        "buyers = ms.aggregate(name='buyers', measure=user_id, agg='count_distinct')\n"
        "cum_buyers = ms.cumulative(name='cum_buyers', base=buyers, over=event_time)\n"
        "cum_active_rate = ms.ratio(name='cum_active_rate', numerator=cum_buyers, denominator=cum_active_users)\n",
        encoding="utf-8",
    )


def _seed(con) -> None:
    con.create_table(
        "events",
        pd.DataFrame(
            {
                "event_id": [1, 2, 3, 4, 5, 6],
                "event_time": pd.to_datetime(
                    [
                        "2026-06-29",
                        "2026-07-01",
                        "2026-07-01",
                        "2026-07-03",
                        "2026-07-03",
                        "2026-07-05",
                    ]
                ),
                "region": ["US", "US", "CA", "US", "CA", "CA"],
                "amount": [5.0, 10.0, 20.0, 7.0, 3.0, 11.0],
                "user_id": [100, 100, 101, 102, 101, 103],
            }
        ),
        overwrite=True,
    )


def _session(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _bootstrap_project(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    return session_attach.get_or_create(name="cum", backends={"warehouse": lambda: con})


def test_cumulative_time_series_carries_forward_and_uses_all_history_baseline(
    tmp_path, monkeypatch
) -> None:
    session = _session(tmp_path, monkeypatch)

    frame = observe(
        make_ref("sales.cum_gmv", SemanticKind.METRIC),
        time_scope={"start": "2026-07-01", "end": "2026-07-06"},
        grain="day",
        session=session,
    )

    df = frame.to_pandas()
    assert frame.meta.semantic_kind == "time_series"
    assert frame.meta.reaggregatable is False
    assert frame.meta.cumulative == {
        "kind": "cumulative",
        "base": "sales.gmv",
        "over": "sales.events.event_time",
        "anchor": "all_history",
        "components": None,
    }
    by_day = {str(row.bucket_start.date()): row.value for row in df.itertuples()}
    assert by_day == {
        "2026-07-01": pytest.approx(35.0),
        "2026-07-02": pytest.approx(35.0),
        "2026-07-03": pytest.approx(45.0),
        "2026-07-04": pytest.approx(45.0),
        "2026-07-05": pytest.approx(56.0),
    }


def test_cumulative_count_distinct_uses_first_seen_not_bucket_sum(tmp_path, monkeypatch) -> None:
    session = _session(tmp_path, monkeypatch)

    frame = observe(
        make_ref("sales.cum_active_users", SemanticKind.METRIC),
        time_scope={"start": "2026-07-01", "end": "2026-07-06"},
        grain="day",
        session=session,
    )

    by_day = {str(row.bucket_start.date()): row.value for row in frame.to_pandas().itertuples()}
    assert by_day == {
        "2026-07-01": pytest.approx(2.0),
        "2026-07-02": pytest.approx(2.0),
        "2026-07-03": pytest.approx(3.0),
        "2026-07-04": pytest.approx(3.0),
        "2026-07-05": pytest.approx(4.0),
    }


def test_cumulative_panel_counts_once_per_slice(tmp_path, monkeypatch) -> None:
    session = _session(tmp_path, monkeypatch)

    frame = observe(
        make_ref("sales.cum_active_users", SemanticKind.METRIC),
        time_scope={"start": "2026-07-01", "end": "2026-07-06"},
        grain="day",
        dimensions=[make_ref("region", SemanticKind.DIMENSION)],
        session=session,
    )

    df = frame.to_pandas()
    assert frame.meta.semantic_kind == "panel"
    ca = df[(df["region"] == "CA") & (df["bucket_start"].dt.date.astype(str) == "2026-07-05")]
    us = df[(df["region"] == "US") & (df["bucket_start"].dt.date.astype(str) == "2026-07-05")]
    assert ca.iloc[0]["value"] == pytest.approx(2.0)
    assert us.iloc[0]["value"] == pytest.approx(2.0)


def test_cumulative_scalar_as_of_window_end(tmp_path, monkeypatch) -> None:
    session = _session(tmp_path, monkeypatch)

    frame = observe(
        make_ref("sales.cum_gmv", SemanticKind.METRIC),
        time_scope={"start": "2026-07-01", "end": "2026-07-04"},
        session=session,
    )

    assert frame.meta.semantic_kind == "scalar"
    assert frame.to_pandas().iloc[0]["value"] == pytest.approx(45.0)


def test_cumulative_month_grain_does_not_hang(tmp_path, monkeypatch) -> None:
    """C1: month grain must not cause an infinite loop in _bucket_date_range."""
    session = _session(tmp_path, monkeypatch)

    frame = observe(
        make_ref("sales.cum_gmv", SemanticKind.METRIC),
        time_scope={"start": "2026-06-01", "end": "2026-08-01"},
        grain="month",
        session=session,
    )

    df = frame.to_pandas()
    assert frame.meta.semantic_kind == "time_series"
    # Window [2026-06-01, 2026-08-01) with month grain should produce 2 buckets.
    by_month = {str(row.bucket_start.date()): row.value for row in df.itertuples()}
    assert by_month == {
        "2026-06-01": pytest.approx(5.0),
        "2026-07-01": pytest.approx(56.0),
    }


def test_cumulative_where_filter_applied_to_all_paths(tmp_path, monkeypatch) -> None:
    """C2: where filter must be applied to scalar, baseline, flow, and count_distinct paths."""
    session = _session(tmp_path, monkeypatch)

    # --- Scalar path: where filter should exclude CA events ---
    # Total without filter (as-of 2026-07-06): 56.0
    # US-only: 5.0 + 10.0 + 7.0 = 22.0
    scalar_frame = observe(
        make_ref("sales.cum_gmv", SemanticKind.METRIC),
        time_scope={"start": "2026-07-01", "end": "2026-07-06"},
        slice_by={make_ref("region", SemanticKind.DIMENSION): "US"},
        session=session,
    )
    assert scalar_frame.meta.semantic_kind == "scalar"
    assert scalar_frame.to_pandas().iloc[0]["value"] == pytest.approx(22.0)

    # --- Time-series path: where filter should apply to baseline + flow ---
    # US-only baseline (before 2026-07-01): 5.0 (event on 2026-06-29)
    # US-only flow: 10.0 (07-01) + 7.0 (07-03) = 17.0
    # Cumulative: 5+10=15, 15, 15+7=22, 22, 22
    ts_frame = observe(
        make_ref("sales.cum_gmv", SemanticKind.METRIC),
        time_scope={"start": "2026-07-01", "end": "2026-07-06"},
        grain="day",
        slice_by={make_ref("region", SemanticKind.DIMENSION): "US"},
        session=session,
    )
    by_day = {str(row.bucket_start.date()): row.value for row in ts_frame.to_pandas().itertuples()}
    assert by_day == {
        "2026-07-01": pytest.approx(15.0),
        "2026-07-02": pytest.approx(15.0),
        "2026-07-03": pytest.approx(22.0),
        "2026-07-04": pytest.approx(22.0),
        "2026-07-05": pytest.approx(22.0),
    }

    # --- Count_distinct path: where filter should apply to combined first-seen query ---
    # US-only users: 100 (first seen 2026-06-29), 102 (first seen 2026-07-03)
    # Cumulative active US users:
    #   07-01: 1 (user 100, first seen 06-29)
    #   07-02: 1
    #   07-03: 2 (user 102 first seen)
    #   07-04: 2
    #   07-05: 2
    cd_frame = observe(
        make_ref("sales.cum_active_users", SemanticKind.METRIC),
        time_scope={"start": "2026-07-01", "end": "2026-07-06"},
        grain="day",
        slice_by={make_ref("region", SemanticKind.DIMENSION): "US"},
        session=session,
    )
    cd_by_day = {
        str(row.bucket_start.date()): row.value for row in cd_frame.to_pandas().itertuples()
    }
    assert cd_by_day == {
        "2026-07-01": pytest.approx(1.0),
        "2026-07-02": pytest.approx(1.0),
        "2026-07-03": pytest.approx(2.0),
        "2026-07-04": pytest.approx(2.0),
        "2026-07-05": pytest.approx(2.0),
    }


# ---------------------------------------------------------------------------
# Sub-day multi-count grain alignment
# ---------------------------------------------------------------------------


def _bootstrap_hourly_project(tmp_path) -> None:
    """Like _bootstrap_project but the time dimension has granularity='hour'."""
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
    (tmp_path / "marivo.toml").write_text('[project]\nname = "test"\n')
    (semantic_dir / "metrics.py").write_text(
        "import marivo.datasource as md\n"
        "import marivo.semantic as ms\n"
        "warehouse = md.ref('datasource.warehouse')\n"
        "events = ms.entity(name='events', datasource=warehouse, source=ms.table('events'))\n"
        "event_time = ms.time_dimension_column("
        "name='event_time', entity=events, column='event_time', granularity='hour')\n"
        "amount = ms.measure_column("
        "name='amount', entity=events, column='amount', additivity='additive', unit='USD')\n"
        "gmv = ms.aggregate(name='gmv', measure=amount, agg='sum')\n"
        "cum_gmv = ms.cumulative(name='cum_gmv', base=gmv, over=event_time)\n",
        encoding="utf-8",
    )


def _seed_hourly(con) -> None:
    """Seed events at specific hours within a single day."""
    con.create_table(
        "events",
        pd.DataFrame(
            {
                "event_id": [1, 2, 3],
                "event_time": pd.to_datetime(
                    [
                        "2026-07-01 01:00",
                        "2026-07-01 03:00",
                        "2026-07-01 05:00",
                    ]
                ),
                "amount": [10.0, 20.0, 7.0],
            }
        ),
        overwrite=True,
    )


def _hourly_session(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _bootstrap_hourly_project(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed_hourly(con)
    return session_attach.get_or_create(
        name="cum_subday",
        report_timezone="UTC",
        backends={"warehouse": lambda: con},
    )


def test_cumulative_subday_multi_count_grain_day_anchored(tmp_path, monkeypatch) -> None:
    """Spine bucket-starts must be day-anchored for count > 1 sub-day grains.

    With grain="2h" and a window starting at 03:30, bucket_start_expr anchors
    buckets at day-start multiples of 2h: 00:00, 02:00, 04:00, 06:00, ...
    The spine must produce the same anchors, otherwise the merge in
    _dense_cumulative_frame produces all-NaN flow values and a flat baseline.

    Events (all on 2026-07-01):
      01:00  amount=10.0   -> SQL bucket 00:00  (before window -> baseline)
      03:00  amount=20.0   -> SQL bucket 02:00  (before window -> baseline)
      05:00  amount= 7.0   -> SQL bucket 04:00  (in window -> flow)

    Window [03:30, 08:00), grain=2h.
    Baseline (all history before 03:30) = 10.0 + 20.0 = 30.0.
    Aligned start = 02:00 (day-anchored).
    Spine: 02:00, 04:00, 06:00.
    Cumulative: 02:00 -> 30.0, 04:00 -> 37.0, 06:00 -> 37.0.
    """
    session = _hourly_session(tmp_path, monkeypatch)

    frame = observe(
        make_ref("sales.cum_gmv", SemanticKind.METRIC),
        time_scope={"start": "2026-07-01 03:30", "end": "2026-07-01 08:00"},
        grain="2h",
        session=session,
    )

    df = frame.to_pandas()
    by_bucket = {row.bucket_start.strftime("%H:%M"): row.value for row in df.itertuples()}
    assert by_bucket == {
        "02:00": pytest.approx(30.0),
        "04:00": pytest.approx(37.0),
        "06:00": pytest.approx(37.0),
    }


# ---------------------------------------------------------------------------
# Ratio-over-cumulative components (Task 5)
# ---------------------------------------------------------------------------


def test_ratio_over_cumulative_components_observes_and_marks_cumulative(
    tmp_path, monkeypatch
) -> None:
    session = _session(tmp_path, monkeypatch)

    frame = observe(
        make_ref("sales.cum_active_rate", SemanticKind.METRIC),
        time_scope={"start": "2026-07-01", "end": "2026-07-06"},
        grain="day",
        session=session,
    )

    assert frame.meta.semantic_kind == "time_series"
    assert frame.meta.reaggregatable is False
    assert frame.meta.cumulative["kind"] == "derived_contains_cumulative"
    assert frame.meta.cumulative["components"]["numerator"]["base"] == "sales.buyers"
    assert frame.meta.cumulative["components"]["denominator"]["base"] == "sales.active_users"
    assert frame.meta.cumulative["components"]["numerator"]["over"] == "sales.events.event_time"
    assert frame.meta.cumulative["components"]["denominator"]["over"] == "sales.events.event_time"
    assert frame.meta.component_ref is not None
    components = frame.components().to_pandas()
    assert {"bucket_start", "cum_buyers", "cum_active_users", "cum_active_rate"}.issubset(
        components.columns
    )
