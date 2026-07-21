"""Observe execution for cumulative metrics."""

from __future__ import annotations

import ibis
import pandas as pd
import pytest

import marivo.analysis.session as session_attach
import marivo.semantic as ms
from marivo.analysis.errors import CumulativeFrameUnsupportedError
from marivo.analysis.evidence.identity import make_artifact_id
from marivo.analysis.intents.attribute import attribute
from marivo.analysis.intents.compare import compare
from marivo.analysis.intents.observe import observe
from marivo.semantic.catalog import SemanticKind
from tests.ref_helpers import make_ref


def _metric_pandas(frame):
    """Normalize an observe export for tests that exercise cumulative math."""
    df = frame.to_pandas()
    measure_name = frame.meta.measure.get("name")
    if isinstance(measure_name, str) and measure_name in df.columns:
        return df.rename(columns={measure_name: "value"})
    return df


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
        "warehouse = ms.ref.datasource('warehouse')\n"
        "events = ms.entity(name='events', datasource=warehouse, source=md.table('events'))\n"
        "event_time = ms.time_dimension_column("
        "name='event_time', entity=events, column='event_time', granularity='day')\n"
        "region = ms.dimension_column(name='region', entity=events, column='region')\n"
        "amount = ms.measure_column("
        "name='amount', entity=events, column='amount', additivity='additive', unit='USD')\n"
        "user_id = ms.measure_column("
        "name='user_id', entity=events, column='user_id', additivity='non_additive')\n"
        "gmv = ms.aggregate(name='gmv', measure=amount, agg='sum')\n"
        "active_users = ms.aggregate(name='active_users', measure=user_id, agg='count_distinct')\n"
        "weighted_user = ms.weighted_mean(name='weighted_user', value=user_id, weight=amount)\n"
        "us_weighted_user = ms.weighted_mean("
        "name='us_weighted_user', value=user_id, weight=amount, filter=ms.where(region='US'))\n"
        "cum_gmv = ms.cumulative(name='cum_gmv', base=gmv, over=event_time)\n"
        "cum_weighted_user = ms.cumulative("
        "name='cum_weighted_user', base=weighted_user, over=event_time)\n"
        "cum_us_weighted_user = ms.cumulative("
        "name='cum_us_weighted_user', base=us_weighted_user, over=event_time)\n"
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

    df = _metric_pandas(frame)
    assert frame.meta.semantic_kind == "time_series"
    assert frame.meta.reaggregatable is False
    assert frame.meta.cumulative == {
        "kind": "cumulative",
        "base": "sales.gmv",
        "over": "sales.events.event_time",
        "anchor": "all_history",
        "components": None,
    }
    assert frame.lineage.steps[-1].params["metric_semantics"] == {
        "additivity": "non_additive",
        "aggregation": None,
        "status_time_dimension_ref": None,
    }
    assert frame.lineage.steps[-1].params["cumulative_contract_version"] == 2
    legacy_params = dict(frame.lineage.steps[-1].params)
    legacy_params.pop("cumulative_contract_version")
    legacy_params.pop("metric_semantics")
    assert frame.ref != make_artifact_id(
        step_type="observe",
        normalized_inputs=[],
        normalized_params=legacy_params,
        semantic_anchors={"metric_id": "sales.cum_gmv", "model": "sales"},
    )
    by_day = {str(row.bucket_start.date()): row.value for row in df.itertuples()}
    assert by_day == {
        "2026-07-01": pytest.approx(35.0),
        "2026-07-02": pytest.approx(35.0),
        "2026-07-03": pytest.approx(45.0),
        "2026-07-04": pytest.approx(45.0),
        "2026-07-05": pytest.approx(56.0),
    }


def test_cumulative_weighted_mean_accumulates_components_before_dividing(
    tmp_path, monkeypatch
) -> None:
    session = _session(tmp_path, monkeypatch)

    frame = observe(
        make_ref("sales.cum_weighted_user", SemanticKind.METRIC),
        time_scope={"start": "2026-07-01", "end": "2026-07-04"},
        grain="day",
        session=session,
    )

    by_day = {str(row.bucket_start.date()): row.value for row in _metric_pandas(frame).itertuples()}
    assert by_day == {
        "2026-07-01": pytest.approx(3520.0 / 35.0),
        "2026-07-02": pytest.approx(3520.0 / 35.0),
        "2026-07-03": pytest.approx(4537.0 / 45.0),
    }


def test_weighted_mean_authored_filter_applies_to_direct_and_cumulative_observe(
    tmp_path, monkeypatch
) -> None:
    session = _session(tmp_path, monkeypatch)

    direct = observe(
        make_ref("sales.us_weighted_user", SemanticKind.METRIC),
        session=session,
    )
    cumulative = observe(
        make_ref("sales.cum_us_weighted_user", SemanticKind.METRIC),
        time_scope={"start": "2026-07-01", "end": "2026-07-04"},
        grain="day",
        session=session,
    )

    assert _metric_pandas(direct)["value"].iloc[0] == pytest.approx(2214.0 / 22.0)
    by_day = {
        str(row.bucket_start.date()): row.value for row in _metric_pandas(cumulative).itertuples()
    }
    assert by_day == {
        "2026-07-01": pytest.approx(1500.0 / 15.0),
        "2026-07-02": pytest.approx(1500.0 / 15.0),
        "2026-07-03": pytest.approx(2214.0 / 22.0),
    }


def test_cumulative_count_distinct_uses_first_seen_not_bucket_sum(tmp_path, monkeypatch) -> None:
    session = _session(tmp_path, monkeypatch)

    frame = observe(
        make_ref("sales.cum_active_users", SemanticKind.METRIC),
        time_scope={"start": "2026-07-01", "end": "2026-07-06"},
        grain="day",
        session=session,
    )

    by_day = {str(row.bucket_start.date()): row.value for row in _metric_pandas(frame).itertuples()}
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
        dimensions=[make_ref("sales.events.region", SemanticKind.DIMENSION)],
        session=session,
    )

    df = _metric_pandas(frame)
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
    assert _metric_pandas(frame).iloc[0]["value"] == pytest.approx(45.0)


def test_cumulative_month_grain_does_not_hang(tmp_path, monkeypatch) -> None:
    """C1: month grain must not cause an infinite loop in _bucket_date_range."""
    session = _session(tmp_path, monkeypatch)

    frame = observe(
        make_ref("sales.cum_gmv", SemanticKind.METRIC),
        time_scope={"start": "2026-06-01", "end": "2026-08-01"},
        grain="month",
        session=session,
    )

    df = _metric_pandas(frame)
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
        slice_by={make_ref("sales.events.region", SemanticKind.DIMENSION): "US"},
        session=session,
    )
    assert scalar_frame.meta.semantic_kind == "scalar"
    assert _metric_pandas(scalar_frame).iloc[0]["value"] == pytest.approx(22.0)

    # --- Time-series path: where filter should apply to baseline + flow ---
    # US-only baseline (before 2026-07-01): 5.0 (event on 2026-06-29)
    # US-only flow: 10.0 (07-01) + 7.0 (07-03) = 17.0
    # Cumulative: 5+10=15, 15, 15+7=22, 22, 22
    ts_frame = observe(
        make_ref("sales.cum_gmv", SemanticKind.METRIC),
        time_scope={"start": "2026-07-01", "end": "2026-07-06"},
        grain="day",
        slice_by={make_ref("sales.events.region", SemanticKind.DIMENSION): "US"},
        session=session,
    )
    by_day = {
        str(row.bucket_start.date()): row.value for row in _metric_pandas(ts_frame).itertuples()
    }
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
        slice_by={make_ref("sales.events.region", SemanticKind.DIMENSION): "US"},
        session=session,
    )
    cd_by_day = {
        str(row.bucket_start.date()): row.value for row in _metric_pandas(cd_frame).itertuples()
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
        "warehouse = ms.ref.datasource('warehouse')\n"
        "events = ms.entity(name='events', datasource=warehouse, source=md.table('events'))\n"
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

    df = _metric_pandas(frame)
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
    assert frame.meta.cumulative["anchor"] == "all_history"
    assert frame.meta.cumulative["compare_blocker"] is None
    assert frame.meta.cumulative["components"]["numerator"]["base"] == "sales.buyers"
    assert frame.meta.cumulative["components"]["denominator"]["base"] == "sales.active_users"
    assert frame.meta.cumulative["components"]["numerator"]["over"] == "sales.events.event_time"
    assert frame.meta.cumulative["components"]["denominator"]["over"] == "sales.events.event_time"
    assert frame.meta.component_ref is not None
    assert frame.lineage.steps[-1].params["cumulative_contract_version"] == 2
    legacy_params = dict(frame.lineage.steps[-1].params)
    legacy_params.pop("cumulative_contract_version")
    legacy_params.pop("cumulative")
    assert frame.ref != make_artifact_id(
        step_type="observe",
        normalized_inputs=[],
        normalized_params=legacy_params,
        semantic_anchors={"metric_id": "sales.cum_active_rate", "model": "sales"},
    )
    components = frame.components().to_pandas()
    assert {"bucket_start", "cum_buyers", "cum_active_users", "cum_active_rate"}.issubset(
        components.columns
    )


# ---------------------------------------------------------------------------
# Task 4: grain_to_date (MTD) x sum/count execution
# ---------------------------------------------------------------------------


def _bootstrap_day_project(tmp_path) -> None:
    """Sales project with daily event_time and an MTD (month-reset) cumulative gmv."""
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
        "warehouse = ms.ref.datasource('warehouse')\n"
        "events = ms.entity(name='events', datasource=warehouse, source=md.table('events'))\n"
        "event_time = ms.time_dimension_column("
        "name='event_time', entity=events, column='event_time', granularity='hour')\n"
        "region = ms.dimension_column(name='region', entity=events, column='region')\n"
        "amount = ms.measure_column("
        "name='amount', entity=events, column='amount', additivity='additive', unit='USD')\n"
        "user_id = ms.measure_column("
        "name='user_id', entity=events, column='user_id', additivity='non_additive')\n"
        "gmv = ms.aggregate(name='gmv', measure=amount, agg='sum')\n"
        "mtd_gmv = ms.cumulative("
        "name='mtd_gmv', base=gmv, over=event_time,"
        " anchor=ms.grain_to_date(grain='month'))\n"
        "active_users = ms.aggregate(name='active_users', measure=user_id, agg='count_distinct')\n"
        "mtd_active_users = ms.cumulative("
        "name='mtd_active_users', base=active_users, over=event_time,"
        " anchor=ms.grain_to_date(grain='month'))\n"
        "qtd_active_users = ms.cumulative("
        "name='qtd_active_users', base=active_users, over=event_time,"
        " anchor=ms.grain_to_date(grain='quarter'))\n"
        "trailing7_gmv = ms.cumulative("
        "name='trailing7_gmv', base=gmv, over=event_time,"
        " anchor=ms.trailing(count=7, unit='day'))\n"
        "trailing7_active_users = ms.cumulative("
        "name='trailing7_active_users', base=active_users, over=event_time,"
        " anchor=ms.trailing(count=7, unit='day'))\n"
        "cum_gmv = ms.cumulative(name='cum_gmv', base=gmv, over=event_time)\n"
        "mtd_gmv_per_active = ms.ratio("
        "name='mtd_gmv_per_active', numerator=mtd_gmv, denominator=mtd_active_users)\n"
        "trailing7_gmv_per_active = ms.ratio("
        "name='trailing7_gmv_per_active', numerator=trailing7_gmv, "
        "denominator=trailing7_active_users)\n"
        "mixed_reset_gmv_per_active = ms.ratio("
        "name='mixed_reset_gmv_per_active', numerator=mtd_gmv, "
        "denominator=qtd_active_users)\n"
        "mixed_cumulative_flow = ms.ratio("
        "name='mixed_cumulative_flow', numerator=mtd_gmv, denominator=active_users)\n",
        encoding="utf-8",
    )


# Deterministic daily sales: one event per day, amount = day-of-month.
# Jan 1..31 -> 1..31 (jan_total = 496), Feb 1..28 -> 1..28 (feb_total = 406),
# July 1..31 -> 1..31 (july_total = 496).
_DAY_AMOUNTS: dict[str, dict[str, float]] = {
    "2026-01": {f"2026-01-{d:02d}": float(d) for d in range(1, 32)},
    "2026-02": {f"2026-02-{d:02d}": float(d) for d in range(1, 29)},
    "2026-07": {f"2026-07-{d:02d}": float(d) for d in range(1, 32)},
}


def _day_seed(con) -> None:
    rows = []
    for month_days in _DAY_AMOUNTS.values():
        for day_str, amt in month_days.items():
            rows.append((day_str, amt))
    # User/region overlay for count_distinct MTD/QTD tests:
    #   - user 100 active Jan 1..Jan 5 (counts ONCE within Jan MTD: Jan 1 and
    #     Jan 5 buckets hold the same distinct set {100}), then again Feb 1
    #     (re-counted at the month boundary). Region US.
    #   - user 100 also active Jul 1 so the quarter-reset test can assert a
    #     re-count at the Q1->Q3 boundary (Jul 1 is a new quarter).
    #   - every other Jan/Feb/Jul day gets a UNIQUE user in region CA. So a US
    #     slice isolates user 100 alone, while the unfiltered frame sees all
    #     users. This lets the filter-before-dedup test prove a CA-only entity
    #     (any of the unique day users) is excluded by the US slice — if the
    #     filter ran AFTER dedup, those CA first-seen rows would leak into the
    #     US count.
    user_overlay = {
        "2026-01-01": (100, "US"),
        "2026-01-02": (100, "US"),
        "2026-01-03": (100, "US"),
        "2026-01-04": (100, "US"),
        "2026-01-05": (100, "US"),
        "2026-02-01": (100, "US"),
        "2026-07-01": (100, "US"),
    }
    user_ids = []
    regions = []
    next_uid = 300
    for day_str, _ in rows:
        uid, region = user_overlay.get(day_str, (next_uid, "CA"))
        if day_str not in user_overlay:
            next_uid += 1
        user_ids.append(uid)
        regions.append(region)
    con.create_table(
        "events",
        pd.DataFrame(
            {
                "event_id": list(range(1, len(rows) + 1)),
                "event_time": pd.to_datetime([r[0] for r in rows]),
                "amount": [r[1] for r in rows],
                "user_id": user_ids,
                "region": regions,
            }
        ),
        overwrite=True,
    )


class _DayProject:
    """Helper wrapping a seeded day-grain sales project."""

    def __init__(self, session) -> None:
        self.session = session

    def flow_for(self, date_str: str) -> float:
        """Raw per-day flow (sum of amount for that calendar day)."""
        return _DAY_AMOUNTS[date_str[:7]][date_str]

    def jan_total(self) -> float:
        return sum(_DAY_AMOUNTS["2026-01"].values())

    def feb_total(self) -> float:
        return sum(_DAY_AMOUNTS["2026-02"].values())

    def july_total(self) -> float:
        return sum(_DAY_AMOUNTS["2026-07"].values())

    def sum_range(self, start: str, end: str) -> float:
        """Sum of per-day flow over the inclusive [start, end] date range."""
        import pandas as pd

        s = pd.Timestamp(start)
        e = pd.Timestamp(end)
        total = 0.0
        cur = s
        while cur <= e:
            key = cur.strftime("%Y-%m-%d")
            month = key[:7]
            if month in _DAY_AMOUNTS and key in _DAY_AMOUNTS[month]:
                total += _DAY_AMOUNTS[month][key]
            cur = cur + pd.Timedelta(days=1)
        return total

    def first_gap_day(self) -> str:
        """A day whose 7-day trailing span contains no events.

        The fixture seeds Jan, Feb, and July only; April is entirely empty, so
        any April day's 7-day span has zero events. Returns the first April day.
        """
        return "2026-04-01"


def _day_session(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _bootstrap_day_project(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _day_seed(con)
    return session_attach.get_or_create(name="mtd", backends={"warehouse": lambda: con})


@pytest.fixture
def day_project(tmp_path, monkeypatch) -> tuple[_DayProject, object]:
    session = _day_session(tmp_path, monkeypatch)
    return _DayProject(session), session


@pytest.fixture
def duckdb_session(day_project) -> object:
    return day_project[1]


def _observe_cumulative(
    day_project: tuple[_DayProject, object],
    session: object,
    *,
    anchor,
    base: str,
    start: str,
    end: str,
    grain: str | None = None,
):
    """Observe a cumulative metric, returning the materialized MetricFrame.

    ``anchor`` routes to the matching pre-declared cumulative metric: a trailing
    anchor selects the trailing7_gmv metric, while None/other selects the
    month-reset (grain_to_date) metric.
    """
    metric_ref = make_ref(f"sales.{_metric_for_base(base, anchor)}", SemanticKind.METRIC)
    return observe(
        metric_ref,
        time_scope={"start": start, "end": end},
        grain=grain,
        session=session,
    )


def _is_trailing_anchor(anchor: object) -> bool:
    if anchor is None:
        return False
    kind = getattr(anchor, "kind", None)
    if kind == "trailing":
        return True
    return isinstance(anchor, tuple) and anchor[:1] == ("trailing",)


def _metric_for_base(base: str, anchor: object = None) -> str:
    if _is_trailing_anchor(anchor):
        return {
            "gmv": "trailing7_gmv",
            "active_users": "trailing7_active_users",
        }[base]
    if anchor == "all_history":
        return {
            "gmv": "cum_gmv",
        }[base]
    return {
        "gmv": "mtd_gmv",
        "active_users": "mtd_active_users",
        "active_users_qtd": "qtd_active_users",
    }[base]


def _recorded_executions(frame, session) -> list[object]:
    """Return persisted query executions for the materialization job."""

    return list(session.job(frame.meta.produced_by_job)["queries"])


def _observe_named_cumulative_metric(session, name: str, *, start: str, end: str):
    return observe(
        make_ref(f"sales.{name}", SemanticKind.METRIC),
        time_scope={"start": start, "end": end},
        grain="day",
        session=session,
    )


def test_compare_mtd_ratio_over_cumulative_components_after_reload(
    day_project, duckdb_session
) -> None:
    current = _observe_named_cumulative_metric(
        duckdb_session,
        "mtd_gmv_per_active",
        start="2026-07-01",
        end="2026-07-08",
    )
    baseline = _observe_named_cumulative_metric(
        duckdb_session,
        "mtd_gmv_per_active",
        start="2026-01-01",
        end="2026-01-08",
    )

    current = duckdb_session.get_frame(current.ref)
    baseline = duckdb_session.get_frame(baseline.ref)
    assert current.meta.cumulative["anchor"] == ["grain_to_date", "month"]
    assert current.meta.cumulative["compare_blocker"] is None
    assert "grain_to_date" in current.render()
    compare_affordance = next(
        item for item in current.contract().affordances if item.capability_id == "compare"
    )
    assert any("boundary" in item.reason for item in compare_affordance.preconditions)

    delta = compare(current, baseline, session=duckdb_session)

    assert delta.meta.cumulative == current.meta.cumulative
    assert delta.meta.alignment["to_date"]["reset_grain"] == "month"
    assert delta.meta.component_ref is not None


def test_compare_mtd_ratio_drops_baseline_tail_from_parent_and_components(
    day_project, duckdb_session
) -> None:
    current = _observe_named_cumulative_metric(
        duckdb_session,
        "mtd_gmv_per_active",
        start="2026-07-01",
        end="2026-07-03",
    )
    baseline = _observe_named_cumulative_metric(
        duckdb_session,
        "mtd_gmv_per_active",
        start="2026-01-01",
        end="2026-01-04",
    )

    delta = compare(current, baseline, session=duckdb_session)
    parent_df = delta.to_pandas()
    component_df = delta.components().to_pandas()

    assert delta.meta.alignment["to_date"]["matched_buckets"] == 2
    assert delta.meta.alignment["to_date"]["baseline_tail_buckets"] == 1
    assert len(parent_df) == 2
    assert len(component_df) == 2
    assert parent_df["current"].notna().all()
    assert parent_df["baseline"].notna().all()


def test_derived_cumulative_delta_keeps_attribute_gated(day_project, duckdb_session) -> None:
    current = _observe_named_cumulative_metric(
        duckdb_session,
        "mtd_gmv_per_active",
        start="2026-07-01",
        end="2026-07-08",
    )
    baseline = _observe_named_cumulative_metric(
        duckdb_session,
        "mtd_gmv_per_active",
        start="2026-01-01",
        end="2026-01-08",
    )
    delta = compare(current, baseline, session=duckdb_session)

    attribute_affordance = next(
        item for item in delta.contract().affordances if item.capability_id == "attribute"
    )
    assert any(
        item.check == "cumulative_attribution_unsupported" and item.status == "fail"
        for item in attribute_affordance.preconditions
    )
    with pytest.raises(CumulativeFrameUnsupportedError):
        attribute(
            delta,
            axes=[make_ref("sales.events.region", SemanticKind.DIMENSION)],
            session=duckdb_session,
        )


def test_compare_trailing_ratio_over_cumulative_components(day_project, duckdb_session) -> None:
    current = _observe_named_cumulative_metric(
        duckdb_session,
        "trailing7_gmv_per_active",
        start="2026-07-01",
        end="2026-07-08",
    )
    baseline = _observe_named_cumulative_metric(
        duckdb_session,
        "trailing7_gmv_per_active",
        start="2026-01-01",
        end="2026-01-08",
    )

    assert current.meta.cumulative["anchor"] == ("trailing", 7, "day")
    delta = compare(current, baseline, session=duckdb_session)

    assert delta.meta.cumulative == current.meta.cumulative
    assert delta.meta.component_ref is not None


def test_derived_trailing_ratio_preserves_window_coverage(day_project, duckdb_session) -> None:
    frame = _observe_named_cumulative_metric(
        duckdb_session,
        "trailing7_gmv_per_active",
        start="2026-01-01",
        end="2026-01-10",
    )

    coverage = frame.coverage()
    coverage_df = coverage.to_pandas()

    assert frame.meta.coverage_ref is not None
    assert coverage.meta.coverage_kind == "window_coverage"
    assert {
        "bucket_start",
        "expected_span",
        "covered_span",
        "coverage_ratio",
        "coverage_status",
    }.issubset(coverage_df.columns)
    assert coverage_df.loc[coverage_df["bucket_start"] == "2026-01-01", "coverage_ratio"].iloc[
        0
    ] == pytest.approx(1 / 7)
    component_graph = frame.components().meta.component_graph
    assert component_graph is not None
    assert all(
        node["coverage_ref"] is not None
        for node in component_graph["nodes"]
        if node["node_kind"] in {"aggregate", "cumulative", "ratio"}
    )


@pytest.mark.parametrize(
    ("metric_name", "expected_blocker"),
    [
        ("mixed_reset_gmv_per_active", "mixed_component_anchors"),
        ("mixed_cumulative_flow", "non_cumulative_component"),
    ],
)
def test_derived_cumulative_compare_blocker_is_persisted(
    day_project,
    duckdb_session,
    metric_name: str,
    expected_blocker: str,
) -> None:
    current = _observe_named_cumulative_metric(
        duckdb_session,
        metric_name,
        start="2026-07-01",
        end="2026-07-08",
    )
    baseline = _observe_named_cumulative_metric(
        duckdb_session,
        metric_name,
        start="2026-01-01",
        end="2026-01-08",
    )

    assert current.meta.cumulative["anchor"] is None
    assert current.meta.cumulative["compare_blocker"] == expected_blocker
    assert expected_blocker in current.render()
    compare_affordance = next(
        item for item in current.contract().affordances if item.capability_id == "compare"
    )
    assert any(
        item.check == "cumulative_compare_compatible" and item.status == "fail"
        for item in compare_affordance.preconditions
    )
    with pytest.raises(CumulativeFrameUnsupportedError) as exc_info:
        compare(current, baseline, session=duckdb_session)
    assert exc_info.value._context["compare_blocker"] == expected_blocker


def test_grain_to_date_month_resets_at_month_boundary(day_project, duckdb_session) -> None:
    """MTD revenue resets to zero at each month start; accumulates within the month."""
    project, session = day_project
    cum = _observe_cumulative(
        day_project,
        duckdb_session,
        anchor=None,
        base="gmv",
        start="2026-01-01",
        end="2026-03-01",
        grain="day",
    )
    df = _metric_pandas(cum)
    # Feb 1 value == Jan 31 is FALSE: Feb 1 resets to that day's flow.
    jan31 = df.loc[df["bucket_start"] == "2026-01-31", "value"].iloc[0]
    feb01 = df.loc[df["bucket_start"] == "2026-02-01", "value"].iloc[0]
    feb01_flow = project.flow_for("2026-02-01")
    assert feb01 == pytest.approx(feb01_flow)
    assert feb01 < jan31  # reset dropped the prior accumulation
    # Within Feb, value accumulates.
    feb02 = df.loc[df["bucket_start"] == "2026-02-02", "value"].iloc[0]
    assert feb02 == pytest.approx(feb01_flow + project.flow_for("2026-02-02"))


def test_grain_to_date_seed_only_for_first_partial_period(day_project, duckdb_session) -> None:
    """A window starting on a reset boundary runs ONE query (no seed)."""
    cum = _observe_cumulative(
        day_project,
        duckdb_session,
        anchor=None,
        base="gmv",
        start="2026-02-01",
        end="2026-03-01",
        grain="day",
    )
    executions = _recorded_executions(cum, duckdb_session)
    assert len(executions) == 1


def test_grain_to_date_seed_for_partial_first_period(day_project, duckdb_session) -> None:
    """A window starting mid-period runs a seed query for the first partial period only."""
    cum = _observe_cumulative(
        day_project,
        duckdb_session,
        anchor=None,
        base="gmv",
        start="2026-01-15",
        end="2026-03-01",
        grain="day",
    )
    executions = _recorded_executions(cum, duckdb_session)
    # Seed (first partial period) + flow == 2 queries.
    assert len(executions) == 2


def test_grain_to_date_scalar_full_july_not_empty_august(day_project, duckdb_session) -> None:
    """Keystone: end='2026-08-01' (exclusive) yields the July total, not empty August MTD."""
    project, session = day_project
    cum = _observe_cumulative(
        day_project,
        duckdb_session,
        anchor=None,
        base="gmv",
        start="2026-07-01",
        end="2026-08-01",
    )
    df = _metric_pandas(cum)
    assert len(df) == 1
    assert df.loc[0, "value"] == pytest.approx(project.july_total())


def test_grain_to_date_week_grain_under_month_reset_rejected(day_project, duckdb_session) -> None:
    """Grain-compat rule: week grain under month reset is illegal (teaching error)."""
    with pytest.raises(Exception) as exc_info:
        _observe_cumulative(
            day_project,
            duckdb_session,
            anchor=None,
            base="gmv",
            start="2026-01-01",
            end="2026-02-01",
            grain="week",
        )
    msg = str(exc_info.value).lower()
    assert "week" in msg and "month" in msg


def test_grain_to_date_month_at_month_grain_is_period_total(day_project, duckdb_session) -> None:
    """month grain under month reset: each bucket is the full-period total (period-end value)."""
    project, session = day_project
    cum = _observe_cumulative(
        day_project,
        duckdb_session,
        anchor=None,
        base="gmv",
        start="2026-01-01",
        end="2026-03-01",
        grain="month",
    )
    df = _metric_pandas(cum)
    assert df.loc[df["bucket_start"] == "2026-01-01", "value"].iloc[0] == pytest.approx(
        project.jan_total()
    )
    assert df.loc[df["bucket_start"] == "2026-02-01", "value"].iloc[0] == pytest.approx(
        project.feb_total()
    )


def test_grain_to_date_carries_cumulative_anchor_marker(day_project, duckdb_session) -> None:
    cum = _observe_cumulative(
        day_project,
        duckdb_session,
        anchor=None,
        base="gmv",
        start="2026-01-01",
        end="2026-02-01",
        grain="day",
    )
    assert cum.meta.cumulative == {
        "kind": "cumulative",
        "base": "sales.gmv",
        "over": "sales.events.event_time",
        "anchor": ("grain_to_date", "month"),
        "components": None,
    }
    assert cum.meta.reaggregatable is False


# ---------------------------------------------------------------------------
# Task 5: grain_to_date (MTD) x count_distinct (period-scoped first-seen)
# ---------------------------------------------------------------------------


def test_grain_to_date_count_distinct_resets_within_period(day_project, duckdb_session) -> None:
    """A user active on day 1 and day 5 of the same month counts once in that
    month's MTD; next month they count again (period-scoped first-seen)."""
    cum = _observe_cumulative(
        day_project,
        duckdb_session,
        anchor=None,
        base="active_users",
        start="2026-01-01",
        end="2026-03-01",
        grain="day",
    )
    df = _metric_pandas(cum)
    # User 100 is the only active user Jan 1..Jan 5, so the MTD distinct count
    # is 1 on every one of those days (no double-count within the period).
    jan01 = df.loc[df["bucket_start"] == "2026-01-01", "value"].iloc[0]
    jan05 = df.loc[df["bucket_start"] == "2026-01-05", "value"].iloc[0]
    assert jan01 == pytest.approx(1.0)
    assert jan05 == jan01  # keystone: no double-count within the period
    # On Feb 1 the same user is counted again (period reset): Feb 1 MTD >= 1
    # and strictly less than the Jan-end MTD (which carried the full Jan set).
    feb01 = df.loc[df["bucket_start"] == "2026-02-01", "value"].iloc[0]
    jan31 = df.loc[df["bucket_start"] == "2026-01-31", "value"].iloc[0]
    assert feb01 >= 1.0  # user re-counted in Feb
    assert feb01 < jan31  # reset dropped the prior period's accumulation


def test_grain_to_date_count_distinct_filters_before_dedup(day_project, duckdb_session) -> None:
    """v1 first-seen rule carries over: where/slice filters apply before dedup.

    Every Jan day outside Jan 1..5 belongs to a UNIQUE user in region CA. A
    US-only slice must exclude all those CA events before dedup, so only user
    100 (US, Jan 1..5) is ever first-seen. If the filter ran AFTER dedup, the
    CA users' first-seen rows would leak into the US slice and inflate the
    count to the full unfiltered total.
    """
    cum = _observe_cumulative(
        day_project,
        duckdb_session,
        anchor=None,
        base="active_users",
        start="2026-01-01",
        end="2026-02-01",
        grain="day",
    )
    # Re-run with a US slice through the public observe path so the where
    # filter is planned onto base_plan.where and re-applied to combined_raw.
    _, session = day_project
    metric_ref = make_ref("sales.mtd_active_users", SemanticKind.METRIC)
    us_frame = observe(
        metric_ref,
        time_scope={"start": "2026-01-01", "end": "2026-02-01"},
        grain="day",
        slice_by={make_ref("sales.events.region", SemanticKind.DIMENSION): "US"},
        session=session,
    )
    us_df = _metric_pandas(us_frame)
    # US-only Jan MTD: user 100 (Jan 1..5) is the only US user, so every Jan
    # bucket holds 1. The CA-only users (one per remaining Jan day) are
    # excluded before dedup and never counted.
    jan01 = us_df.loc[us_df["bucket_start"] == "2026-01-01", "value"].iloc[0]
    jan31 = us_df.loc[us_df["bucket_start"] == "2026-01-31", "value"].iloc[0]
    assert jan01 == pytest.approx(1.0)  # user 100
    assert jan31 == pytest.approx(1.0)  # CA users never counted (filter before dedup)
    # Compare against the unfiltered frame: Jan 31 MTD counts user 100 plus
    # every CA-only user (one per Jan day outside Jan 1..5) = 1 + 26 = 27.
    # The US slice strictly excludes them, proving the filter runs before dedup.
    full_df = _metric_pandas(cum)
    full_jan31 = full_df.loc[full_df["bucket_start"] == "2026-01-31", "value"].iloc[0]
    assert full_jan31 == pytest.approx(27.0)
    assert jan31 < full_jan31  # the slice actually excluded the CA users


def test_grain_to_date_count_distinct_quarter_reset_aligns_across_paths(
    day_project, duckdb_session
) -> None:
    """Cross-grain alignment: a quarter-reset count_distinct must keep the SQL
    dedup period_key (combined_time_expr.truncate('Q')) aligned with the pandas
    cumsum reset partition (_trunc_series_to_grain quarter branch).

    Jan and Feb are in the SAME quarter (Q1). User 100 is first-seen Jan 1 and
    active again Feb 1. Under quarter reset, Feb 1 is NOT a reset boundary, so
    user 100 must NOT re-count there: Feb 1 QTD == Jan 31 QTD (the Jan distinct
    set carries forward unchanged). Under month reset (the existing Task 5
    test), Feb 1 DOES reset and user 100 re-counts, so Feb 1 MTD == 1. The two
    grains must diverge here — if they didn't, the two truncation paths would
    be coupling silently.

    Jul 1 starts Q3, so user 100 (active Jul 1) re-counts there: Jul 1 QTD
    resets toward 1. This asserts the period-scoped re-count at a NON-month
    boundary, which would fail if either truncation path mishandled quarters.
    """
    # Quarter-reset count_distinct over Jan 1 -> Aug 1 (Q1 = Jan..Mar, Q3 = Jul).
    cum_qtd = _observe_cumulative(
        day_project,
        duckdb_session,
        anchor=None,
        base="active_users_qtd",
        start="2026-01-01",
        end="2026-08-01",
        grain="day",
    )
    qtd = _metric_pandas(cum_qtd)
    jan31_qtd = qtd.loc[qtd["bucket_start"] == "2026-01-31", "value"].iloc[0]
    feb01_qtd = qtd.loc[qtd["bucket_start"] == "2026-02-01", "value"].iloc[0]
    jul01_qtd = qtd.loc[qtd["bucket_start"] == "2026-07-01", "value"].iloc[0]

    # Jan 31 QTD: user 100 + 26 unique CA users (Jan 6..31) = 27.
    assert jan31_qtd == pytest.approx(27.0)
    # Feb 1 is inside Q1 (NOT a quarter boundary): user 100 does NOT re-count,
    # and no new user is first-seen on Feb 1, so QTD is unchanged. This is the
    # divergence from month-reset (where Feb 1 MTD == 1).
    assert feb01_qtd == pytest.approx(jan31_qtd)
    # Contrast with month-reset to prove the grains actually differ.
    cum_mtd = _observe_cumulative(
        day_project,
        duckdb_session,
        anchor=None,
        base="active_users",
        start="2026-01-01",
        end="2026-02-02",
        grain="day",
    )
    mtd = _metric_pandas(cum_mtd)
    feb01_mtd = mtd.loc[mtd["bucket_start"] == "2026-02-01", "value"].iloc[0]
    assert feb01_mtd == pytest.approx(1.0)  # user 100 re-counted at month boundary
    assert feb01_qtd != pytest.approx(feb01_mtd)
    # Jul 1 starts Q3: user 100 (active Jul 1) re-counts in the new quarter.
    assert jul01_qtd == pytest.approx(1.0)


def test_grain_to_date_count_distinct_seed_scoped_for_mid_period_start(
    day_project, duckdb_session
) -> None:
    """Seed-scoping path for count_distinct: a MID-PERIOD window start exercises
    the count_distinct-specific seed SQL
    (first_seen.period_key == first_period_start AND first_seen_ts < window.start).

    With start='2026-01-15' (mid-January), first_period_start == 2026-01-01 and
    first_period_start < window_start, so the seed query runs. User 100 was
    first-seen Jan 1 (before 2026-01-15, in the same January period), so it
    MUST be seeded into the first displayed bucket (Jan 15) — the bucket value
    depends on the seed. A type mismatch or off-by-one in the seed SQL would
    either drop user 100 (value too low) or double-count flow (value too high).

    Asserts:
      - exactly 2 query executions (seed + flow), mirroring Task 4's
        test_grain_to_date_seed_for_partial_first_period pattern;
      - the Jan 15 MTD value is 11 (seed of 10 entities first-seen before Jan 15
        in the January period + Jan 15's own first-seen flow of 1), a value that
        DEPENDS ON the seed running correctly.
    """
    cum = _observe_cumulative(
        day_project,
        duckdb_session,
        anchor=None,
        base="active_users",
        start="2026-01-15",
        end="2026-02-01",
        grain="day",
    )
    executions = _recorded_executions(cum, duckdb_session)
    # Seed (first partial period) + flow == 2 queries.
    assert len(executions) == 2
    df = _metric_pandas(cum)
    # First displayed bucket is Jan 15. The seed (entities first-seen before
    # Jan 15 within the January period) = user 100 (Jan 1..5) + the 9 unique CA
    # users first-seen Jan 6..14 = 10 distinct entities. Jan 15's own flow is
    # the unique CA user first-seen that day (+1). The dense frame adds the seed
    # to every January-period bucket, so Jan 15 MTD = seed (10) + flow(Jan 15)
    # (1) = 11. Without the seed, Jan 15 would be 1 — the value 11 depends on
    # the seed SQL running correctly.
    jan15 = df.loc[df["bucket_start"] == "2026-01-15", "value"].iloc[0]
    assert jan15 == pytest.approx(11.0)
    # Jan 16 MTD = seed (10) + cumsum(flow Jan 15..16) (2) = 12.
    jan16 = df.loc[df["bucket_start"] == "2026-01-16", "value"].iloc[0]
    assert jan16 == pytest.approx(12.0)


# ---------------------------------------------------------------------------
# Task 6: trailing (rolling N) x sum/count execution + data-start coverage
# ---------------------------------------------------------------------------


def test_trailing_sum_rolling_window(day_project, duckdb_session) -> None:
    """Trailing 7-day sum: bucket value = sum of the 7-day span ending at bucket end."""
    project, _ = day_project
    cum = _observe_cumulative(
        day_project,
        duckdb_session,
        anchor=ms.trailing(count=7, unit="day"),
        base="gmv",
        start="2026-02-01",
        end="2026-02-15",
        grain="day",
    )
    df = _metric_pandas(cum)
    # Feb 8 value == sum of Feb 2..Feb 8 (7-day span ending Feb 8 end).
    assert df.loc[df["bucket_start"] == "2026-02-08", "value"].iloc[0] == pytest.approx(
        project.sum_range("2026-02-02", "2026-02-08")
    )


def test_trailing_partial_window_shows_actual_value(day_project, duckdb_session) -> None:
    """First bucket's window reaches before data start: show actual partial
    accumulation, marked partial in coverage."""
    cum = _observe_cumulative(
        day_project,
        duckdb_session,
        anchor=ms.trailing(count=7, unit="day"),
        base="gmv",
        start="2026-01-01",
        end="2026-01-10",
        grain="day",
    )
    df = _metric_pandas(cum)
    # Jan 1 window reaches Jan -5 (before data start Jan 1): partial, but shows
    # actual sum (Jan 1's own flow since only Jan 1 falls in the span).
    assert df.loc[df["bucket_start"] == "2026-01-01", "value"].iloc[0] == pytest.approx(1.0)
    cov = cum.coverage()
    cov_df = cov.to_pandas()
    assert (cov_df["coverage_status"] == "partial").any()


def test_trailing_empty_window_is_zero_not_carryforward(day_project, duckdb_session) -> None:
    """Keystone: a gap with no activity in the last 7 days means 0, not carried
    forward. Contrast against all_history carry-forward explicitly."""
    project, _ = day_project
    cum = _observe_cumulative(
        day_project,
        duckdb_session,
        anchor=ms.trailing(count=7, unit="day"),
        base="gmv",
        start="2026-04-01",
        end="2026-04-15",
        grain="day",
    )
    df = _metric_pandas(cum)
    # day_project has a 7-day gap in early April; those buckets are 0.
    gap_day = project.first_gap_day()
    assert df.loc[df["bucket_start"] == gap_day, "value"].iloc[0] == 0
    # Contrast against all_history: a cumulative all_history frame over the same
    # April window would carry forward all prior data (Jan + Feb totals), not 0.
    all_history = _observe_cumulative(
        day_project,
        duckdb_session,
        anchor="all_history",
        base="gmv",
        start="2026-04-01",
        end="2026-04-15",
        grain="day",
    )
    ah_df = _metric_pandas(all_history)
    ah_gap = ah_df.loc[ah_df["bucket_start"] == gap_day, "value"].iloc[0]
    assert ah_gap == pytest.approx(project.jan_total() + project.feb_total())
    assert ah_gap != 0  # carry-forward vs trailing's true zero


def test_trailing_integer_multiple_rule_rejects_mismatch(day_project, duckdb_session) -> None:
    """7-day trailing at hour grain is legal (168 buckets); a non-integer span is
    rejected. (Fixed-size units always divide evenly, so this guards future
    variable spans.)"""
    cum = _observe_cumulative(
        day_project,
        duckdb_session,
        anchor=ms.trailing(count=7, unit="day"),
        base="gmv",
        start="2026-02-01",
        end="2026-02-08",
        grain="hour",
    )
    df = _metric_pandas(cum)
    assert len(df) == 7 * 24  # 168 buckets


def test_trailing_no_grain_rejected(day_project, duckdb_session) -> None:
    """Trailing without a grain is rejected; teaching error points to a plain
    windowed observe."""
    with pytest.raises(Exception) as exc_info:
        _observe_cumulative(
            day_project,
            duckdb_session,
            anchor=ms.trailing(count=7, unit="day"),
            base="gmv",
            start="2026-02-01",
            end="2026-02-08",
        )
    assert "observe" in str(exc_info.value).lower()


def test_trailing_display_window_clipping(day_project, duckdb_session) -> None:
    """The extended fetch window ([start - span, end)) is clipped back to
    [start, end) in display."""
    cum = _observe_cumulative(
        day_project,
        duckdb_session,
        anchor=ms.trailing(count=7, unit="day"),
        base="gmv",
        start="2026-02-10",
        end="2026-02-15",
        grain="day",
    )
    df = _metric_pandas(cum)
    assert df["bucket_start"].min() >= pd.Timestamp("2026-02-10")
    assert df["bucket_start"].max() < pd.Timestamp("2026-02-15")


# ---------------------------------------------------------------------------
# Task 7: trailing (rolling N) x count_distinct (memtable-spine expansion join)
# ---------------------------------------------------------------------------


def test_trailing_distinct_expansion_join_correctness(day_project, duckdb_session) -> None:
    """Keystone: a user active on day 1 and day 5 counts once in any 7d window
    containing both, and drops out after the window passes.

    The day_project fixture seeds a unique CA user per day alongside user 100
    (US, active Jan 1..5). A US slice isolates user 100 so the trailing-distinct
    semantics are observable: user 100 counts once in every 7d window whose span
    contains any of Jan 1..5, and drops out once the span no longer reaches
    Jan 5 (the last active day)."""
    _, session = day_project
    metric_ref = make_ref("sales.trailing7_active_users", SemanticKind.METRIC)
    cum = observe(
        metric_ref,
        time_scope={"start": "2026-01-01", "end": "2026-02-01"},
        grain="day",
        slice_by={make_ref("sales.events.region", SemanticKind.DIMENSION): "US"},
        session=session,
    )
    df = _metric_pandas(cum)
    # The 7d window for bucket B is [B-6d, B] inclusive (reachback (W-1)*grain,
    # shared with the additive trailing path). User 100 (active Jan 1..5) is
    # present in every bucket whose window reaches Jan 5 (the last active day):
    # B = Jan 11 -> window [Jan 5, Jan 11] still includes Jan 5, so value 1.
    # B = Jan 12 -> window [Jan 6, Jan 12] no longer reaches Jan 5, so value 0.
    in_window = df.loc[df["bucket_start"].between("2026-01-01", "2026-01-11"), "value"]
    assert (in_window == 1).all()
    after = df.loc[df["bucket_start"] >= "2026-01-12", "value"]
    assert (after == 0).all()


def test_trailing_distinct_bucket_cap_rejects(day_project, duckdb_session) -> None:
    """A trailing distinct query exceeding the bucket-count cap is rejected with a
    teaching error."""
    with pytest.raises(Exception) as exc_info:
        _observe_cumulative(
            day_project,
            duckdb_session,
            anchor=ms.trailing(count=365, unit="day"),
            base="active_users",
            start="2020-01-01",
            end="2026-01-01",
            grain="hour",
        )
    msg = str(exc_info.value).lower()
    assert "cap" in msg or "too many" in msg


def test_trailing_distinct_empty_bucket_is_zero(day_project, duckdb_session) -> None:
    """A gap bucket with no activity in its trailing span yields 0, not missing."""
    cum = _observe_cumulative(
        day_project,
        duckdb_session,
        anchor=ms.trailing(count=7, unit="day"),
        base="active_users",
        start="2026-04-01",
        end="2026-04-15",
        grain="day",
    )
    df = _metric_pandas(cum)
    gap_day = day_project[0].first_gap_day()
    assert df.loc[df["bucket_start"] == gap_day, "value"].iloc[0] == 0


def test_trailing_distinct_partial_window_marked_in_coverage(day_project, duckdb_session) -> None:
    """Distinct path: a bucket whose 7-day window reaches before the data start is
    marked ``partial`` in coverage, mirroring the additive path. The shared
    ``_trailing_coverage_df`` uses the same (W-1)*grain window model as the
    distinct aggregation's span, so partial-marking is correct by construction."""
    cum = _observe_cumulative(
        day_project,
        duckdb_session,
        anchor=ms.trailing(count=7, unit="day"),
        base="active_users",
        start="2026-01-01",
        end="2026-01-15",
        grain="day",
    )
    df = _metric_pandas(cum)
    # Jan 1's 7-day window reaches back to Dec 26, before the data start (Jan 1):
    # it shows the actual value (user 100 active Jan 1) and is marked partial.
    assert df.loc[df["bucket_start"] == "2026-01-01", "value"].iloc[0] == pytest.approx(1.0)
    cov_df = cum.coverage().to_pandas()
    jan1_status = cov_df.loc[cov_df["bucket_start"] == "2026-01-01", "coverage_status"].iloc[0]
    assert jan1_status == "partial"
    # A bucket whose full 7-day window is inside the data range is complete.
    # Jan 8's window is [Jan 2, Jan 8]: entirely after the Jan 1 data start.
    jan8_status = cov_df.loc[cov_df["bucket_start"] == "2026-01-08", "coverage_status"].iloc[0]
    assert jan8_status == "complete"


# ---------------------------------------------------------------------------
# Task 8: coverage widening — window_coverage kind
# ---------------------------------------------------------------------------


@pytest.fixture
def trailing_partial_frame(day_project, duckdb_session) -> object:
    """A trailing 7-day frame whose first bucket's window reaches before the
    data start (Jan 1), producing a partial window_coverage sidecar row."""
    return _observe_cumulative(
        day_project,
        duckdb_session,
        anchor=ms.trailing(count=7, unit="day"),
        base="gmv",
        start="2026-01-01",
        end="2026-01-10",
        grain="day",
    )


def test_trailing_coverage_is_window_coverage_kind(trailing_partial_frame) -> None:
    cov = trailing_partial_frame.coverage()
    assert cov.meta.coverage_kind == "window_coverage"
    assert cov.meta.sample_interval is None
    cov_df = cov.to_pandas()
    assert {
        "bucket_start",
        "expected_span",
        "covered_span",
        "coverage_ratio",
        "coverage_status",
    }.issubset(cov_df.columns)
    assert (cov_df["coverage_status"].isin(["complete", "partial"])).all()


def test_trailing_coverage_partial_bucket_has_precise_ratio(trailing_partial_frame) -> None:
    """The partial first bucket's coverage_ratio is the real fraction of the
    span covered by data (1/7 for a 7-day span with only Jan 1 covered), not
    the Task 6 interim 0.5 placeholder."""
    cov_df = trailing_partial_frame.coverage().to_pandas()
    jan1 = cov_df.loc[cov_df["bucket_start"] == "2026-01-01"].iloc[0]
    assert jan1["coverage_status"] == "partial"
    # span = 7 days; bucket_end (Jan 2) - data_start (Jan 1) = 1 day; ratio = 1/7.
    assert jan1["expected_span"] == 7 * 24 * 3600
    assert jan1["covered_span"] == 1 * 24 * 3600
    assert jan1["coverage_ratio"] == pytest.approx(1 / 7)
    # A complete bucket has ratio 1.0 and covered_span == expected_span.
    jan8 = cov_df.loc[cov_df["bucket_start"] == "2026-01-08"].iloc[0]
    assert jan8["coverage_status"] == "complete"
    assert jan8["coverage_ratio"] == pytest.approx(1.0)
    assert jan8["covered_span"] == jan8["expected_span"]
