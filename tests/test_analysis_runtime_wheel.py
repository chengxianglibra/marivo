"""Release-only smoke for the installed Session runtime public surface."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.release


def _wheel() -> Path:
    wheels = tuple(sorted((Path(__file__).parents[1] / "dist" / "pypi").glob("marivo-*.whl")))
    assert len(wheels) == 1, f"expected exactly one built wheel, received {wheels!r}"
    return wheels[0].resolve()


def _venv_python(venv: Path) -> Path:
    if os.name == "nt":
        return venv / "Scripts" / "python.exe"
    return venv / "bin" / "python"


def _run(command: list[str], *, cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, (
        f"command failed: {command!r}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    return result


def test_clean_wheel_exposes_session_runtime_and_artifact_audit(tmp_path: Path) -> None:
    project = tmp_path / "isolated-project"
    semantic = project / "models" / "semantic" / "sales"
    datasources = project / "models" / "datasources"
    semantic.mkdir(parents=True)
    datasources.mkdir(parents=True)
    (project / "marivo.toml").write_text('[project]\nname = "wheel-smoke"\n')
    (datasources / "warehouse.py").write_text(
        "import marivo.datasource as md\nmd.duckdb(name='warehouse', path='warehouse.duckdb')\n"
    )
    (semantic / "__init__.py").write_text("")
    (semantic / "_domain.py").write_text(
        "import marivo.semantic as ms\nms.domain(name='sales', owner='Release Smoke')\n"
    )
    (semantic / "datasets.py").write_text(
        "import marivo.datasource as md\n"
        "import marivo.semantic as ms\n"
        "warehouse = ms.ref.datasource('warehouse')\n"
        "orders = ms.entity(name='orders', datasource=warehouse, source=md.table('orders'))\n"
        "@ms.time_dimension(entity=orders, granularity='day', is_default=True)\n"
        "def order_date(orders):\n"
        "    return orders.created_at.cast('date')\n"
        "@ms.metric(entities=[orders], additivity='additive', name='revenue')\n"
        "def revenue(orders):\n"
        "    return orders.amount.sum()\n"
    )
    smoke = project / "smoke.py"
    smoke.write_text(
        "from importlib.resources import files\n"
        "import os\n"
        "from pathlib import Path\n"
        "import duckdb\n"
        "import marivo\n"
        "import marivo.analysis as mv\n"
        "import marivo.semantic as ms\n"
        "source_root = Path(os.environ['MARIVO_SOURCE_ROOT']).resolve()\n"
        "assert source_root not in Path(marivo.__file__).resolve().parents\n"
        "with duckdb.connect('warehouse.duckdb') as con:\n"
        "    con.execute('create table orders(created_at timestamp, amount double)')\n"
        "    con.execute(\"insert into orders values ('2026-08-01', 10), ('2026-08-02', 20)\")\n"
        "session = mv.session.get_or_create(name='wheel-session')\n"
        "artifact = session.observe(ms.ref.metric('sales.revenue'))\n"
        "page = session.runs()\n"
        "assert isinstance(page, mv.RunPage) and len(page.items) == 1\n"
        "run = session.get_run(page.items[0].run_id)\n"
        "assert isinstance(run, mv.SucceededRun) and run.output_artifact_ref == artifact.ref\n"
        "recovered = session.artifact(artifact.ref)\n"
        "assert recovered.ref == artifact.ref\n"
        "findings = recovered.findings()\n"
        "assert isinstance(findings, mv.FindingPage)\n"
        "assert recovered.finding_count == len(findings.items)\n"
        "if findings.items:\n"
        "    assert recovered.finding(findings.items[0].finding_id) == findings.items[0]\n"
        "graph = session.graph()\n"
        "assert isinstance(graph, mv.SessionGraph) and graph.head_artifact_refs == (artifact.ref,)\n"
        "focused = session.graph(artifact_ref=artifact.ref, direction='ancestors')\n"
        "assert focused.head_artifact_refs == (artifact.ref,)\n"
        "session.revalidate(artifact.ref)\n"
        "marivo.help('analysis.runtime')\n"
        "marivo.help('analysis.runtime.runs')\n"
        "marivo.help('analysis.evidence')\n"
        "skill = files('marivo.skills').joinpath('marivo-analysis/SKILL.md').read_text()\n"
        "for token in ('bounded Run history', 'exact committed Artifact', "
        "'focused Session graph', 'Artifact-owned Finding reads'):\n"
        "    assert token in skill\n"
        "for stale in ('session.jobs(', 'session.get_frame(', 'session.evidence'):\n"
        "    assert stale not in skill\n"
        "for exported in ('SessionGraph', 'ArtifactSummary', 'RunPage', 'IncompleteRun', "
        "'SucceededRun', 'FailedRun', 'FindingPage', 'Finding'):\n"
        "    assert hasattr(mv, exported)\n"
        "for stale in ('RunRecord', 'FrameRefNotFound', 'JobNotFoundError'):\n"
        "    assert not hasattr(mv, stale)\n"
        "print('clean-wheel Session runtime smoke passed')\n"
    )

    venv = tmp_path / "venv"
    host_env = dict(os.environ)
    host_env.pop("PYTHONPATH", None)
    host_env["PYTHONNOUSERSITE"] = "1"
    host_env["MARIVO_SOURCE_ROOT"] = str(Path(__file__).parents[1].resolve())
    _run([sys.executable, "-m", "venv", str(venv)], cwd=tmp_path, env=host_env)
    python = _venv_python(venv)
    _run(
        [str(python), "-m", "pip", "install", f"{_wheel()}[duckdb]"],
        cwd=tmp_path,
        env=host_env,
    )
    result = _run([str(python), str(smoke)], cwd=project, env=host_env)
    assert "clean-wheel Session runtime smoke passed" in result.stdout
