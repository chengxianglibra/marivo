"""Deviation exports preserve raw carriers; statistical recipes belong to pandas."""

import sys
from decimal import Decimal
from fractions import Fraction
from typing import Literal

import duckdb
import pandas as pd
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from tests.analysis.statistics.deviation_oracle import expected
from tests.analysis.statistics.pandas_workflows import (
    assert_clean,
    daily,
    deviation,
    monthly,
    orders,
    plain,
)
from tests.shared_fixtures import DslCaseFactory, run_ids


def test_exported_comparison_supports_pandas_deviation_selection(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    current = monthly(case, "2026-08-01", "2026-09-01")
    baseline = monthly(case, "2026-07-01", "2026-08-01")
    comparison = current.compare(baseline)
    artifact = comparison.execute()
    frame = plain(artifact).rename(columns={"value.absolute": "value"})
    before = run_ids(case.session)
    result = deviation(frame, "zscore")
    values = tuple(frame.value)
    center, _, _, scores = expected(values, "zscore")
    assert result.value.tolist() == list(values)
    assert result.reference.tolist() == [center] * len(values)
    assert result.deviation.tolist() == [Fraction(value) - center for value in values]
    assert result.score.tolist() == list(scores)
    positive = result.loc[result.score > 0]
    assert positive.key_0.tolist() == result.loc[result.score > 0, "key_0"].tolist()
    ranked = result.sort_values(["score", "key_0"], ascending=[False, True]).head(2)
    assert ranked.score.tolist() == sorted(scores, reverse=True)[:2]
    pd.testing.assert_frame_equal(plain(case.session.artifact(artifact.id)), plain(artifact))
    assert run_ids(case.session) == before


@pytest.mark.parametrize("method,profile", [("mad", "DOUBLE"), ("zscore", "DECIMAL(38,38)")])
def test_exported_scores_select_an_explicit_followup_query(
    analysis_dsl_case_factory: DslCaseFactory,
    method: Literal["mad", "zscore"],
    profile: str,
) -> None:
    case = analysis_dsl_case_factory("j2")
    with duckdb.connect(str(case.database_path)) as db:
        if profile == "DECIMAL(38,38)":
            db.execute("UPDATE \"order\" SET amount=10 WHERE order_id='j2_ac'")
        factor = "0.001" if profile.startswith("DECIMAL") else "1"
        db.execute(
            f'ALTER TABLE "order" ALTER amount TYPE {profile} USING CAST(amount * {factor} AS {profile})'
        )
    comparison = monthly(case, "2026-08-01", "2026-09-01").compare(
        monthly(case, "2026-07-01", "2026-08-01")
    )
    frame = plain(comparison.execute()).rename(columns={"value.absolute": "value"})
    before = run_ids(case.session)
    scores = deviation(frame, method)
    selected = scores.loc[scores.score > 0, "key_0"].tolist()
    assert selected == ["B", "D"]
    assert run_ids(case.session) == before
    population = mv.identities(
        case.session.catalog.entities.get("sales.customer"), keys=tuple((key,) for key in selected)
    )
    scope = case.session.time_scope(start="2026-08-01", end="2026-09-01")
    following = case.session.observe(
        {"value": case.session.catalog.metrics.get("sales.revenue")},
        during=scope.by(mv.grain("day")),
        by=population,
        members=population,
    )
    output = plain(following.execute())
    assert len(output) == 62
    assert output.value.sum() == (Decimal("0.12") if profile.startswith("DECIMAL") else 120)
    assert output.value.eq(0).sum() == 61
    assert case.session._runtime.store.resources(case.session.id) == ()


def test_null_partitions_selection_boundary_and_shared_fit(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    with duckdb.connect(str(case.database_path)) as db:
        db.execute('UPDATE "order" SET channel=NULL WHERE amount=0')
    artifact = orders(case).execute()
    frame = plain(artifact)
    before = run_ids(case.session)
    groups = tuple((key, group) for key, group in frame.groupby("category", dropna=False))
    assert sorted(len(group) for _, group in groups) == [4, 7]
    partitions = pd.concat([deviation(group, "mad") for _, group in groups]).sort_index()
    assert len(partitions) == 11
    selected = frame.loc[frame.value > 50]
    assert len(selected) == 5
    selected_fit = deviation(selected, "mad")
    original_fit = deviation(frame, "mad")
    retained = original_fit.loc[original_fit.value > 50]
    assert selected_fit.reference.iloc[0] != retained.reference.iloc[0]
    assert len(original_fit) == 11 and len(selected_fit) == 5
    assert run_ids(case.session) == before


@pytest.mark.parametrize("empty", (False, True))
def test_exported_time_scores_allow_explicit_pandas_refit(
    analysis_dsl_case_factory: DslCaseFactory,
    empty: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from marivo.datasource.adapters import SourceSession
    from tests.analysis.statistics.deviation_oracle import forbidden

    case = analysis_dsl_case_factory("j2")
    artifact = daily(case, end="2026-09-01").execute()
    before = run_ids(case.session)
    monkeypatch.setattr(SourceSession, "__enter__", forbidden)
    monkeypatch.setattr(ms, "load", forbidden)
    scores = deviation(plain(artifact), "mad")
    chosen = scores.loc[scores.score > (scores.score.max() + 1 if empty else 0), ["key_0", "score"]]
    assert chosen.empty is empty
    refit = deviation(chosen.rename(columns={"score": "value"}), "mad")
    assert len(refit) == len(chosen)
    if chosen.empty:
        assert refit.empty
    elif chosen.score.nunique() == 1:
        assert refit.score.isna().all()
    else:
        assert refit.score.tolist() == list(expected(tuple(chosen.score), "mad")[3])
    assert run_ids(case.session) == before


def test_observed_nullable_large_int64_preserves_native_and_saved_values(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    import pyarrow as pa

    case = analysis_dsl_case_factory("j2")
    values = (2**63 - 5, 2**63 - 4, 2**63 - 1)
    with duckdb.connect(str(case.database_path)) as db:
        db.execute('DELETE FROM "order"')
        db.execute('ALTER TABLE "order" ALTER amount TYPE BIGINT')
        for key, value in zip(("a", "b", "c", "null"), (*values, None), strict=True):
            db.execute('INSERT INTO "order" (order_id, amount) VALUES (?, ?)', [key, value])
    import ibis

    native_backend = ibis.duckdb.connect(str(case.database_path))
    try:
        source = native_backend.table("order")
        native = native_backend.execute(
            source.select("order_id", value=source.amount).order_by("order_id")
        )
    finally:
        native_backend.disconnect()
    artifact = orders(case).execute()
    frame = plain(artifact)
    assert frame.value.iloc[:3].tolist() == native.value.iloc[:3].tolist()
    assert frame.value.isna().tolist() == native.value.isna().tolist()
    assert frame.value.dtype == pd.ArrowDtype(pa.float64())
    scored = deviation(frame, "zscore")
    assert scored.value.iloc[:3].tolist() == native.value.iloc[:3].tolist()
    # The native floats have lost the original differences. The local recipe
    # consumes those represented values and does not invent a nonzero spread.
    assert scored.score.isna().all()
    pd.testing.assert_frame_equal(plain(case.session.artifact(artifact.id)), frame)


def test_extreme_binary64_export_supports_bounded_preview(
    analysis_dsl_case_factory: DslCaseFactory,
    capsys: pytest.CaptureFixture[str],
) -> None:
    case = analysis_dsl_case_factory("j2")
    values = (sys.float_info.max, float.fromhex("0x0.0000000000001p-1022"))
    with duckdb.connect(str(case.database_path)) as db:
        db.execute('DELETE FROM "order"')
        db.execute('ALTER TABLE "order" ALTER amount TYPE DOUBLE')
        for category in "abc":
            for index, value in enumerate(values):
                db.execute(
                    'INSERT INTO "order" (order_id, channel, amount) VALUES (?, ?, ?)',
                    [category + str(index), category, value],
                )
    artifact = orders(case).execute()
    frame = plain(artifact)
    result = pd.concat([deviation(group, "mad") for _, group in frame.groupby("category")])
    assert result.score.tolist().count(expected(values, "mad")[3][0]) == 3
    artifact.show(n=2)
    assert len(capsys.readouterr().out.encode()) <= 8192
    assert not hasattr(artifact, "contract")


def test_construction_mode_identity_and_zero_business_reads(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from marivo.analysis.errors import AnalysisError
    from marivo.datasource.adapters import SourceSession
    from tests.analysis.statistics.deviation_oracle import forbidden

    case = analysis_dsl_case_factory("j2")
    query = orders(case)
    before = run_ids(case.session)
    monkeypatch.setattr(SourceSession, "execute", forbidden)
    selected = query.where(query["value"] > 0)
    selected.contract()
    assert run_ids(case.session) == before
    for name in ("deviation", "correlate", "forecast", "runs", "rank"):
        assert not hasattr(query, name)
    with pytest.raises(AnalysisError):
        query.select({"foreign": selected["value"]})


@pytest.mark.parametrize("phase", ("query_admitted", "terminal_primary_written", "before_commit"))
def test_export_phase_failure_is_atomic(
    analysis_dsl_case_factory: DslCaseFactory,
    phase: str,
) -> None:
    from marivo.analysis.errors import AnalysisError

    case = analysis_dsl_case_factory("j2")
    query = orders(case)
    runtime = case.session._runtime

    def inject(actual: str) -> None:
        if actual == phase:
            raise RuntimeError("injected export phase failure")

    runtime._hook = inject
    try:
        with pytest.raises(AnalysisError):
            query.execute()
    finally:
        runtime._hook = None
    assert_clean(case.session, artifacts=0)


@pytest.mark.slow
def test_scalar_export_recovers_in_independent_processes(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    from tests.analysis.statistics.recovery_worker import subprocess_phases

    case = analysis_dsl_case_factory("j2")
    subprocess_phases(case, "scalar")
