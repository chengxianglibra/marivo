"""Structured observe slice predicates."""

import pytest

import marivo.analysis.session as session_attach
from marivo.analysis.errors import SliceInvalidError
from marivo.analysis.intents.observe import observe
from marivo.analysis.refs import DimensionRef, MetricRef
from tests.shared_fixtures import connect_sales_orders, sales_backends


@pytest.fixture(autouse=True)
def _chdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    yield


def _bootstrap_sales(tmp_path):
    from tests.conftest import bootstrap_sales_project

    bootstrap_sales_project(tmp_path)


def _session_with_sales(tmp_path):
    _bootstrap_sales(tmp_path)
    con = connect_sales_orders()
    return session_attach.get_or_create(name="demo", backends=sales_backends(con))


@pytest.mark.parametrize(
    ("slice_spec", "expected"),
    [
        ({"region": {"op": "==", "value": "NORTH"}}, 70.0),
        ({"region": {"op": "!=", "value": "NORTH"}}, 30.0),
        ({"region": {"op": "in", "value": ["NORTH", "SOUTH"]}}, 100.0),
        ({"amount": {"op": ">", "value": 20.0}}, 70.0),
        ({"amount": {"op": ">=", "value": 20.0}}, 90.0),
        ({"amount": {"op": "<", "value": 30.0}}, 30.0),
        ({"amount": {"op": "<=", "value": 30.0}}, 60.0),
        ({"amount": {"op": "between", "value": [20.0, 40.0]}}, 90.0),
    ],
)
def test_observe_structured_slice_predicates(tmp_path, slice_spec, expected):
    session = _session_with_sales(tmp_path)
    where = {DimensionRef(key): value for key, value in slice_spec.items()}
    frame = observe(MetricRef("sales.revenue"), where=where, session=session)
    assert frame.to_pandas().iloc[0, 0] == pytest.approx(expected)


def test_observe_equality_shorthand_still_works(tmp_path):
    session = _session_with_sales(tmp_path)
    frame = observe(
        MetricRef("sales.revenue"), where={DimensionRef("region"): "NORTH"}, session=session
    )
    assert frame.to_pandas().iloc[0, 0] == pytest.approx(70.0)


def test_in_predicate_with_set_is_json_safe_in_job_record(tmp_path):
    session = _session_with_sales(tmp_path)
    frame = observe(
        MetricRef("sales.revenue"),
        where={DimensionRef("region"): {"op": "in", "value": {"NORTH"}}},
        session=session,
    )

    job = next(item for item in session.jobs() if item.output_frame_ref == frame.ref)
    record = session.job(job.id)
    assert record["params"]["where"] == {"region": {"op": "in", "value": ["NORTH"]}}
    assert frame.meta.where == {"region": {"op": "in", "value": ["NORTH"]}}


@pytest.mark.parametrize(
    "slice_spec",
    [
        {"amount": {"op": "contains", "value": 10}},
        {"amount": {"op": "in", "value": []}},
        {"amount": {"op": "between", "value": [10]}},
        {"amount": {"value": 10}},
        {"amount": {"op": ">"}},
        {"amount": {"op": [">"], "value": 10}},
        {"region": {"op": "==", "value": ["NORTH"]}},
        {"region": {"op": "!=", "value": {"NORTH"}}},
        {"amount": {"op": ">", "value": [10]}},
        {"amount": {"op": ">=", "value": (10,)}},
        {"amount": {"op": "<", "value": {"threshold": 10}}},
        {"amount": {"op": "<=", "value": {10}}},
        {"region": ["NORTH"]},
    ],
)
def test_invalid_structured_predicates_raise(tmp_path, slice_spec):
    session = _session_with_sales(tmp_path)
    where = {DimensionRef(key): value for key, value in slice_spec.items()}
    with pytest.raises(SliceInvalidError):
        observe(MetricRef("sales.revenue"), where=where, session=session)


def test_mixed_set_in_predicate_is_json_safe_and_normalized(tmp_path):
    session = _session_with_sales(tmp_path)
    frame = observe(
        MetricRef("sales.revenue"),
        where={DimensionRef("user_id"): {"op": "in", "value": {100, "200"}}},
        session=session,
    )

    job = next(item for item in session.jobs() if item.output_frame_ref == frame.ref)
    record = session.job(job.id)
    assert record["params"]["where"] == {"user_id": {"op": "in", "value": ["200", 100]}}
    assert frame.meta.where == {"user_id": {"op": "in", "value": ["200", 100]}}


def test_non_json_safe_slice_fails_before_session_meta_side_effect(tmp_path):
    session = _session_with_sales(tmp_path)

    with pytest.raises(SliceInvalidError):
        observe(
            MetricRef("sales.revenue"),
            where={DimensionRef("region"): {"op": "in", "value": [object()]}},
            session=session,
        )

    # No frames or jobs should be persisted since observe failed.
    assert len(session.frame_summaries()) == 0
    assert len(session.jobs()) == 0
