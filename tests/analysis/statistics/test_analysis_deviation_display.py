"""Pandas display selection keeps complete keys and the original fitted scope."""

from decimal import Decimal
from fractions import Fraction
from typing import Literal

import pandas as pd
import pytest

import marivo.analysis as mv
from marivo.analysis.errors import AnalysisError
from tests.analysis.statistics.deviation_oracle import expected
from tests.analysis.statistics.pandas_workflows import (
    deviation,
    orders,
    plain,
    receipt_paths,
)
from tests.shared_fixtures import DslCaseFactory, analysis_dsl_rows, run_ids


def test_local_display_pairing_rejects_different_current_keys(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    query = orders(case)
    selected = query.top(3, by=query["value"])
    with pytest.raises(AnalysisError):
        query.select({"current": selected["value"]})


@pytest.mark.parametrize(
    "method,mode,reverse",
    [("zscore", "category", False), ("zscore", "other_fit", True), ("mad", "ranks", False)],
)
def test_mixed_table_preserves_each_current_value_and_original_fit(
    analysis_dsl_case_factory: DslCaseFactory,
    method: Literal["zscore", "mad"],
    mode: str,
    reverse: bool,
) -> None:
    case = analysis_dsl_case_factory("j2")
    artifact = orders(case).persist()
    frame = plain(artifact)
    fitted = deviation(frame, method)
    if mode == "category":
        fitted["other"] = frame.category
    elif mode == "other_fit":
        fitted["other"] = deviation(frame, "mad" if method == "zscore" else "zscore").score
    else:
        fitted = fitted.sort_values(["score", "key_0"], ascending=[False, True]).head(3).copy()
        fitted["other"] = fitted.score.rank(method="dense", ascending=False)
    columns = ["key_0", "other", "score"] if reverse else ["key_0", "score", "other"]
    display = fitted[columns]
    facts = analysis_dsl_rows("j2").orders
    scores = expected(tuple(row[-1] for row in facts), method)[3]
    wanted = dict(zip((row[0] for row in facts), scores, strict=True))
    assert dict(zip(display.key_0, display.score, strict=True)) == {
        key: wanted[key] for key in display.key_0
    }
    if mode == "ranks":
        assert sorted(display.other) == [1, 2, 3]
    pd.testing.assert_frame_equal(plain(case.session.persisted_frame(artifact.artifact.id)), frame)


def test_pandas_rank_projection_retains_original_fit_scope(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    frame = plain(orders(case).persist())
    before = run_ids(case.session)
    scores = deviation(frame, "zscore")
    rank = scores.score.rank(method="dense", ascending=False)
    display = scores.assign(rank=rank).sort_values(["rank", "key_0"]).head(3)
    assert display["rank"].tolist() == [1, 2, 3]
    assert len(scores) == 11
    assert display.reference.nunique() == 1
    assert display.reference.iloc[0] == sum(map(Fraction, frame.value)) / 11
    assert run_ids(case.session) == before


@pytest.mark.parametrize("fault", ("missing", "corrupt"))
def test_saved_result_order_and_primary_receipt_are_required_for_preview(
    analysis_dsl_case_factory: DslCaseFactory,
    fault: str,
) -> None:
    case = analysis_dsl_case_factory("j2")
    query = orders(case)
    artifact = query.top(3, by=query["value"]).persist()
    paths = receipt_paths(case.root, artifact)
    assert len(paths) == 1  # Top order is carried by primary rows, without a permutation.
    expected = plain(artifact)
    assert expected.value.tolist() == sorted(expected.value, reverse=True)
    path = paths[0]
    payload = path.read_bytes()
    before = run_ids(case.session)
    if fault == "missing":
        path.unlink()
    else:
        path.write_bytes(payload[:-1] + bytes((payload[-1] ^ 1,)))
    try:
        reopened = case.session.persisted_frame(artifact.artifact.id)
        with pytest.raises(AnalysisError):
            reopened.show(n=3)
        assert run_ids(case.session) == before
    finally:
        path.write_bytes(payload)
    pd.testing.assert_frame_equal(plain(artifact), expected)
    assert case.session._runtime.store.resources(case.session.id) == ()


@pytest.mark.slow
def test_display_export_recovers_in_fresh_processes(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    from tests.analysis.statistics.recovery_worker import subprocess_phases

    case = analysis_dsl_case_factory("j2")
    subprocess_phases(case, "display")


def test_decimal_grid_tables_retain_current_keys_and_full_fit(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    import duckdb

    case = analysis_dsl_case_factory("j2")
    with duckdb.connect(str(case.database_path)) as db:
        db.execute('ALTER TABLE "order" ALTER amount TYPE DECIMAL(30,6)')
    session = case.session
    grid = session.time_scope(start="2026-08-01", end="2026-08-16").by(mv.grain("day"))
    artifact = session.observe(
        {"value": session.catalog.metrics.get("sales.revenue")},
        by=mv.product(grid, session.members(session.catalog.entities.get("sales.customer"))),
    ).persist()
    frame = plain(artifact)
    assert len(frame) == 60 and frame.value.notna().all()
    assert all(isinstance(value, Decimal) for value in frame.value)
    assert frame.value.sum() == Decimal("180")
    fitted = deviation(frame, "zscore")
    center, _, _, scores = expected(tuple(frame.value), "zscore")
    assert fitted.reference.tolist() == [center] * 60
    assert fitted.score.tolist() == list(scores)
    selected = fitted.sort_values(["score", "key_0", "key_1"], ascending=[False, True, True]).head(
        7
    )
    assert len(selected) == 7 and selected.reference.tolist() == [center] * 7
    assert not selected[["key_0", "key_1"]].duplicated().any()
    pd.testing.assert_frame_equal(plain(session.persisted_frame(artifact.artifact.id)), frame)
