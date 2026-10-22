"""Expression Entity output grain, source identity, and offline recovery."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.core.frame_data import ExpressionFrameBacking, SourceFrameBinding
from tests.shared_fixtures import terminal_runs
from tests.support.paths import PROJECT_ROOT


def project(root: Path, predicate: str = "raw.amount > 0") -> None:
    semantic = root / "models" / "semantic" / "sales"
    semantic.mkdir(parents=True, exist_ok=True)
    (semantic / "_domain.py").write_text(
        "import marivo.semantic as ms\nms.domain(name='sales', owner='fixture', default=True)\n"
    )
    pq.write_table(
        pa.table(
            {
                "region": ["east", "east", "west", "west"],
                "amount": [10, 20, 5, -8],
                "day": pa.array(
                    [datetime(2026, 1, 1, tzinfo=timezone.utc)] * 4,
                    type=pa.timestamp("us", tz="UTC"),
                ),
            }
        ),
        root / "rows.parquet",
    )
    (semantic / "orders.py").write_text(
        "import marivo.datasource as md\nimport marivo.semantic as ms\n"
        "@ms.entity(datasource=ms.ref.datasource('default'), source=md.parquet('rows.parquet'), primary_key=['region'])\n"
        "def regional(raw):\n"
        f"    return raw.filter({predicate}).group_by('region', 'day').aggregate(total=raw.amount.sum())\n"
        "region = ms.dimension_column(name='region', entity=regional, column='region')\n"
        "total = ms.measure_column(name='total', entity=regional, column='total', additivity=ms.additive_all(), unit='USD')\n"
        "day = ms.time_dimension_column(name='day', entity=regional, column='day', is_default=True)\n"
        ""
        "total_value = total\n"
        "@ms.metric()\n"
        "def revenue():\n"
        "    return total_value.sum()\n"
    )


@pytest.mark.slow
def test_transformed_grain_and_cold_terminal_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    session = mv.session.create("expression-entity")
    logical = session.observe(
        {"value": session.catalog.metrics.get("sales.revenue")},
        by=(session.catalog.fields.get("sales.regional.region"),),
    )
    result = logical.persist()
    frame = result.to_pandas()
    assert frame.set_index(frame.columns[0])[("value", "value")].to_dict() == {
        "east": 30,
        "west": 5,
    }
    total = logical.reduce({"value": logical["value"].sum()}).persist()
    assert total.to_pandas()[("value", "value")].tolist() == [35]
    assert isinstance(logical._backing, ExpressionFrameBacking)
    assert isinstance(logical._backing.root, SourceFrameBinding)
    fingerprint = logical._backing.root.graph.fingerprint
    project(tmp_path, "raw.amount > 15")
    other = mv.session.create("changed-expression")
    changed = other.observe(
        {"value": other.catalog.metrics.get("sales.revenue")},
        by=(other.catalog.fields.get("sales.regional.region"),),
    )
    assert isinstance(changed._backing, ExpressionFrameBacking)
    assert isinstance(changed._backing.root, SourceFrameBinding)
    assert changed._backing.root.graph.fingerprint != fingerprint
    assert changed.compute().to_pandas()[("value", "value")].tolist() == [20]
    shutil.rmtree(tmp_path / "models")
    (tmp_path / "rows.parquet").unlink()
    assert result.to_pandas()[("value", "value")].sum() == 35
    code = """import sys
import marivo.analysis as mv
session = mv.session.resume(sys.argv[1])
result = session.persisted_frame(sys.argv[2])
frame = result.to_pandas()
assert frame.set_index(frame.columns[0])[('value', 'value')].to_dict() == {'east':30, 'west':5}
assert session.persisted_frame(sys.argv[3]).to_pandas()[('value', 'value')].tolist() == [35]
assert frame[('value', 'value')].sum() == 35
"""
    completed = subprocess.run(
        [sys.executable, "-c", code, session.id, result.artifact.id, total.artifact.id],
        cwd=tmp_path,
        env=dict(os.environ, PYTHONPATH=str(PROJECT_ROOT), MARIVO_PROJECT_ROOT=str(tmp_path)),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_output_keys_rejected_before_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project(tmp_path)
    path = tmp_path / "models" / "semantic" / "sales" / "orders.py"
    path.write_text(path.read_text().replace("primary_key=['region']", "primary_key=['amount']"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    session = mv.session.create("invalid-output-key")
    with pytest.raises(ms.errors.SemanticRuntimeError) as caught:
        session.frame(session.members(session.catalog.entities.get("sales.regional"))).compute()
    cause = caught.value
    assert cause.expected == "primary_key 'amount' present on the Entity output schema"
    assert cause.received == "output columns: 'region', 'day', 'total'"
    assert terminal_runs(session) == ()
