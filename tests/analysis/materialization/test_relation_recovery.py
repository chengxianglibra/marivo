"""Fresh-process relation recipes and required terminal receipt integrity."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.errors import AnalysisError
from marivo.datasource.adapters import SourceSession
from marivo.semantic.reader import SemanticProject
from tests.analysis.statistics.pandas_workflows import receipt_paths
from tests.packaging.relation_journeys import Scenario
from tests.shared_fixtures import DslCaseFactory, export_dsl_parquet_models, terminal_runs
from tests.support.json import Json, read
from tests.support.paths import PROJECT_ROOT


@pytest.mark.slow
@pytest.mark.parametrize("scenario", ("a02", "a06", "a07", "a08"))
def test_parquet_relation_journey_recover_in_three_processes(
    tmp_path: Path, scenario: Scenario
) -> None:
    project = tmp_path / "project"
    reports: list[dict[str, Json]] = []
    for phase in ("produce", "fixed", "cold"):
        output = tmp_path / f"{phase}.json"
        process = subprocess.run(
            [
                sys.executable,
                "-m",
                "tests.packaging.relation_journeys",
                phase,
                str(project),
                scenario,
                "parquet",
                str(output),
            ],
            cwd=tmp_path,
            env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT), "MARIVO_TELEMETRY": "off"},
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert process.returncode == 0, process.stdout + process.stderr
        reports.append(read(output))
    assert len({report["pid"] for report in reports}) == 3
    for field in (
        "session",
        "snapshots",
        "oracle",
        "facts_sha256",
        "edge_checks",
        "shared_nodes",
        "derived",
        "run_count",
    ):
        assert reports[0][field] == reports[1][field] == reports[2][field]
    assert all(report["source_offline"] is True for report in reports)


@pytest.mark.slow
def test_required_terminal_receipts_reject_corruption_without_source(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = analysis_dsl_case_factory("j2")
    export_dsl_parquet_models(case, case.root)
    ms.load(workspace_dir=case.root)
    session = case.session
    members = session.members(session.catalog.entities.get("sales.customer"))
    metric = session.catalog.metrics.get("sales.order_count")
    current = session.observe(
        {"value": metric},
        by=mv.product(session.time_scope(start="2026-08-01", end="2026-09-01"), members),
    )
    baseline = session.observe(
        {"value": metric},
        by=mv.product(session.time_scope(start="2026-07-01", end="2026-08-01"), members),
    )
    difference = current.compare(
        {"reference": baseline},
        on={"reference": tuple(zip(current.axes, baseline.axes, strict=True))},
        match={"reference": mv.ordinal()},
    ).persist()
    previous = current.persist()
    expected = difference.to_pandas()
    previous_rows = previous.to_pandas()
    assert expected[("value", "reference.absolute.value")].tolist() == [0, 0, 0, 0]
    assert previous_rows[("value", "value")].tolist() == [1, 1, 1, 1]
    runs_before = terminal_runs(session)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("damaged terminal output replayed its source")

    monkeypatch.setattr(ms, "load", forbidden)
    monkeypatch.setattr(SemanticProject, "load", forbidden)
    monkeypatch.setattr(SourceSession, "__enter__", forbidden)
    monkeypatch.setattr(SourceSession, "execute", forbidden)
    primary = receipt_paths(case.root, difference)[0]
    contents = primary.read_bytes()
    for damage in ("missing", "payload"):
        try:
            if damage == "missing":
                primary.unlink()
            else:
                primary.write_bytes(b"damaged required terminal output")
            # Opening metadata is deliberately lazy; the actual consumed file fails.
            reopened = session.persisted_frame(difference.artifact.id)
            with pytest.raises(AnalysisError):
                reopened.to_pandas()
            with pytest.raises(AnalysisError):
                difference.to_pandas()
            pd.testing.assert_frame_equal(previous.to_pandas(), previous_rows)
            assert terminal_runs(session) == runs_before
        finally:
            primary.write_bytes(contents)
        pd.testing.assert_frame_equal(
            session.persisted_frame(difference.artifact.id).to_pandas(), expected
        )
    assert terminal_runs(session) == runs_before
