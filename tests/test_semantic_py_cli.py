"""Tests for marivo.semantic_py CLI check subcommand."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def _run_cli(*args: str, cwd: str | Path | None = None) -> subprocess.CompletedProcess[str]:
    """Run the semantic_py CLI as a subprocess."""
    return subprocess.run(
        [sys.executable, "-m", "marivo.semantic_py", "check", *args],
        capture_output=True,
        text=True,
        cwd=cwd,
        check=False,
        timeout=30,
    )


def _bootstrap_ready_project(tmp_path: Path) -> Path:
    """Create a ready semantic project directory and return project root."""
    root = tmp_path / "project"
    semantic_dir = root / ".marivo" / "semantic" / "sales"
    semantic_dir.mkdir(parents=True)
    datasource_dir = semantic_dir.parent.parent / "datasource"
    datasource_dir.mkdir(parents=True, exist_ok=True)
    (datasource_dir / "warehouse.py").write_text(
        "import marivo.datasource_py as md\n"
        "md.datasource(name='warehouse', backend_type='duckdb', path=':memory:')\n"
    )
    (semantic_dir / "__init__.py").write_text("")
    (semantic_dir / "_model.py").write_text(
        "import marivo.semantic_py as ms\nms.model(name='sales')\n"
    )
    (semantic_dir / "definitions.py").write_text(
        "import marivo.semantic_py as ms\n"
        "\n"
        "@ms.dataset(name='orders', datasource='warehouse')\n"
        "def orders(backend):\n"
        "    return backend.table('orders')\n"
        "\n"
        "@ms.time_field(dataset=orders, data_type='date', granularity='day')\n"
        "def created_at(orders):\n"
        "    return orders.created_at.cast('date')\n"
        "\n"
        "@ms.metric(datasets=[orders], decomposition=ms.sum(), name='revenue')\n"
        "def revenue(orders):\n"
        "    return orders.amount.sum()\n"
    )
    return root


def _bootstrap_errored_project(tmp_path: Path) -> Path:
    """Create a semantic project with errors (missing datasource) and return root."""
    root = tmp_path / "errored_project"
    semantic_dir = root / ".marivo" / "semantic" / "sales"
    semantic_dir.mkdir(parents=True)
    datasource_dir = semantic_dir.parent.parent / "datasource"
    datasource_dir.mkdir(parents=True, exist_ok=True)
    (datasource_dir / "warehouse.py").write_text(
        "import marivo.datasource_py as md\n"
        "md.datasource(name='warehouse', backend_type='duckdb', path=':memory:')\n"
    )
    (semantic_dir / "__init__.py").write_text("")
    (semantic_dir / "_model.py").write_text(
        "import marivo.semantic_py as ms\nms.model(name='sales')\n"
    )
    (semantic_dir / "definitions.py").write_text(
        "import marivo.semantic_py as ms\n"
        "\n"
        "@ms.dataset(name='orders', datasource='sales.nonexistent')\n"
        "def orders(backend):\n"
        "    return backend.table('orders')\n"
    )
    return root


def test_check_exit_code_0_for_ready_project(tmp_path):
    root = _bootstrap_ready_project(tmp_path)
    result = _run_cli(f"--project={root}")
    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"


def test_check_text_format_shows_status(tmp_path):
    root = _bootstrap_ready_project(tmp_path)
    result = _run_cli(f"--project={root}", "--format=text")
    assert result.returncode == 0
    assert "ready" in result.stdout.lower()


def test_check_json_format_valid_schema(tmp_path):
    root = _bootstrap_ready_project(tmp_path)
    result = _run_cli(f"--project={root}", "--format=json")
    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert data["schema_version"] == "1"
    assert data["status"] == "ready"
    assert data["project_root"] == str(root)
    assert isinstance(data["models"], list)
    assert isinstance(data["errors"], list)
    assert isinstance(data["warnings"], list)
    assert isinstance(data["parity"], list)


def test_check_json_format_includes_models(tmp_path):
    root = _bootstrap_ready_project(tmp_path)
    result = _run_cli(f"--project={root}", "--format=json")
    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert len(data["models"]) >= 1
    model = data["models"][0]
    assert model["name"] == "sales"
    assert "object_counts" in model


def test_check_exit_code_1_for_errored_project(tmp_path):
    root = _bootstrap_errored_project(tmp_path)
    result = _run_cli(f"--project={root}")
    assert result.returncode == 1, f"stdout: {result.stdout}\nstderr: {result.stderr}"


def test_check_json_format_shows_errors(tmp_path):
    root = _bootstrap_errored_project(tmp_path)
    result = _run_cli(f"--project={root}", "--format=json")
    assert result.returncode == 1
    data = json.loads(result.stdout)
    assert data["status"] == "errored"
    assert len(data["errors"]) >= 1


def test_check_text_format_shows_errors(tmp_path):
    root = _bootstrap_errored_project(tmp_path)
    result = _run_cli(f"--project={root}", "--format=text")
    assert result.returncode == 1
    assert "error" in result.stdout.lower() or "missing" in result.stdout.lower()


def test_check_exit_code_2_strict_provenance_unverified(tmp_path):
    root = _bootstrap_ready_project(tmp_path)
    # The ready project has metrics without parity checks,
    # so --strict-provenance should detect unverified metrics.
    result = _run_cli(f"--project={root}", "--strict-provenance")
    assert result.returncode == 2, f"stdout: {result.stdout}\nstderr: {result.stderr}"


def test_check_strict_provenance_json(tmp_path):
    root = _bootstrap_ready_project(tmp_path)
    result = _run_cli(f"--project={root}", "--strict-provenance", "--format=json")
    data = json.loads(result.stdout)
    assert result.returncode == 2


def test_check_exit_code_4_missing_directory(tmp_path):
    nonexistent = tmp_path / "does_not_exist"
    result = _run_cli(f"--project={nonexistent}")
    assert result.returncode == 4


def test_check_exit_code_4_directory_without_marivo(tmp_path):
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    result = _run_cli(f"--project={empty_dir}")
    assert result.returncode == 4


def test_check_json_format_missing_project(tmp_path):
    nonexistent = tmp_path / "does_not_exist"
    result = _run_cli(f"--project={nonexistent}", "--format=json")
    assert result.returncode == 4
    data = json.loads(result.stdout)
    assert data["status"] == "errored"


def test_check_warnings_appear_in_json_output(tmp_path):
    # Create a project with string refs (which generate warnings)
    root = tmp_path / "warn_project"
    semantic_dir = root / ".marivo" / "semantic" / "sales"
    semantic_dir.mkdir(parents=True)
    datasource_dir = semantic_dir.parent.parent / "datasource"
    datasource_dir.mkdir(parents=True, exist_ok=True)
    (datasource_dir / "warehouse.py").write_text(
        "import marivo.datasource_py as md\n"
        "md.datasource(name='warehouse', backend_type='duckdb', path=':memory:')\n"
    )
    (semantic_dir / "__init__.py").write_text("")
    (semantic_dir / "_model.py").write_text(
        "import marivo.semantic_py as ms\nms.model(name='sales')\n"
    )
    (semantic_dir / "definitions.py").write_text(
        "import marivo.semantic_py as ms\n"
        "\n"
        "@ms.dataset(name='orders', datasource='warehouse')\n"
        "def orders(backend):\n"
        "    return backend.table('orders')\n"
        "\n"
        # Use string ref for time_field which generates a string_ref warning
        "@ms.time_field(dataset='sales.orders', data_type='date', granularity='day')\n"
        "def created_at(orders):\n"
        "    return orders.created_at.cast('date')\n"
        "\n"
        "@ms.metric(datasets=[orders], decomposition=ms.sum(), name='revenue')\n"
        "def revenue(orders):\n"
        "    return orders.amount.sum()\n"
    )
    result = _run_cli(f"--project={root}", "--format=json")
    data = json.loads(result.stdout)
    assert isinstance(data["warnings"], list)
