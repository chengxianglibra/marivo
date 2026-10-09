"""Execute the complete local tutorial and its deliberate mixed-input error."""

import os
import subprocess
from pathlib import Path

import pytest

import marivo.analysis as mv
from marivo.analysis.errors import AnalysisError
from tests.support.documentation import _example
from tests.support.paths import PROJECT_ROOT

TUTORIAL = "guides/python-quick-start"


def _demo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("MARIVO_TELEMETRY", "off")
    namespace: dict[str, object] = {}
    exec(compile(_example("en", "demo-seed", page=TUTORIAL), "demo-seed", "exec"), namespace)
    (tmp_path / "models/datasources/warehouse.py").write_text(
        _example("en", "demo-datasource", page=TUTORIAL)
    )
    (tmp_path / "models/semantic/sales/_domain.py").write_text(
        _example("en", "demo-model", page=TUTORIAL)
    )
    for identifier in ("demo-readiness", "demo-analysis", "demo-evidence", "demo-save"):
        exec(compile(_example("en", identifier, page=TUTORIAL), identifier, "exec"), namespace)
    return namespace


@pytest.mark.runtime
def test_complete_local_tutorial_and_source_offline_process_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    namespace = _demo(tmp_path, monkeypatch)
    expected = {"current_total": 800, "baseline_total": 1000, "change": -200}
    for name, value in expected.items():
        result = namespace[name]
        assert isinstance(
            result, (mv.MaterializedNumericRelation, mv.MaterializedDifferenceRelation)
        )
        assert result.to_pandas()["value"].tolist() == [value]
    regional = namespace["regional_change"]
    assert isinstance(regional, mv.MaterializedDifferenceRelation)
    assert regional.to_pandas().set_index("group")["value"].to_dict() == {
        "east": -100,
        "west": -100,
    }
    session = namespace["session"]
    assert isinstance(session, mv.Session)
    before = len(session.runs().items)
    (tmp_path / "warehouse.duckdb").unlink()
    (tmp_path / "models").rename(tmp_path / "models.offline")
    recovery = (
        _example("en", "demo-recovery", page=TUTORIAL)
        + "\n"
        + (
            'assert rows.set_index("group")["value"].to_dict() == {"east": -100, "west": -100}\n'
            "assert isinstance(restored, mv.MaterializedDifferenceRelation)\n"
            f"assert len(session.runs().items) == {before}\n"
        )
    )
    recovered = subprocess.run(
        [str(PROJECT_ROOT / ".venv/bin/python"), "-c", recovery],
        cwd=tmp_path,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert recovered.returncode == 0, recovered.stdout + recovered.stderr


@pytest.mark.runtime
def test_documented_mixed_input_error_is_structured_and_admits_no_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    namespace = _demo(tmp_path, monkeypatch)
    session = namespace["session"]
    assert isinstance(session, mv.Session)
    # Save the fixed input first so the failure's own admission can be measured.
    page = "reference/troubleshooting"
    setup = _example("en", "mixed-input-setup", page=page)
    exec(compile(setup, "mixed-input-setup", "exec"), namespace)
    before = len(session.runs().items)
    failure = _example("en", "mixed-input-error", page=page)
    with pytest.raises(AnalysisError) as raised:
        exec(compile(failure, "mixed-input-error", "exec"), namespace)
    message = str(raised.value)
    assert "mixed" in message.lower(), message
    assert "mixed live and materialized dependencies" in message, message
    assert "Use matching logical inputs or exact Artifacts from this Session." in message
    assert "marivo.help('analysis')" in message
    assert len(session.runs().items) == before
