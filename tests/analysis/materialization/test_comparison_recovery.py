"""Nested, outer and ratio outputs survive independent source-offline readers."""

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

import marivo.analysis as mv
from marivo.semantic.reader import SemanticProject
from tests.analysis.graph.reference_fixtures import reference_data
from tests.analysis.graph.source_fixtures import author_source_project
from tests.analysis.materialization.domain_recovery_worker import snapshot
from tests.analysis.numeric.comparison_fixtures import assert_comparison, comparison_queries
from tests.datasource.source_cases import source_case
from tests.support.json import Json, encode
from tests.support.paths import PROJECT_ROOT
from tests.support.source_trace import SourceTrace


@pytest.mark.release
@pytest.mark.slow
def test_native_comparison_independent_fixed_and_cold(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    source_trace: SourceTrace,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    data = reference_data("duckdb")
    with source_case("duckdb", "table", tmp_path, monkeypatch, data) as case:
        author_source_project("duckdb", case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("comparison-recovery", timezone="UTC")
        originals: dict[str, Json] = {}
        for name, query in comparison_queries(session).items():
            result = query.persist()
            assert_comparison(result, name)
            originals[name] = {"artifact": result.artifact_id, "snapshot": snapshot(result)}
            source_trace.record(result)
        state: dict[str, Json] = {"session": session.id, "originals": originals}
        (tmp_path / "comparison-recovery.json").write_bytes(encode(state))
        source_trace.save(
            "comparison-source-duckdb",
            case.environment,
            {"rows": 3},
            {"nested": [2, 0, 4], "ratio": [1, None, 1], "outer": [None, None]},
            None,
            (case.session,),
        )
    shutil.rmtree(tmp_path / "models")
    for path in tmp_path.glob("source.duckdb*"):
        path.unlink()
    script = """
import json, os, sys
from pathlib import Path
import marivo.analysis as mv
from tests.shared_fixtures import terminal_runs
import marivo.semantic as ms
from marivo.datasource.adapters import SourceSession
from marivo.semantic.reader import SemanticProject
from tests.analysis.materialization.domain_recovery_worker import snapshot
from tests.analysis.numeric.comparison_fixtures import assert_comparison

def forbidden(*args, **kwargs):
    raise AssertionError('cold comparison read touched live source or Semantic')
ms.load = forbidden
SemanticProject.load = forbidden
SourceSession.__enter__ = forbidden
SourceSession.execute = forbidden
state = json.loads(Path('comparison-recovery.json').read_text())
session = mv.session.resume(state['session'])
before = len(terminal_runs(session))
for name, item in state['originals'].items():
    result = session.persisted_frame(item['artifact'])
    assert snapshot(result) == item['snapshot']
    assert_comparison(result, name)
assert len(terminal_runs(session)) == before
print(json.dumps({'pid':os.getpid(),'phase':sys.argv[1]}))
"""
    reports = []
    for phase in ("cold",):
        completed = subprocess.run(
            [sys.executable, "-c", script, phase],
            cwd=tmp_path,
            env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT)},
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
        reports.append(json.loads(completed.stdout.strip().splitlines()[-1]))
    assert len({os.getpid(), *(report["pid"] for report in reports)}) == 2
