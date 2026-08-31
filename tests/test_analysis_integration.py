"""End-to-end: semantic loader + analysis session + observe/compare/load."""

from datetime import datetime, timedelta

import ibis
import pytest

import marivo.analysis as mv
import marivo.analysis.session as session_attach
from marivo.semantic.catalog import SemanticKind
from tests.conftest import bootstrap_sales_project
from tests.ref_helpers import make_ref


@pytest.fixture(autouse=True)
def _chdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TZ", "UTC")
    session_attach._reset_process_state()
    yield


def _seed_warehouse():
    con = ibis.duckdb.connect(":memory:")
    con.raw_sql("CREATE TABLE orders (order_id INTEGER, created_at DATE, amount DOUBLE)")
    con.raw_sql(
        "INSERT INTO orders VALUES "
        "(1, DATE '2026-07-01', 10.0),"
        "(2, DATE '2026-07-15', 20.0),"
        "(3, DATE '2026-08-01', 30.0),"
        "(4, DATE '2026-04-10', 5.0),"
        "(5, DATE '2026-05-20', 15.0)"
    )
    return con


def test_end_to_end_sales_observe_compare_load(tmp_path):
    bootstrap_sales_project(tmp_path)
    con = _seed_warehouse()
    s = mv.session.get_or_create(
        name="qoq-investigation",
        question="Why did Q3 revenue jump vs Q2?",
        backends={"warehouse": lambda: con},
    )
    assert not s.is_read_only

    q3 = s.observe(
        make_ref("sales.revenue", SemanticKind.METRIC),
        time_scope=mv.time_scope(start="2026-07-01", end="2026-09-30"),
    )
    q2 = s.observe(
        make_ref("sales.revenue", SemanticKind.METRIC),
        time_scope=mv.time_scope(start="2026-04-01", end="2026-06-30"),
    )
    d = s.compare(
        q3,
        q2,
        alignment=mv.window_bucket(),
    )
    df = d.to_pandas()
    assert df.iloc[0]["current"] == pytest.approx(60.0)
    assert df.iloc[0]["baseline"] == pytest.approx(20.0)
    assert df.iloc[0]["delta"] == pytest.approx(40.0)

    assert sorted(j.capability_id for j in s.runs(limit=100).items) == [
        "compare",
        "observe",
        "observe",
    ]
    assert {artifact.family for artifact in s.graph().artifacts} == {
        "MetricFrame",
        "DeltaFrame",
    }

    reloaded = s.artifact(q3.ref)
    assert reloaded.meta.metric_id == "sales.revenue"
    assert reloaded.meta.session_id == s.id

    session_attach._reset_process_state()
    s_ro = mv.session.get_or_create(name="qoq-investigation", use_datasources=False)
    assert s_ro.is_read_only
    q3_again = s_ro.artifact(q3.ref)
    q2_again = s_ro.artifact(q2.ref)
    d_again = s_ro.compare(
        q3_again,
        q2_again,
        alignment=mv.window_bucket(),
    )
    assert d_again.to_pandas().iloc[0]["delta"] == pytest.approx(40.0)


def _bootstrap_hour_partition_sales(tmp_path):
    semantic_dir = tmp_path / "models" / "semantic" / "sales"
    semantic_dir.mkdir(parents=True)
    (semantic_dir / "__init__.py").write_text("")
    (semantic_dir / "_domain.py").write_text(
        "import marivo.datasource as md\nimport marivo.semantic as ms\n"
        "ms.domain(name='sales', owner='Mina Zhang')\n"
    )
    datasource_dir = tmp_path / "models" / "datasources"
    datasource_dir.mkdir(parents=True, exist_ok=True)
    (datasource_dir / "warehouse.py").write_text(
        "import marivo.datasource as md\nmd.duckdb(name='warehouse', path=':memory:')\n"
    )
    (semantic_dir / "datasets.py").write_text(
        "import marivo.datasource as md\nimport marivo.semantic as ms\n"
        "\n"
        "orders = ms.entity(name='orders', datasource=ms.ref.datasource('warehouse'), source=md.table('orders'))\n"
        "\n"
        "@ms.time_dimension(entity=orders, granularity='hour', parse=ms.strptime('%Y%m%d%H'))\n"
        "def log_hour(orders):\n"
        "    return orders.log_hour\n"
        "\n"
        "@ms.metric(entities=[orders], additivity='additive', name='revenue')\n"
        "def revenue(orders):\n"
        "    return orders.amount.sum()\n"
    )


def test_hour_partition_observe_compare_window_bucket_pairs_all_84(tmp_path):
    _bootstrap_hour_partition_sales(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    con.raw_sql("CREATE TABLE orders (order_id INTEGER, log_hour VARCHAR, amount DOUBLE)")
    # 84 expected whole-hour buckets plus one row exactly at the end boundary,
    # which must be excluded by half-open [start, end).
    start = datetime(2024, 10, 11, 0, 0)
    rows = [f"({i}, '{(start + timedelta(hours=i)):%Y%m%d%H}', {float(i + 1)})" for i in range(85)]
    con.raw_sql("INSERT INTO orders VALUES " + ", ".join(rows))

    s = session_attach.get_or_create(name="demo", backends={"warehouse": lambda: con})

    scope = mv.time_scope(start="2024-10-11T00:00:00", end="2024-10-14T12:00:00")
    cur = s.observe(
        make_ref("sales.revenue", SemanticKind.METRIC),
        time_scope=scope,
        grain=mv.grain("hour"),
    )
    base = s.observe(
        make_ref("sales.revenue", SemanticKind.METRIC),
        time_scope=scope,
        grain=mv.grain("hour"),
    )

    # The exact-hour end bucket (12:00) must be excluded from both frames.
    assert len(cur.to_pandas()) == 84
    assert len(base.to_pandas()) == 84

    delta = s.compare(cur, base, alignment=mv.window_bucket())
    assert len(delta.to_pandas()) == 84
    coverage = delta.meta.alignment["coverage"]
    assert coverage["paired_buckets"] == 84
    assert coverage["current_unpaired_buckets"] == 0
    assert coverage["baseline_unpaired_buckets"] == 0
    assert coverage["current"]["missing_buckets"] == 0
    assert coverage["baseline"]["missing_buckets"] == 0


def test_hour_partition_observe_compare_window_bucket_pairs_non_hour_end(tmp_path):
    _bootstrap_hour_partition_sales(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    con.raw_sql("CREATE TABLE orders (order_id INTEGER, log_hour VARCHAR, amount DOUBLE)")
    # 85 whole-hour buckets: the intersecting 12:00 bucket of a non-integer
    # end (12:30) must be kept by both observe and the window_bucket
    # enumeration, so two identical scopes pair with zero missing buckets.
    start = datetime(2024, 10, 11, 0, 0)
    rows = [f"({i}, '{(start + timedelta(hours=i)):%Y%m%d%H}', {float(i + 1)})" for i in range(85)]
    con.raw_sql("INSERT INTO orders VALUES " + ", ".join(rows))

    s = session_attach.get_or_create(name="demo", backends={"warehouse": lambda: con})

    scope = mv.time_scope(start="2024-10-11T00:00:00", end="2024-10-14T12:30:00")
    cur = s.observe(
        make_ref("sales.revenue", SemanticKind.METRIC),
        time_scope=scope,
        grain=mv.grain("hour"),
    )
    base = s.observe(
        make_ref("sales.revenue", SemanticKind.METRIC),
        time_scope=scope,
        grain=mv.grain("hour"),
    )

    # The intersecting 12:00 bucket (12:00 < 12:30) is kept in both frames.
    assert len(cur.to_pandas()) == 85
    assert len(base.to_pandas()) == 85

    delta = s.compare(cur, base, alignment=mv.window_bucket())
    assert len(delta.to_pandas()) == 85
    coverage = delta.meta.alignment["coverage"]
    assert coverage["paired_buckets"] == 85
    assert coverage["current_unpaired_buckets"] == 0
    assert coverage["baseline_unpaired_buckets"] == 0
    assert coverage["current"]["missing_buckets"] == 0
    assert coverage["baseline"]["missing_buckets"] == 0


def test_end_to_end_discover_period_shifts_uses_canonical_delta(tmp_path):
    """A real observe -> compare -> discover chain: the compare DeltaFrame
    carries current/baseline/delta/pct_change, and discover.period_shifts with
    an omitted value defaults to the canonical ``delta`` column instead of
    failing the single-numeric-column sniff (issue #118)."""
    bootstrap_sales_project(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    con.raw_sql("CREATE TABLE orders (order_id INTEGER, created_at DATE, amount DOUBLE)")
    rows = []
    oid = 1
    for day in range(30):
        rows.append((oid, f"DATE '2026-04-{day + 1:02d}'", 10.0))
        oid += 1
    for day in range(30):
        amount = 100.0 if 10 <= day < 17 else 10.0
        rows.append((oid, f"DATE '2026-07-{day + 1:02d}'", amount))
        oid += 1
    values = ", ".join(f"({a}, {b}, {c})" for a, b, c in rows)
    con.raw_sql(f"INSERT INTO orders VALUES {values}")

    s = mv.session.get_or_create(
        name="discover-chain",
        question="why did Q3 spike?",
        backends={"warehouse": lambda: con},
    )
    q3 = s.observe(
        make_ref("sales.revenue", SemanticKind.METRIC),
        time_scope=mv.time_scope(start="2026-07-01", end="2026-07-30"),
        grain=mv.grain("day"),
    )
    q2 = s.observe(
        make_ref("sales.revenue", SemanticKind.METRIC),
        time_scope=mv.time_scope(start="2026-04-01", end="2026-04-30"),
        grain=mv.grain("day"),
    )
    d = s.compare(q3, q2, alignment=mv.window_bucket())

    delta_df = d.to_pandas()
    assert {"current", "baseline", "delta", "pct_change"}.issubset(set(delta_df.columns))

    out = s.discover.period_shifts(d, threshold=2.0)

    assert out.meta.params["value"] == "delta"
    assert len(out.to_pandas()) >= 1
