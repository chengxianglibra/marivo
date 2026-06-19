"""observe dynamic-grain threading tests."""

import ibis
import pytest

import marivo.analysis.session as session_attach
from marivo.analysis.errors import GrainUnsupportedError
from marivo.analysis.intents.observe import observe
from marivo.semantic.catalog import SemanticKind
from marivo.semantic.refs import make_ref


@pytest.fixture(autouse=True)
def _chdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TZ", "UTC")
    session_attach._reset_process_state()
    yield


def _bootstrap_events(tmp_path):
    semantic_dir = tmp_path / "models" / "semantic" / "ops"
    semantic_dir.mkdir(parents=True)
    datasource_dir = tmp_path / "models" / "datasources"
    datasource_dir.mkdir(parents=True, exist_ok=True)
    (datasource_dir / "warehouse.py").write_text(
        "import marivo.datasource as md\nmd.duckdb(name='warehouse', path=':memory:')\n"
    )
    (semantic_dir / "__init__.py").write_text("")
    (semantic_dir / "_domain.py").write_text(
        "import marivo.semantic as ms\nms.domain(name='ops')\n"
    )
    (semantic_dir / "datasets.py").write_text(
        "import marivo.semantic as ms\n"
        "events = ms.entity(name='events', datasource='warehouse', source=ms.table('events'))\n"
        "@ms.time_dimension(entity=events, granularity='minute', parse=ms.timestamp(timezone='UTC'))\n"
        "def ts(events):\n"
        "    return events.ts\n"
        "@ms.metric(entities=[events], additivity='additive', name='hits', )\n"
        "def hits(events):\n"
        "    return events.n.sum()\n"
    )


def _seed_events(con):
    con.raw_sql("CREATE TABLE events (ts TIMESTAMP, n DOUBLE)")
    con.raw_sql(
        "INSERT INTO events VALUES "
        "(TIMESTAMP '2026-06-03 00:07:30', 1.0),"
        "(TIMESTAMP '2026-06-03 00:12:00', 2.0),"
        "(TIMESTAMP '2026-06-03 00:18:00', 4.0)"
    )


def test_observe_five_minute_grain(tmp_path):
    _bootstrap_events(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed_events(con)
    s = session_attach.get_or_create(name="demo", backends={"warehouse": lambda: con})

    frame = observe(
        make_ref("ops.hits", SemanticKind.METRIC),
        timescope={"start": "2026-06-03 00:00:00", "end": "2026-06-03 01:00:00"},
        grain=(5, "minute"),
        session=s,
    )

    assert frame.meta.semantic_kind == "time_series"
    assert frame.meta.axes["time"]["grain"] == "5minute"
    df = frame.to_pandas()
    mapping = {str(b): v for b, v in zip(df["bucket_start"], df["value"], strict=True)}
    assert mapping["2026-06-03 00:05:00"] == 1.0
    assert mapping["2026-06-03 00:10:00"] == 2.0
    assert mapping["2026-06-03 00:15:00"] == 4.0


def test_observe_grain_finer_than_base_rejected(tmp_path):
    _bootstrap_events(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed_events(con)
    s = session_attach.get_or_create(name="demo", backends={"warehouse": lambda: con})

    with pytest.raises(GrainUnsupportedError):
        observe(
            make_ref("ops.hits", SemanticKind.METRIC),
            timescope={"start": "2026-06-03 00:00:00", "end": "2026-06-03 01:00:00"},
            grain=(30, "second"),
            session=s,
        )


def test_resolved_window_and_promotion_store_grain_token(tmp_path):
    """Regression guard: resolved grain must be stored as a token string
    (e.g. "5minute"), not as a Grain repr / object, in job params."""
    _bootstrap_events(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed_events(con)
    s = session_attach.get_or_create(name="demo", backends={"warehouse": lambda: con})

    observe(
        make_ref("ops.hits", SemanticKind.METRIC),
        timescope={"start": "2026-06-03 00:00:00", "end": "2026-06-03 01:00:00"},
        grain=(5, "minute"),
        session=s,
    )

    job = s.job(s.jobs()[0].id)
    assert job["params"]["timescope"]["resolved"]["grain"] == "5minute"
