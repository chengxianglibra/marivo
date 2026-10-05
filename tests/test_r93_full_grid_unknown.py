"""Native public business coverage supplies actual full-grid Unknown consumers."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import NoReturn

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo._temporal import TimeScope
from marivo.analysis.core.business_coverage import complete, normalize
from marivo.analysis.core.time_grid import bind_grid
from marivo.analysis.errors import AnalysisError, StatisticalRelationError
from marivo.analysis.materialization.deviation_execution import load
from marivo.analysis.materialization.graph_exchange import ExchangePart, from_arrow
from marivo.analysis.materialization.runs_execution import _decode
from marivo.analysis.public_dsl import LogicalNumericRelation
from marivo.datasource.adapters import SourceSession
from tests.deviation_r82_fixture import prepare_profiles
from tests.shared_fixtures import DslCaseFactory

START = datetime(2026, 8, 1, tzinfo=timezone.utc)


def test_business_coverage_union_and_original_bucket_boundaries() -> None:
    grid = bind_grid(
        mv.time_scope(start=START, end=START + timedelta(days=3)),
        mv.grain("day"),
        report_timezone="UTC",
    )
    spans = normalize(
        (
            mv.time_scope(start=START + timedelta(hours=12), end=START + timedelta(days=1)),
            mv.time_scope(start=START, end=START + timedelta(hours=12)),
            mv.time_scope(start=START + timedelta(days=2), end=START + timedelta(days=3)),
        ),
        grid,
    )
    assert len(spans) == 2
    assert [complete(cell, spans) for cell in grid.cells] == [True, False, True]
    inside = normalize((mv.time_scope(start=START, end=START + timedelta(hours=23)),), grid)
    assert not complete(grid.cells[0], inside)
    assert not any(complete(cell, ()) for cell in grid.cells)


@pytest.mark.parametrize("fault", ["naive", "outside", "too_many"])
def test_business_coverage_refuses_unbound_claims(fault: str) -> None:
    grid = bind_grid(
        mv.time_scope(start=START, end=START + timedelta(days=3)),
        mv.grain("day"),
        report_timezone="UTC",
    )
    spans: tuple[TimeScope, ...] = (mv.time_scope(start=START, end=START + timedelta(days=1)),)
    if fault == "naive":
        spans = (
            mv.time_scope(
                start=START.replace(tzinfo=None),
                end=(START + timedelta(days=1)).replace(tzinfo=None),
            ),
        )
    elif fault == "outside":
        spans = (mv.time_scope(start=START - timedelta(days=1), end=START + timedelta(days=1)),)
    else:
        spans *= 65
    with pytest.raises(AnalysisError, match="complete_during"):
        normalize(spans, grid)


@pytest.mark.runtime
@pytest.mark.parametrize("form", ["table", "parquet"])
def test_public_full_grid_unknown(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
    form: str,
) -> None:
    case = analysis_dsl_case_factory("j2")
    prepare_profiles(case, "KS", form, "us", "UTC", False, followup=True)
    ms.load(workspace_dir=case.root)
    session = mv.session.get_or_create("business-coverage", report_timezone="UTC")
    members = session.members(ms.ref.entity("sales.order"))
    grid = mv.time_grid(
        during=mv.time_scope(start=START, end=START + timedelta(days=3)),
        grain=mv.grain("day"),
    )
    windows = (
        mv.time_scope(start=START, end=START + timedelta(days=1)),
        mv.time_scope(start=START + timedelta(days=2), end=START + timedelta(days=3)),
    )
    logical = members.each(grid).observe(
        ms.ref.metric("sales.total_0"), during=grid.window, complete_during=windows
    )
    fixed = logical.execute()
    frame = fixed.to_pandas()
    assert frame.cell_tag.tolist().count("unknown") == 3
    assert frame.cell_tag.tolist().count("defined") == 6
    assert set(frame.loc[frame.cell_tag == "unknown", "cell_reason"]) == {
        "insufficient_business_coverage"
    }
    assert frame.loc[frame.cell_tag == "unknown", "value"].isna().all()
    assert fixed._dataset is not None
    checked = fixed._dataset.verified()
    assert not any("rollup(" in action.call for action in fixed.contract().actions)
    assert "uncovered buckets are Unknown" in dict(fixed.contract()._facts)["business_coverage"]
    coverage = next(p.table for p in checked.parts if p.role == "coverage")
    assert coverage["coverage__complete"].to_pylist() == [True] * 9
    assert coverage["coverage__business_complete"].to_pylist().count(False) == 3
    partial = coverage.filter(pa.compute.invert(coverage["coverage__business_complete"]))
    assert sorted(partial["coverage__partial_value"].to_pylist()) == [0, 0, 2]
    with pytest.raises(AnalysisError, match="business"):
        fixed.rollup().execute()

    def forbidden(*args: object, **kwargs: object) -> NoReturn:
        pytest.fail("Fixed full-grid Unknown consumer accessed its source")

    monkeypatch.setattr(SourceSession, "batches", forbidden)
    errors: list[str] = []
    for model in (mv.naive(), mv.drift(), mv.seasonal_naive(periods=2)):
        before = set(case.root.rglob("*.parquet"))
        with pytest.raises(StatisticalRelationError) as caught:
            fixed.forecast(horizon=mv.periods(1), model=model).execute()
        assert caught.value.code == "r8.cell_policy"
        assert "unknown" in str(caught.value.received)
        assert "insufficient_business_coverage" in str(caught.value.received)
        assert set(case.root.rglob("*.parquet")) == before
        errors.append(str(caught.value.received))
        assert session._runtime.store.resources(session._runtime.session_ref) == ()
    runs = fixed.runs(where=fixed.value.gt(-1)).execute()
    assert runs.count.to_pandas().value.tolist() == [1] * 6
    assert runs.duration.to_pandas().value.tolist() == [timedelta(days=1)] * 6
    assert (
        sorted(runs.start.to_pandas().value.tolist())
        == [START] * 3 + [START + timedelta(days=2)] * 3
    )
    assert (
        sorted(runs.end.to_pandas().value.tolist())
        == [START + timedelta(days=1)] * 3 + [START + timedelta(days=3)] * 3
    )
    assert runs._dataset is not None
    retained = runs._dataset.verified()
    capture, state = _decode(retained.parts)
    assert state.classifications.count("unavailable") == 3
    assert capture.signature.domain.time_grid == checked.contract.signature.domain.time_grid
    assert load(capture.inputs[0]).equals(checked.primary)
    assert {p.role for p in retained.parts} >= {
        "condition_cells",
        "run_cells",
        "grid_cells",
        "subject",
    }
    for column, values in (
        ("coverage__business_complete", [True] * 9),
        ("coverage__partial_value", [99] * 9),
    ):
        corrupted = coverage.set_column(
            coverage.schema.get_field_index(column),
            column,
            pa.array(values, type=coverage.schema.field(column).type),
        )
        with pytest.raises(AnalysisError):
            from_arrow(
                checked.primary,
                checked.contract,
                parts=tuple(
                    ExchangePart(p.role, corrupted) if p.role == "coverage" else p
                    for p in checked.parts
                ),
            )
    assert session._runtime.store.resources(session._runtime.session_ref) == ()
    if form == "parquet":
        artifact_ref = fixed.evidence_digest().artifact_ref.ref
        for path in (case.root / "source_files").glob("*.parquet"):
            path.rename(path.with_suffix(".offline"))
        script = """
import marivo.analysis as mv
from marivo.datasource.adapters import SourceSession
def forbidden(*args: object, **kwargs: object) -> None:
    raise AssertionError('Cold business coverage accessed its source')
SourceSession.batches = forbidden
session = mv.session.get_or_create('business-coverage', report_timezone='UTC')
fixed = session.artifact(__import__('sys').argv[1])
assert fixed.to_pandas().cell_tag.tolist().count('unknown') == 3
runs = fixed.runs(where=fixed.value.gt(-1)).execute()
assert runs.count.to_pandas().value.tolist() == [1] * 6
assert session._runtime.store.resources(session._runtime.session_ref) == ()
"""
        cold = subprocess.run(
            [sys.executable, "-c", script, artifact_ref],
            cwd=case.root,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert cold.returncode == 0, cold.stdout + cold.stderr
    if directory := os.environ.get("MARIVO_R93_EVIDENCE_DIR"):
        Path(directory, "full-grid-unknown-" + form + ".json").write_text(
            json.dumps(
                {
                    "producer": "members.each(grid).observe(sum_metric, complete_during=windows)",
                    "form": form,
                    "unknown_count": 3,
                    "defined_count": 6,
                    "actual_read_coverage": True,
                    "retained_partial_values": [0, 0, 2],
                    "forecast_cell_policy_refusals": errors,
                    "run_segments": 6,
                    "unavailable_cells": 3,
                    "duration_days": [1] * 6,
                    "original_inputs_and_grid_preserved": True,
                    "fixed_source_reads": 0,
                    "corruption_rejected": True,
                    "resources": 0,
                    "cold_source_offline": form == "parquet",
                },
                sort_keys=True,
            )
        )


@pytest.mark.runtime
def test_public_duration_business_coverage(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = analysis_dsl_case_factory("j2")
    prepare_profiles(case, "KS", "parquet", "us", "UTC", False, followup=True)
    path = case.root / "source_files/order.parquet"
    table = pq.read_table(path)
    index = table.schema.get_field_index("profile_0")
    pq.write_table(
        table.set_column(index, "profile_0", table["profile_0"].cast(pa.duration("us"))), path
    )
    ms.load(workspace_dir=case.root)
    session = mv.session.get_or_create("duration-business-coverage", report_timezone="UTC")
    grid = mv.time_grid(
        during=mv.time_scope(start=START, end=START + timedelta(days=3)), grain=mv.grain("day")
    )
    observed = (
        session.members(ms.ref.entity("sales.order"))
        .each(grid)
        .observe(
            ms.ref.metric("sales.total_0"),
            during=grid.window,
            complete_during=(
                mv.time_scope(start=START, end=START + timedelta(days=1)),
                mv.time_scope(start=START + timedelta(days=2), end=START + timedelta(days=3)),
            ),
        )
        .execute()
    )
    assert observed._node.root.value_type.name == "interval('us')"
    assert observed.to_pandas().cell_tag.tolist().count("unknown") == 3

    def forbidden(*args: object, **kwargs: object) -> NoReturn:
        pytest.fail("Duration business coverage quotient accessed its source")

    monkeypatch.setattr(SourceSession, "batches", forbidden)
    ratio = observed.ratio(observed).execute()
    frame = ratio.to_pandas()
    assert frame.cell_tag.tolist().count("unknown") == 3
    assert set(frame.loc[frame.cell_tag == "unknown", "cell_reason"]) == {
        "insufficient_business_coverage"
    }
    assert ratio._dataset is not None
    ratio._dataset.verified()
    assert session._runtime.store.resources(session._runtime.session_ref) == ()


@pytest.mark.runtime
def test_business_coverage_source_selection_keeps_original_support(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    prepare_profiles(case, "KS", "parquet", "us", "UTC", False, followup=True)
    ms.load(workspace_dir=case.root)
    session = mv.session.get_or_create("business-coverage-selection", report_timezone="UTC")
    grid = mv.time_grid(
        during=mv.time_scope(start=START, end=START + timedelta(days=3)), grain=mv.grain("day")
    )
    observed = (
        session.members(ms.ref.entity("sales.order"))
        .each(grid)
        .observe(
            ms.ref.metric("sales.total_0"),
            during=grid.window,
            complete_during=(
                mv.time_scope(start=START, end=START + timedelta(days=1)),
                mv.time_scope(start=START + timedelta(days=2), end=START + timedelta(days=3)),
            ),
        )
    )
    assert isinstance(observed, LogicalNumericRelation)
    selected = observed.where(observed.value.is_defined())
    fixed = selected.execute()
    assert fixed.to_pandas().cell_tag.tolist() == ["defined"] * 6
    assert fixed._dataset is not None
    support = next(p.table for p in fixed._dataset.verified().parts if p.role == "coverage")
    assert support.num_rows == 6
    assert "coverage__partial_value" in support.column_names
    for receiver in (selected, fixed):
        with pytest.raises(AnalysisError):
            receiver.runs(where=receiver.value.gt(-1))
        with pytest.raises(AnalysisError):
            receiver.forecast(horizon=mv.periods(1))
    assert session._runtime.store.resources(session._runtime.session_ref) == ()
