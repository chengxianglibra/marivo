"""Expression Entity output grain, source identity, and offline recovery."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
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
                    [datetime(2026, 1, 1, tzinfo=UTC)] * 4, type=pa.timestamp("us", tz="UTC")
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
        "day = ms.time_dimension_column(name='day', entity=regional, column='day', granularity='day', is_default=True)\n"
        "revenue = ms.aggregate(name='revenue', measure=total, agg='sum')\n"
    )


@pytest.mark.runtime
def test_transformed_grain_and_cold_fixed_continuation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    session = mv.session.get_or_create("expression-entity")
    logical = session.members(ms.ref.entity("sales.regional")).observe(
        ms.ref.metric("sales.revenue"), by=(ms.ref.dimension("sales.regional.region"),)
    )
    result = logical.execute()
    assert result.to_pandas().set_index("group")["value"].to_dict() == {"east": 30, "west": 5}
    fingerprint = logical._node.binding.graph.leaf.definition.fingerprint
    project(tmp_path, "raw.amount > 15")
    other = mv.session.get_or_create("changed-expression")
    changed = other.members(ms.ref.entity("sales.regional")).observe(
        ms.ref.metric("sales.revenue"), by=(ms.ref.dimension("sales.regional.region"),)
    )
    assert changed._node.binding.graph.leaf.definition.fingerprint != fingerprint
    assert changed.execute().to_pandas()["value"].tolist() == [20]
    shutil.rmtree(tmp_path / "models")
    (tmp_path / "rows.parquet").unlink()
    assert result.rollup().execute().to_pandas()["value"].tolist() == [35]
    code = """import sys
import marivo.analysis as mv
session = mv.session.resume(sys.argv[1], by='id')
result = session.artifact(sys.argv[2])
assert result.to_pandas().set_index('group')['value'].to_dict() == {'east':30, 'west':5}
assert result.rollup().execute().to_pandas()['value'].tolist() == [35]
assert session._runtime.statistics.statements == []
"""
    completed = subprocess.run(
        [sys.executable, "-c", code, session.id, result.state.artifact_ref.ref],
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
    session = mv.session.get_or_create("invalid-output-key")
    with pytest.raises(ms.errors.SemanticRuntimeError, match="primary_key"):
        session.members(ms.ref.entity("sales.regional"))
    assert session.runs().items == ()
