from __future__ import annotations

import pytest

import marivo.analysis as mv
import marivo.analysis.session as session_attach
import marivo.semantic as ms
from marivo.analysis.errors import AttributionMaterializationError
from marivo.analysis.intents._replay import recover_observe_replay
from tests.conftest import bootstrap_sales_project
from tests.shared_fixtures import connect_sales_orders, sales_backends


@pytest.fixture(autouse=True)
def _runtime_session_state(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TZ", "UTC")
    session_attach._reset_process_state()


@pytest.fixture
def runtime_session(tmp_path):
    bootstrap_sales_project(tmp_path)
    semantic_file = tmp_path / "models" / "semantic" / "sales" / "datasets.py"
    semantic_file.write_text(
        semantic_file.read_text()
        + "\n@ms.measure(entity=orders, additivity='additive', unit='USD')\n"
        + "def amount_measure(orders):\n"
        + "    return orders.amount\n"
    )
    connection = connect_sales_orders()
    return session_attach.get_or_create(
        name="runtime-downstream",
        backends=sales_backends(connection),
    )


def _refs(session):
    measure_id = next(iter(session.catalog._require_index().registry.measures))
    amount = session.catalog.require(ms.Ref.measure(measure_id)).ref
    region = session.catalog.require(ms.Ref.dimension("sales.orders.region")).ref
    return amount, region


def test_runtime_frame_uses_ordinary_quality_and_transform_state(runtime_session) -> None:
    amount, _region = _refs(runtime_session)
    frame = runtime_session.observe(
        mv.runtime_metric.aggregate(amount, agg="sum"),
        time_scope={"start": "2026-07-01", "end": "2026-10-01"},
        grain="month",
    )

    quality = runtime_session.assess_quality(frame)
    top = frame.transform.topk(by="value", limit=1)

    assert quality.meta.source_refs == [frame.ref]
    assert top.meta.metric_identity == frame.meta.metric_identity
    assert top.meta.expression_graph == frame.meta.expression_graph
    assert top.meta.artifact_identity is None
    assert top.meta.comparable_value_semantics is not None
    assert frame.meta.comparable_value_semantics is not None
    assert (
        top.meta.comparable_value_semantics.fingerprint
        != frame.meta.comparable_value_semantics.fingerprint
    )
    assert len(top.to_pandas()) == 1
    with pytest.raises(AttributionMaterializationError) as exc_info:
        recover_observe_replay(top, session=runtime_session)
    assert (
        exc_info.value._context["recoverability_status"] == "transformed_replay_state_unavailable"
    )


def test_runtime_delta_can_replay_missing_axis_for_attribution(runtime_session) -> None:
    amount, region = _refs(runtime_session)
    expression = mv.runtime_metric.aggregate(amount, agg="sum")
    current = runtime_session.observe(
        expression,
        time_scope={"start": "2026-07-01", "end": "2026-08-01"},
    )
    baseline = runtime_session.observe(
        expression,
        time_scope={"start": "2026-08-01", "end": "2026-09-01"},
    )
    delta = runtime_session.compare(current, baseline)

    attribution = runtime_session.attribute(delta, axes=[region])

    assert attribution.meta.params["materialization_status"] == "expanded"
    assert attribution.meta.params["missing_axes"] == ["sales.orders.region"]
    assert set(attribution.to_pandas()["driver"]) == {"NORTH", "SOUTH"}
