"""Frame value_semantics aggregation/additivity/reaggregatable contract (issue #110).

These tests pin the three distinct semantics fields on observe frames:

* ``aggregation``   — how the source rows were aggregated (``sum``, ``count``, ...);
* ``additivity``    — whether the result may be summed on a business axis;
* ``reaggregatable``— whether the materialized frame has a safe plain-sum rollup.

A non-additive or unknown-additivity metric must never be published as
``reaggregatable=yes``, because the v1 rollup contract sums value columns.
"""

from __future__ import annotations

import json

import ibis
import pytest

import marivo.analysis as mv
import marivo.analysis.session as session_attach
from marivo.analysis.errors import TransformShapeUnsupportedError
from marivo.analysis.session._load import load_frame
from marivo.refs import ref as ref_factory
from marivo.semantic.catalog import SemanticKind
from tests.ref_helpers import make_ref


def _bootstrap_value_semantics_project(tmp_path) -> None:
    """Sales project: additive amount + non-additive user_id measures."""
    semantic_dir = tmp_path / "models" / "semantic" / "sales"
    semantic_dir.mkdir(parents=True)
    datasource_dir = tmp_path / "models" / "datasources"
    datasource_dir.mkdir(parents=True, exist_ok=True)
    (datasource_dir / "warehouse.py").write_text(
        "import marivo.datasource as md\nmd.duckdb(name='warehouse', path=':memory:')\n",
        encoding="utf-8",
    )
    (semantic_dir / "__init__.py").write_text("")
    (semantic_dir / "_domain.py").write_text(
        "import marivo.semantic as ms\nms.domain(name='sales', owner='Data')\n",
        encoding="utf-8",
    )
    (semantic_dir / "metrics.py").write_text(
        "import marivo.datasource as md\n"
        "import marivo.semantic as ms\n"
        "warehouse = ms.ref.datasource('warehouse')\n"
        "events = ms.entity(name='events', datasource=warehouse, source=md.table('events'))\n"
        "event_time = ms.time_dimension_column("
        "name='event_time', entity=events, column='event_time', granularity='day')\n"
        "region = ms.dimension_column(name='region', entity=events, column='region')\n"
        "amount = ms.measure_column(name='amount', entity=events, column='amount', "
        "additivity='additive', unit='USD')\n"
        "user_id = ms.measure_column(name='user_id', entity=events, column='user_id', "
        "additivity='non_additive')\n"
        "gmv = ms.aggregate(name='gmv', measure=amount, agg='sum')\n"
        "order_count = ms.aggregate(name='order_count', measure=amount, agg='count')\n"
        "buyers = ms.aggregate(name='buyers', measure=user_id, agg='count_distinct')\n"
        "conversion = ms.ratio(name='conversion', numerator=buyers, denominator=order_count)\n",
        encoding="utf-8",
    )
    (tmp_path / "marivo.toml").write_text('[project]\nname = "test"\n')


def _seed_value_semantics(con) -> None:
    con.raw_sql(
        "CREATE TABLE events (event_id INTEGER, event_time DATE, region VARCHAR, "
        "amount DOUBLE, user_id INTEGER)"
    )
    con.raw_sql(
        "INSERT INTO events VALUES "
        "(1, DATE '2026-07-01', 'north', 10.0, 100),"
        "(2, DATE '2026-07-01', 'north', 20.0, 101),"
        "(3, DATE '2026-07-02', 'south', 30.0, 100),"
        "(4, DATE '2026-07-02', 'south', 0.0, 102)"
    )


@pytest.fixture(autouse=True)
def _chdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TZ", "UTC")
    session_attach._reset_process_state()
    yield


@pytest.fixture()
def value_semantics_session(tmp_path):
    _bootstrap_value_semantics_project(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed_value_semantics(con)
    return session_attach.get_or_create(name="value_semantics", backends={"warehouse": lambda: con})


def _observe(session, metric, **kwargs):
    return session.observe(
        make_ref(metric, SemanticKind.METRIC),
        time_scope=mv.time_scope(start="2026-07-01", end="2026-07-02"),
        grain=mv.grain("day"),
        **kwargs,
    )


def test_additive_sum_aggregate_persists_sum_and_additive(value_semantics_session) -> None:
    frame = _observe(value_semantics_session, "sales.gmv")

    assert frame.meta.aggregation == "sum"
    assert frame.meta.additivity == "additive"
    assert frame.meta.reaggregatable is True


def test_count_aggregate_persists_count_and_additive(value_semantics_session) -> None:
    frame = _observe(value_semantics_session, "sales.order_count")

    assert frame.meta.aggregation == "count"
    assert frame.meta.additivity == "additive"
    assert frame.meta.reaggregatable is True


def test_count_distinct_aggregate_is_non_additive_and_not_reaggregatable(
    value_semantics_session,
) -> None:
    frame = _observe(value_semantics_session, "sales.buyers")

    assert frame.meta.aggregation == "count_distinct"
    assert frame.meta.additivity == "non_additive"
    assert frame.meta.reaggregatable is False


def test_ratio_is_non_additive_without_sum_rollup(value_semantics_session) -> None:
    frame = _observe(value_semantics_session, "sales.conversion")

    assert frame.meta.additivity == "non_additive"
    assert frame.meta.aggregation is None
    assert frame.meta.reaggregatable is False

    with pytest.raises(TransformShapeUnsupportedError) as exc_info:
        frame.transform.rollup(
            drop_axes=[make_ref("sales.events.region", SemanticKind.DIMENSION)],
        )
    assert exc_info.value._context["reason"] == "non_reaggregatable"


def test_multi_metric_observe_preserves_per_binding_aggregation(value_semantics_session) -> None:
    catalog = value_semantics_session.catalog
    frame = value_semantics_session.observe(
        [
            catalog.require(ref_factory.metric("sales.gmv")).ref,
            catalog.require(ref_factory.metric("sales.order_count")).ref,
        ],
        time_scope=mv.time_scope(start="2026-07-01", end="2026-07-02"),
        grain=mv.grain("day"),
    )

    assert [m["metric_id"] for m in frame.meta.measures] == ["sales.gmv", "sales.order_count"]
    assert frame.meta.measures[0]["aggregation"] == "sum"
    assert frame.meta.measures[1]["aggregation"] == "count"
    assert frame.meta.measures[0]["additivity"] == "additive"
    assert frame.meta.measures[1]["additivity"] == "additive"
    assert frame.meta.reaggregatable is True


def test_normalize_invalidates_plain_sum_reaggregation(value_semantics_session) -> None:
    frame = value_semantics_session.observe(
        make_ref("sales.gmv", SemanticKind.METRIC),
        time_scope=mv.time_scope(start="2026-07-01", end="2026-07-03"),
        grain=mv.grain("day"),
    )

    normalized = frame.transform.normalize(mode="index")

    assert normalized.meta.additivity == "additive"
    assert normalized.meta.reaggregatable is False
    assert normalized.meta.measure_bindings[0].reaggregatable is False
    assert normalized.measures_meta()[0]["reaggregatable"] is False

    with pytest.raises(TransformShapeUnsupportedError) as exc_info:
        normalized.transform.rollup(grain=mv.grain("month"))
    assert exc_info.value._context["reason"] == "non_reaggregatable"

    reloaded = load_frame(normalized.ref, session=value_semantics_session)
    assert reloaded.meta.reaggregatable is False
    assert reloaded.meta.measure_bindings[0].reaggregatable is False


@pytest.mark.parametrize("legacy_additivity", ["non_additive", None])
def test_load_clamps_legacy_non_additive_reaggregatable(
    value_semantics_session, legacy_additivity
) -> None:
    """A pre-issue-110 artifact must load as blocked, not as sum-rollup-able.

    The legacy persist path derived ``reaggregatable`` from the fold/cumulative
    marker alone, so ``non_additive``/unknown metrics shipped with
    ``reaggregatable=True``. On load the conservative clamp must downgrade that
    combination to ``False`` (issue #110 P2) — both at the frame level and in
    the rollup gate.
    """
    frame = _observe(value_semantics_session, "sales.buyers")
    assert frame.meta.reaggregatable is False  # sanity: current artifact is correct

    meta_path = value_semantics_session._layout.frames_dir / frame.ref / "meta.json"
    payload = json.loads(meta_path.read_text())
    payload["measure_bindings"] = []  # legacy scalar shape: no typed bindings
    payload["reaggregatable"] = True  # pre-issue-110 persisted value
    payload["additivity"] = legacy_additivity
    meta_path.write_text(json.dumps(payload))

    reloaded = load_frame(frame.ref, session=value_semantics_session)

    assert reloaded.meta.measure_bindings == ()
    assert reloaded.meta.additivity == legacy_additivity
    assert reloaded.meta.reaggregatable is False

    with pytest.raises(TransformShapeUnsupportedError) as exc_info:
        reloaded.transform.rollup(
            drop_axes=[make_ref("sales.events.region", SemanticKind.DIMENSION)]
        )
    assert exc_info.value._context["reason"] == "non_reaggregatable"


def test_load_clamps_legacy_binding_reaggregatable(value_semantics_session) -> None:
    """A legacy per-binding ``reaggregatable=True`` on a non-additive metric is
    also downgraded on load, so the frame-level flag recomputes from the clamped
    bindings (issue #110 P2 binding branch).
    """
    frame = _observe(value_semantics_session, "sales.buyers")
    assert frame.meta.reaggregatable is False

    meta_path = value_semantics_session._layout.frames_dir / frame.ref / "meta.json"
    payload = json.loads(meta_path.read_text())
    for binding in payload["measure_bindings"]:
        binding["reaggregatable"] = True
    payload["reaggregatable"] = True
    meta_path.write_text(json.dumps(payload))

    reloaded = load_frame(frame.ref, session=value_semantics_session)

    assert reloaded.meta.measure_bindings[0].reaggregatable is False
    assert reloaded.meta.reaggregatable is False
