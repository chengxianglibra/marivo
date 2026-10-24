"""Explicit business coverage masks consume complete terminal calendar exports."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.errors import AnalysisError
from marivo.datasource.adapters import SourceSession
from tests.analysis.materialization.domain_recovery_worker import snapshot
from tests.analysis.statistics.pandas_workflows import forecast, receipt_paths
from tests.analysis.statistics.runs_worker import segments
from tests.shared_fixtures import terminal_runs
from tests.support.json import encode
from tests.support.paths import PROJECT_ROOT

START = datetime(2026, 8, 1, tzinfo=timezone.utc)


@pytest.fixture
def coverage_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Seed the three keyed source rows consumed by the business-mask recipes."""
    source = tmp_path / "source_files"
    source.mkdir()
    path = source / "order.parquet"
    pq.write_table(
        pa.table(
            {
                "order_id": ["a", "b", "c"],
                "ordered_at": pa.array(
                    [datetime(2026, 8, day, 12) for day in (1, 2, 3)],
                    type=pa.timestamp("us"),
                ),
                "profile_0": [1, 2, 7],
            }
        ),
        path,
    )
    semantic = tmp_path / "models/semantic/sales"
    semantic.mkdir(parents=True)
    datasources = tmp_path / "models/datasources"
    datasources.mkdir()
    (tmp_path / "marivo.toml").write_text('[project]\nname = "business-coverage"\n')
    (datasources / "warehouse.py").write_text(
        "import marivo.datasource as md\nmd.duckdb(name='warehouse', path=':memory:')\n"
    )
    (semantic / "_domain.py").write_text(
        "import marivo.semantic as ms\nms.domain(name='sales', owner='Fixture', default=True)\n"
    )
    (semantic / "models.py").write_text(
        "import marivo.datasource as md\n"
        "import marivo.semantic as ms\n"
        f"orders = ms.entity(name='order', datasource=ms.ref.datasource('warehouse'), source=md.parquet({str(path)!r}), primary_key=['order_id'])\n"
        "ordered_at = ms.time_dimension_column(name='ordered_at', entity=orders, column='ordered_at', parse=ms.timestamp())\n"
        "profile_0 = ms.measure_column(name='profile_0', entity=orders, column='profile_0', additivity=ms.additive_all(), unit='CNY')\n"
        "@ms.metric()\ndef total_0():\n    return profile_0.sum(time=ordered_at)\n"
    )
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    return tmp_path


def _query(session: mv.Session) -> mv.AnalysisFrame:
    period = session.time_scope(start=START, end=START + timedelta(days=3))
    return session.observe(
        {"value": session.catalog.metrics.get("sales.total_0")},
        by=mv.product(
            period.by(mv.grain("day")),
            session.members(session.catalog.entities.get("sales.order")),
        ),
    )


def _mask(frame: pd.DataFrame) -> pd.DataFrame:
    """Keep the original three dates and apply a separate business policy."""
    result = frame.copy(deep=True)
    calendar = next(axis for axis in frame.attrs["axes"] if axis["role"] == "calendar")
    name = calendar["token"]
    descriptor = calendar["descriptor"]
    directory = next(
        part["rows"]
        for part in frame.attrs["frozen_coordinates"]
        if part["name"] == descriptor["frozen"]["directory_name"]
    )
    key = descriptor["columns"][0]["slot_id"]
    uncovered_keys = directory.loc[directory["actual_start"] == START + timedelta(days=1), key]
    uncovered = result[("coordinate", name)].isin(uncovered_keys)
    assert uncovered.sum() == 3
    result.loc[uncovered, ("value", "value")] = pd.NA
    return result


@pytest.mark.slow
def test_public_full_grid_unknown(
    coverage_project: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    session = mv.session.create("business-coverage", timezone="UTC")
    artifact = _query(session).persist()
    raw = artifact.to_pandas()
    assert len(raw) == 9 and not raw[("value", "value")].isna().any()
    assert sorted(raw[("value", "value")].tolist()) == [0] * 6 + [1, 2, 7]
    frame = _mask(raw)
    assert frame[("value", "value")].isna().sum() == 3
    assert sorted(frame[("value", "value")].dropna().tolist()) == [0] * 4 + [1, 7]
    artifact.show(n=0)
    text = capsys.readouterr().out
    assert "rows: 9" in text and ".to_pandas()" in text
    # The terminal source result has no external business-completeness claim.
    # The mask belongs to this pandas workflow and never changes saved values.
    assert artifact.to_pandas().equals(raw)
    before = len(terminal_runs(session))

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("business-mask consumer accessed its source")

    monkeypatch.setattr(SourceSession, "execute", forbidden)
    runs = segments(frame, threshold=-1)
    assert runs["count"].tolist() == [1] * 6
    assert runs["duration"].tolist() == [timedelta(days=1)] * 6
    assert sorted(runs["start"].tolist()) == [START] * 3 + [START + timedelta(days=2)] * 3
    assert (
        sorted(runs["end"].tolist())
        == [START + timedelta(days=1)] * 3 + [START + timedelta(days=3)] * 3
    )
    # A raw forecast recipe cannot silently consume a business-masked NA.
    with pytest.raises(ValueError):
        forecast(frame[("value", "value")])
    assert len(forecast(raw[("value", "value")])) == 2
    assert len(terminal_runs(session)) == before
    for path in receipt_paths(coverage_project, artifact):
        contents = path.read_bytes()
        try:
            path.write_bytes(b"invalid calendar output")
            with pytest.raises(AnalysisError):
                artifact.to_pandas()
        finally:
            path.write_bytes(contents)
    (coverage_project / "business-mask.json").write_bytes(
        encode(
            {
                "session": session.id,
                "artifact": artifact.artifact.id,
                "snapshot": snapshot(artifact, frame=raw),
            }
        )
    )
    for path in (coverage_project / "source_files").glob("*.parquet"):
        path.rename(path.with_suffix(".offline"))
    shutil.rmtree(coverage_project / "models")
    script = """
import json
from pathlib import Path
import marivo.analysis as mv
from tests.shared_fixtures import terminal_runs
import marivo.semantic as ms
from marivo.datasource.adapters import SourceSession
from marivo.semantic.reader import SemanticProject
from tests.analysis.graph.test_full_grid_unknown import _mask
from tests.analysis.statistics.runs_worker import segments
from tests.analysis.materialization.domain_recovery_worker import snapshot

def forbidden(*args, **kwargs):
    raise AssertionError('cold business mask read live source or Semantic')
ms.load = forbidden
SemanticProject.load = forbidden
SourceSession.__enter__ = forbidden
SourceSession.execute = forbidden
state = json.loads(Path('business-mask.json').read_text())
session = mv.session.resume(state['session'])
before = len(terminal_runs(session))
output = session.persisted_frame(state['artifact'])
rows = output.to_pandas()
assert snapshot(output, frame=rows) == state['snapshot']
masked = _mask(rows)
assert masked[('value','value')].isna().sum() == 3
assert segments(masked, threshold=-1)['count'].tolist() == [1]*6
assert len(terminal_runs(session)) == before
"""
    cold = subprocess.run(
        [sys.executable, "-c", script],
        cwd=coverage_project,
        env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT)},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert cold.returncode == 0, cold.stdout + cold.stderr


def test_public_duration_business_coverage(
    coverage_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = coverage_project / "source_files/order.parquet"
    table = pq.read_table(path)
    index = table.schema.get_field_index("profile_0")
    pq.write_table(
        table.set_column(index, "profile_0", table["profile_0"].cast(pa.duration("us"))), path
    )
    ms.load(workspace_dir=coverage_project)
    session = mv.session.create("duration-business-coverage", timezone="UTC")
    query = _query(session)
    original = query.persist()
    ratio = query.select({"value": query["value"] / query["value"]}).persist()
    original_rows = original.to_pandas()
    original_values = original_rows[("value", "value")]
    dtype = original_values.dtype
    assert isinstance(dtype, pd.ArrowDtype) and dtype.pyarrow_dtype == pa.duration("us")
    assert _mask(original_rows)[("value", "value")].isna().sum() == 3
    before = len(terminal_runs(session))

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Duration business mask touched its source")

    monkeypatch.setattr(SourceSession, "execute", forbidden)
    frame = _mask(ratio.to_pandas())
    assert frame[("value", "value")].isna().sum() == 7
    assert frame[("value", "value")].dropna().tolist() == [1.0, 1.0]
    assert len(terminal_runs(session)) == before


def test_business_coverage_source_selection_keeps_original_support(
    coverage_project: Path,
) -> None:
    ms.load(workspace_dir=coverage_project)
    session = mv.session.create("business-coverage-selection", timezone="UTC")
    original = _query(session).persist()
    raw = original.to_pandas()
    masked = _mask(raw)
    selected = masked.loc[masked[("value", "value")].notna()].copy()
    assert len(selected) == 6
    # Selecting represented data keeps the original nominal and actual bounds;
    # a removed middle date must never make the two surviving dates adjacent.
    runs = segments(selected, threshold=-1)
    assert runs["count"].tolist() == [1] * 6
    assert runs["duration"].tolist() == [timedelta(days=1)] * 6
    assert original.to_pandas().equals(raw)
    assert session._runtime.store.resources(session.id) == ()
