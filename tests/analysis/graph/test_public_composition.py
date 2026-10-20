"""Lazy composition, exact terminal integrity and source-offline pandas analysis."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Literal

import pandas as pd
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.errors import AnalysisError
from marivo.analysis.materialization import terminal_store
from marivo.analysis.materialization.terminal_protocol import InvocationInput
from tests.analysis.numeric.pandas_coordinates import ordinary_columns
from tests.shared_fixtures import (
    DslCase,
    DslCaseFactory,
    analysis_dsl_rows,
    export_dsl_parquet_models,
)
from tests.support.paths import PROJECT_ROOT


def _ratio(case: DslCase, *, coordinates: tuple[str, ...] = ()) -> mv.AnalysisFrame:
    session = case.session
    members = session.members(session.catalog.require(ms.ref.entity("sales.customer")))
    domain = (
        members
        if not coordinates
        else mv.product(
            members,
            session.members(session.catalog.require(ms.ref.entity("sales.order"))).classify(
                by=tuple(session.catalog.fields.get("sales.order." + name) for name in coordinates)
            ),
        )
    )
    return session.observe(
        {"value": session.catalog.metrics.get("sales.aov_from_lines")},
        by=mv.product(session.time_scope(start="2026-08-01", end="2026-09-01"), domain),
    )


def _observations(case: DslCase) -> tuple[mv.AnalysisFrame, mv.AnalysisFrame]:
    session = case.session
    members = session.members(session.catalog.require(ms.ref.entity("sales.customer")))
    current, baseline = tuple(
        session.observe(
            {"value": session.catalog.metrics.get("sales.revenue")},
            by=mv.product(session.time_scope(start=start, end=end), members),
        )
        for start, end in (("2026-08-01", "2026-09-01"), ("2026-07-01", "2026-08-01"))
    )
    return current, baseline


def test_ratio_complete_coordinate_tuple_and_original_components(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("tuple_union")
    observed = _ratio(case, coordinates=(case.names.channel, case.names.status))
    frame = observed.persist().to_pandas()
    assert frame.set_index(list(ordinary_columns(frame)))[("value", "value")].to_dict() == {
        ("A", "mobile", "cancelled"): 0.0,
        ("A", "web", "paid"): 40.0,
    }
    channel = case.session.catalog.fields.get("sales.order.channel")
    rolled = (
        case.session.observe(
            {"value": case.session.catalog.metrics.get("sales.aov_from_lines")},
            by=(case.session.time_scope(start="2026-08-01", end="2026-09-01"), channel),
        )
        .persist()
        .to_pandas()
    )
    assert rolled.set_index(ordinary_columns(rolled)[0])[("value", "value")].to_dict() == {
        "web": 40.0,
        "mobile": 0.0,
    }
    mean = observed.reduce({"mean": observed["value"].mean()}).persist().to_pandas()
    assert mean[("value", "mean")].tolist() == [20.0]


@pytest.mark.parametrize("scenario", ["empty_group", "zero_denominator"])
def test_ratio_original_inputs_and_empty_contribution(
    analysis_dsl_case_factory: DslCaseFactory,
    scenario: Literal["empty_group", "zero_denominator"],
) -> None:
    values = _ratio(analysis_dsl_case_factory(scenario)).persist().to_pandas()[("value", "value")]
    if scenario == "empty_group":
        assert values.iloc[0] == 0
        assert pd.isna(values.iloc[1])
    else:
        assert values.isna().all() and len(values) == 1


def test_empty_numeric_selection_has_distinct_row_policies(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    current, baseline = _observations(analysis_dsl_case_factory("j2"))
    comparison = current.compare(
        baseline, on=tuple(zip(current.axes, baseline.axes, strict=True)), match=mv.ordinal()
    )
    empty = comparison.where(comparison["absolute.value"] < -1000)
    assert empty.persist().to_pandas().empty
    result = (
        empty.reduce(
            {
                "sum": empty["absolute.value"].sum(),
                "count": mv.row_count(),
                "mean": empty["absolute.value"].mean(),
            }
        )
        .persist()
        .to_pandas()
    )
    assert result[("value", "sum")].isna().all()
    assert result[("value", "count")].tolist() == [0]
    assert result[("value", "mean")].isna().all()


@pytest.mark.parametrize("corruption", ["rows", "graph", "format_version"])
def test_exact_artifact_corruption_blocks_consumed_output_without_new_runs(
    analysis_dsl_case_factory: DslCaseFactory,
    corruption: str,
) -> None:
    case = analysis_dsl_case_factory("j1")
    current, _ = _observations(case)
    saved = current.persist()
    store = case.session._runtime.store
    before = case.session.runs().items
    if corruption == "rows":
        receipt = saved.artifact._manifest.data.rows.receipt
        (
            case.root / receipt.project_relative_path / receipt.file_manifest[0].relative_path
        ).unlink()
        # Metadata opening is lazy; consuming the saved output detects the missing file.
        reopened = case.session.persisted_frame(saved.artifact.id)
        for value in (saved, reopened):
            with pytest.raises(AnalysisError):
                value.to_pandas()
    else:
        with store._write() as connection:
            row = connection.execute(
                "SELECT descriptor_payload FROM dataset_artifacts WHERE artifact_ref=?",
                (saved.artifact.id,),
            ).fetchone()
            assert row is not None
            value = json.loads(row[0])
            if corruption == "graph":
                value["value"]["fields"]["continuation_snapshot"] = {}
            else:
                value["format"] = "marivo.terminal_output/v999"
            connection.execute(
                "UPDATE dataset_artifacts SET descriptor_payload=? WHERE artifact_ref=?",
                (json.dumps(value), saved.artifact.id),
            )
        with pytest.raises(AnalysisError) as caught:
            case.session.persisted_frame(saved.artifact.id)
        assert caught.value.expected and caught.value.received and caught.value.repair
    assert case.session.runs().items == before


def test_public_history_and_guarded_abandonment_use_query_runs(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = analysis_dsl_case_factory("j1")
    current, _ = _observations(case)
    saved = current.persist()
    session, store = case.session, case.session._runtime.store
    run = session.get_run(saved.artifact._manifest.run_id)
    assert run.lifecycle == "succeeded"
    assert isinstance(run.dataset_input, InvocationInput)
    assert mv.session.resume(session.id).id == session.id
    assert mv.session.inspect(session.id).runs.items[0].run_id == run.run_id
    latest = session.runs().items[0]
    assert isinstance(latest, mv.SucceededRun)
    assert latest.output_artifact_ref.ref == saved.artifact.id
    session.show()
    run.show()
    with pytest.raises(AnalysisError):
        mv.session.reconcile_run(session_id=session.id, run_id=run.run_id)
    terminal_store.admit(store, session.id, "pending", run.dataset_input.result_id)
    mv.session.reconcile_run(session_id=session.id, run_id="pending")
    assert session.get_run("pending").lifecycle == "failed"
    terminal_store.admit(store, session.id, "other", run.dataset_input.result_id)
    mv.session.reconcile_run(session_id=session.id, run_id="pending")

    def unavailable(*args: object, **kwargs: object) -> None:
        raise RuntimeError("proof unavailable")

    monkeypatch.setattr(
        "marivo.analysis.materialization.terminal_publication.discharge_resources", unavailable
    )
    with pytest.raises(RuntimeError, match="proof unavailable"):
        mv.session.reconcile_run(session_id=session.id, run_id="other")
    assert session.get_run("other").lifecycle == "incomplete"


def _correlation_input(case: DslCase) -> mv.AnalysisFrame:
    session = case.session
    return session.observe(
        {
            "revenue": session.catalog.metrics.get("sales.revenue"),
            "count": session.catalog.metrics.get("sales.order_count"),
        },
        by=mv.product(
            session.time_scope(start="2026-08-01", end="2026-09-01"),
            session.members(session.catalog.require(ms.ref.entity("sales.customer"))),
        ),
    )


@pytest.mark.slow
def test_public_exact_cold_output_after_source_and_models_are_deleted(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    import shutil

    case = analysis_dsl_case_factory("j4")
    saved = _correlation_input(case).persist()
    values = saved.to_pandas()["value"]
    assert values["revenue"].corr(values["count"], method="spearman") == pytest.approx(-0.4)
    case.database_path.unlink()
    shutil.rmtree(case.root / "models")
    script = """
import sys
import ibis
import marivo.analysis as mv
from tests.analysis.numeric.pandas_coordinates import ordinary_columns
import marivo.semantic as ms
from marivo.datasource.runtime import DatasourceConnectionService
from tests.shared_fixtures import run_ids

def forbidden(*args, **kwargs):
    raise AssertionError('cold output consulted a source or current Semantic')
ibis.duckdb.connect = forbidden
ms.load = forbidden
DatasourceConnectionService.use_backend = forbidden
session = mv.session.resume(sys.argv[1])
before = run_ids(session)
saved = session.persisted_frame(sys.argv[2])
values = saved.to_pandas()['value']
coefficient = values['revenue'].corr(values['count'], method='spearman')
assert abs(coefficient + .4) < 1e-12
assert coefficient < .1
assert run_ids(session) == before
"""
    process = subprocess.run(
        [sys.executable, "-c", script, case.session.id, saved.artifact.id],
        cwd=case.root,
        env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT)},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert process.returncode == 0, process.stdout + process.stderr


def test_incompatible_inputs_reject_before_business_or_artifact_reads(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from marivo.analysis.materialization import terminal_storage
    from marivo.datasource.adapters import SourceSession

    case = analysis_dsl_case_factory("j2")
    current, baseline = _observations(case)
    fixed = current.persist()
    other_session = mv.session.create("foreign", timezone="UTC")
    foreign = other_session.observe({"value": other_session.catalog.metrics.get("sales.revenue")})
    before = case.session.runs().items

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("rejected composition read an input")

    monkeypatch.setattr(SourceSession, "execute", forbidden)
    monkeypatch.setattr(terminal_storage, "read_output", forbidden)
    with pytest.raises(AnalysisError):
        current.compare(fixed.artifact, on=tuple(zip(current.axes, fixed.axes, strict=True)))  # type: ignore[arg-type]  # Exercise the terminal Artifact boundary.
    with pytest.raises(AnalysisError):
        current.compare(foreign, on=tuple(zip(current.axes, foreign.axes, strict=False)))
    with pytest.raises(AnalysisError):
        baseline.align(foreign, on=tuple(zip(baseline.axes, foreign.axes, strict=False)))
    assert case.session.runs().items == before


def test_members_schema_preflight_does_not_submit_business_rows(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from marivo.datasource.adapters import SourceSession

    case = analysis_dsl_case_factory("j1")

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("construction submitted business rows")

    monkeypatch.setattr(SourceSession, "execute", forbidden)
    current, _ = _observations(case)
    assert current.output_names
    assert case.session.runs().items == ()


@pytest.mark.parametrize("threshold", [float("nan"), 10**38])
def test_invalid_threshold_rejects_without_another_run(
    analysis_dsl_case_factory: DslCaseFactory, threshold: int | float
) -> None:
    case = analysis_dsl_case_factory("j2")
    current, baseline = _observations(case)
    difference = current.compare(
        baseline, on=tuple(zip(current.axes, baseline.axes, strict=True)), match=mv.ordinal()
    )
    with pytest.raises(AnalysisError):
        predicate = difference["absolute.value"] < threshold
        difference.where(predicate).persist()
    assert current._local.owner == baseline._local.owner
    assert case.session.runs().items == ()


def test_large_integer_threshold_retains_exact_comparison(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    current, _ = _observations(analysis_dsl_case_factory("j2"))
    values = current.select({"large": 2**63})
    same = values.where(values["large"] == 2**63).persist().to_pandas()
    assert len(same) == 4
    assert same[("value", "large")].tolist() == [2**63] * 4
    assert values.where(values["large"] == 2**63 + 1).persist().to_pandas().empty


def test_public_parquet_spearman_has_separate_source_evidence(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    from dataclasses import replace

    case = analysis_dsl_case_factory("j4_ties")
    export_dsl_parquet_models(case, case.root)
    case = replace(
        case,
        catalog=ms.load(workspace_dir=case.root),
        session=mv.session.create("parquet", timezone="UTC"),
    )
    saved = _correlation_input(case).persist()
    values = saved.to_pandas()["value"]
    facts = analysis_dsl_rows("j4_ties")
    amounts = {
        key: sum(row[-1] for row in facts.orders if row[1] == key) for key, _ in facts.customers
    }
    counts = {key: sum(row[1] == key for row in facts.orders) for key, _ in facts.customers}
    oracle = pd.Series(amounts).rank().corr(pd.Series(counts).rank())
    assert values["revenue"].corr(values["count"], method="spearman") == pytest.approx(oracle)
    assert saved.artifact.evidence_digest().evidence_count > 0
    assert case.session._runtime.store.layout.generation == 14
