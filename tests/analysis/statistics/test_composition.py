"""Explicit Query, pandas selection and new business-query rounds."""

from __future__ import annotations

from typing import Literal

import duckdb
import pandas as pd
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from tests.analysis.statistics.journeys import prepare
from tests.analysis.statistics.pandas_workflows import deviation, forecast, plain
from tests.analysis.statistics.runs_worker import segments
from tests.shared_fixtures import (
    DslCaseFactory,
    analysis_dsl_rows,
    export_dsl_parquet_models,
    run_ids,
)


def test_direct_score_runs_and_next_business_round(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    session = prepare(case, "table")
    with duckdb.connect(str(case.database_path)) as database:
        database.execute(
            "UPDATE \"order\" SET profile_0=CASE channel WHEN 'a' THEN -2 WHEN 'b' THEN 2 ELSE -2 END"
        )
    export_dsl_parquet_models(case, case.root)
    ms.load(workspace_dir=case.root)
    field = session.catalog.fields.get("sales.order.profile_0")
    time = session.catalog.fields.get("sales.order.ordered_at")
    grid = session.time_scope(start="2026-08-01", end="2026-08-04").by(mv.grain("day"))
    artifact = session.observe({"value": field.sum(time=time)}, by=(grid,)).compute()
    exported = artifact.to_pandas()
    assert exported["value"].tolist() == [-2, 2, -2]
    absolute = exported.copy(deep=True)
    absolute["value"] = exported["value"].abs()
    direct = segments(absolute, threshold=1)
    assert direct["count"].tolist() == [3]
    assert direct.duration.tolist() == [pd.Timedelta(days=3)]
    assert str(direct.start.iloc[0]).startswith("2026-08-01")
    assert str(direct.end.iloc[0]).startswith("2026-08-04")
    before = run_ids(session)
    for method in ("mad", "zscore"):
        score = deviation(plain(artifact), method)
        scored = exported.copy(deep=True)
        scored["value"] = score.score
        intervals = segments(scored)
        assert intervals["count"].tolist() == [1]
        assert str(intervals.start.iloc[0]).startswith("2026-08-02")
        assert str(intervals.end.iloc[0]).startswith("2026-08-03")
    constant = exported.copy(deep=True)
    constant["value"] = 1
    zero_scale = deviation(plain(artifact).assign(value=1), "zscore")
    constant["value"] = zero_scale.score
    assert segments(constant).empty
    assert run_ids(session) == before
    axis = session.catalog.fields.get("sales.order.channel")
    domain = mv.coordinates(by=(axis,), keys=(("a",), ("b",), ("c",)))
    previous = session.observe(
        {"value": field.sum(time=time)},
        by=mv.product(session.time_scope(start="2026-08-01", end="2026-08-04"), domain),
    )
    following = session.observe(
        {"value": field.sum(time=time)},
        by=mv.product(session.time_scope(start="2026-08-04", end="2026-08-07"), domain),
    )
    changes = plain(
        following.compare(
            {
                "reference": mv.baseline(
                    previous,
                    on=tuple(zip(following.axes, previous.axes, strict=True)),
                    match=mv.ordinal(),
                )
            }
        ).compute()
    )
    assert changes.set_index("key_1")["reference.absolute.value"].to_dict() == {
        "a": 2,
        "b": -2,
        "c": 2,
    }
    assert changes["reference.absolute.value"].sum() == 2
    assert session._runtime.store.resources(session.id) == ()


@pytest.mark.parametrize("method", ("deviation", "association", "forecast", "runs"))
def test_shared_method_and_transport_are_distinct_from_new_kernels(
    analysis_dsl_case_factory: DslCaseFactory,
    method: str,
) -> None:
    case = analysis_dsl_case_factory("j2")
    session = prepare(case, "table")
    members = session.members(session.catalog.entities.get("sales.order"))
    query = session.read(members, {"value": session.catalog.fields.get("sales.order.profile_0")})
    artifact = query.persist()
    frame = plain(artifact)
    assert frame.value.tolist() == [1, 2, 7]
    before = run_ids(session)
    if method == "deviation":
        result = deviation(frame, "zscore")
        selected = result.loc[result.score > 0]
        assert selected.value.tolist() == [7]
    elif method == "association":
        assert frame.value.astype(float).corr(frame.value.astype(float)) == 1
    elif method == "forecast":
        assert forecast(frame.value).prediction.tolist() == [7, 7]
    else:
        chosen = frame.loc[frame.value > 1]
        assert len(chosen) == 2
    pd.testing.assert_frame_equal(plain(session.persisted_frame(artifact.artifact.id)), frame)
    assert run_ids(session) == before


def test_two_separate_axes_and_joint_keep_the_original_target(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    session = case.session
    axes = (
        session.catalog.fields.get("sales.customer.region"),
        session.catalog.fields.get("sales.order.channel"),
    )
    current = session.observe(
        {"value": session.catalog.metrics.get("sales.order_count")},
        by=(session.time_scope(start="2026-08-01", end="2026-09-01"), *axes),
    )
    baseline = session.observe(
        {"value": session.catalog.metrics.get("sales.order_count")},
        by=(session.time_scope(start="2026-07-01", end="2026-08-01"), *axes),
    )
    frame = plain(
        current.compare(
            {
                "reference": mv.baseline(
                    baseline,
                    on=tuple(zip(current.axes, baseline.axes, strict=True)),
                    match=mv.ordinal(),
                )
            }
        ).compute()
    )
    facts = analysis_dsl_rows("j2")
    target = sum(
        1 if row[4].startswith("2026-08") else -1 if row[4].startswith("2026-07") else 0
        for row in facts.orders
    )
    assert frame["reference.absolute.value"].sum() == target
    before = run_ids(session)
    for axis in ("key_1", "key_2"):
        assert frame.groupby(axis, dropna=False)["reference.absolute.value"].sum().sum() == target
    joint = frame.groupby(["key_1", "key_2"], dropna=False)["reference.absolute.value"].sum()
    assert joint.sum() == target
    assert run_ids(session) == before


@pytest.mark.parametrize("method", ("zscore", "mad"))
def test_pandas_score_selection_starts_an_explicit_followup_query(
    analysis_dsl_case_factory: DslCaseFactory,
    method: Literal["zscore", "mad"],
) -> None:
    case = analysis_dsl_case_factory("j2")
    session = prepare(case, "parquet")
    members = session.members(session.catalog.entities.get("sales.order"))
    field = session.catalog.fields.get("sales.order.profile_0")
    captured = session.read(members, {"value": field}).persist()
    frame = deviation(plain(captured), method)
    before = run_ids(session)
    selected = frame.loc[frame.score > 0, "key_0"].tolist()
    assert selected == [("c", 13)]
    assert run_ids(session) == before
    population = mv.coordinates(
        by=(mv.identity(session.catalog.entities.get("sales.order")),),
        keys=tuple((key,) for key in selected),
    )
    followup = session.observe(
        {"value": field.sum(time=session.catalog.fields.get("sales.order.ordered_at"))},
        by=mv.product(session.time_scope(start="2026-08-03", end="2026-08-04"), population),
    )
    output = plain(followup.persist())
    assert output.key_1.tolist() == [("c", 13)] and output.value.tolist() == [7]
    assert len(run_ids(session)) == len(before) + 1
