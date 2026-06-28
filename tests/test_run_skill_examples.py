"""Tests for scripts/run_skill_examples.py."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import textwrap
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
RUNNER = REPO_ROOT / "scripts" / "run_skill_examples.py"
SEMANTIC_EXAMPLES = REPO_ROOT / "marivo/skills/marivo-semantic/references/examples"


def _load_runner_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("run_skill_examples_for_test", RUNNER)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _make_skill_tree(root: Path, skill_name: str, *, skill_md: str = "# placeholder\n") -> Path:
    """Create a minimal marivo/skills/<skill_name>/... layout under root."""
    skill_dir = root / "marivo/skills" / skill_name
    examples_dir = skill_dir / "references" / "examples"
    (examples_dir / "_fixtures").mkdir(parents=True)
    (examples_dir / "_fixtures" / "__init__.py").write_text("")
    (skill_dir / "SKILL.md").write_text(skill_md)
    return examples_dir


_VALID_DATASOURCE_EXAMPLE = textwrap.dedent(
    """
    from types import SimpleNamespace

    def result(*args, **kwargs):
        return SimpleNamespace(table="orders", columns=[], values=[], ok=True)

    md = SimpleNamespace(
        help_text=result,
        test=result,
        discover_entity=result,
        discover_dimensions=result,
        discover_time_dimensions=result,
        discover_measures=result,
        discover_dimension_values=result,
    )

    md.help_text("discover_entity")
    md.test("warehouse")
    md.discover_entity()
    md.discover_dimensions()
    md.discover_time_dimensions()
    md.discover_measures()
    md.discover_dimension_values()
    print("semantic datasource example ok")
    """
).lstrip()

_VALID_MODEL_EXAMPLE = textwrap.dedent(
    """
    from types import SimpleNamespace

    def result(*args, **kwargs):
        return SimpleNamespace(status="ok")

    def metric(*args, **kwargs):
        def decorator(fn):
            return fn
        return decorator

    ms = SimpleNamespace(
        measure_column=result,
        aggregate=result,
        relationship=result,
        metric=metric,
        ratio=result,
        weighted_average=result,
        linear=result,
        ref=result,
        verify_object=result,
        readiness=result,
    )
    orders = object()

    # Comments should not affect semantic validation:
    # md.inspect_columns
    # judgment_targets

    ms.measure_column()
    ms.aggregate()
    ms.relationship()

    @ms.metric(root_entity=orders)
    def revenue_by_customer_country():
        return 1

    ms.ratio()
    ms.weighted_average()
    ms.linear()
    ms.verify_object("sales.orders")
    ms.readiness(refs=(ms.ref("entity.sales.orders"),))
    print("semantic model example ok")
    """
).lstrip()

_COMMENT_ONLY_DATASOURCE_EXAMPLE = textwrap.dedent(
    """
    # md.help_text(
    # md.test(
    # md.discover_entity(
    # md.discover_dimensions(
    # md.discover_time_dimensions(
    # md.discover_measures(
    # md.discover_dimension_values(
    print("semantic datasource example ok")
    """
).lstrip()


def _make_semantic_example_tree(
    root: Path,
    *,
    datasource: str | None = None,
    model: str | None = None,
) -> Path:
    _make_skill_tree(root, "marivo-analysis")
    examples = _make_skill_tree(root, "marivo-semantic")
    (examples / "01_datasource.py").write_text(
        datasource if datasource is not None else _VALID_DATASOURCE_EXAMPLE
    )
    (examples / "02_semantic_model.py").write_text(
        model if model is not None else _VALID_MODEL_EXAMPLE
    )
    return examples


def _run_runner(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(RUNNER)],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )


def test_runner_succeeds_on_empty_tree(tmp_path: Path) -> None:
    _make_skill_tree(tmp_path, "marivo-analysis")
    result = _run_runner(tmp_path)
    assert result.returncode == 0, result.stderr


def test_runner_executes_passing_example(tmp_path: Path) -> None:
    examples = _make_skill_tree(tmp_path, "marivo-analysis")
    (examples / "01_smoke.py").write_text('print("hello from example")\n')
    result = _run_runner(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "hello from example" not in result.stdout, (
        "runner should not echo child stdout on success"
    )


def test_runner_uses_in_process_execution_by_default(tmp_path: Path, monkeypatch: object) -> None:
    runner = _load_runner_module()
    examples = _make_skill_tree(tmp_path, "marivo-analysis")
    (examples / "01_smoke.py").write_text('print("hello from example")\n')
    seen_in_process: list[bool] = []

    def check_example(_example: Path, *, in_process: bool = False) -> object | None:
        seen_in_process.append(in_process)
        return None

    monkeypatch.setattr(runner, "_check_example", check_example)

    assert runner.main(["--root", str(tmp_path)]) == 0
    assert seen_in_process == [True]


def test_runner_can_opt_into_subprocess_execution(tmp_path: Path, monkeypatch: object) -> None:
    runner = _load_runner_module()
    examples = _make_skill_tree(tmp_path, "marivo-analysis")
    (examples / "01_smoke.py").write_text('print("hello from example")\n')
    seen_in_process: list[bool] = []

    def check_example(_example: Path, *, in_process: bool = False) -> object | None:
        seen_in_process.append(in_process)
        return None

    monkeypatch.setattr(runner, "_check_example", check_example)

    assert runner.main(["--root", str(tmp_path), "--subprocess"]) == 0
    assert seen_in_process == [False]


def test_in_process_example_prefers_current_root_on_sys_path(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    runner = _load_runner_module()
    examples = _make_skill_tree(tmp_path, "marivo-analysis")
    stale_root = tmp_path / "stale"
    stale_root.mkdir()
    (stale_root / "worktree_marker.py").write_text("VALUE = 'stale'\n")
    (tmp_path / "worktree_marker.py").write_text("VALUE = 'current'\n")
    example = examples / "01_import_marker.py"
    example.write_text(
        "import worktree_marker\n"
        "assert worktree_marker.VALUE == 'current', worktree_marker.VALUE\n"
        "print('loaded current root')\n"
    )

    monkeypatch.chdir(tmp_path)
    monkeypatch.syspath_prepend(str(stale_root))
    sys.modules.pop("worktree_marker", None)

    failure = runner._check_example(example, in_process=True)

    assert failure is None


def test_runner_fails_when_example_exits_nonzero(tmp_path: Path) -> None:
    examples = _make_skill_tree(tmp_path, "marivo-analysis")
    (examples / "01_bad.py").write_text("raise SystemExit(2)\n")
    result = _run_runner(tmp_path)
    assert result.returncode != 0
    assert "01_bad.py" in result.stderr


def test_runner_fails_when_example_stdout_empty(tmp_path: Path) -> None:
    examples = _make_skill_tree(tmp_path, "marivo-analysis")
    (examples / "01_quiet.py").write_text("x = 1\n")
    result = _run_runner(tmp_path)
    assert result.returncode != 0
    assert "01_quiet.py" in result.stderr


def test_skill_md_within_cap_passes(tmp_path: Path) -> None:
    _make_skill_tree(tmp_path, "marivo-analysis", skill_md="# ok\n" * 100)
    result = _run_runner(tmp_path)
    assert result.returncode == 0, result.stderr


def test_skill_md_over_cap_fails(tmp_path: Path) -> None:
    _make_skill_tree(tmp_path, "marivo-analysis", skill_md="# x\n" * 700)
    result = _run_runner(tmp_path)
    assert result.returncode != 0
    assert "SKILL.md exceeds" in result.stderr


def test_check_example_reports_timeout_with_partial_stdout(
    tmp_path: Path, monkeypatch: object
) -> None:
    runner = _load_runner_module()
    example = tmp_path / "01_timeout.py"
    example.write_text("raise AssertionError('should not execute')\n")

    def timeout(_example: Path) -> tuple[int, str, str]:
        raise subprocess.TimeoutExpired(
            cmd=[sys.executable, _example.name],
            timeout=0.05,
            output=b"started before timeout\n",
            stderr=b"partial error\n",
        )

    monkeypatch.setattr(runner, "EXAMPLE_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(runner, "_execute_example", timeout)

    failure = runner._check_example(example)

    assert failure is not None
    assert failure.reason == "timeout"
    assert failure.file.name == "01_timeout.py"
    assert "partial stdout: started before timeout" in failure.detail
    assert "partial stderr: partial error" in failure.detail


_PITFALL_PASS = textwrap.dedent(
    """
    \"\"\"Pitfall: x.

    Expected output:
        FakeError: something went wrong
        Fix:
          do this instead
    \"\"\"
    print("FakeError: something went wrong")
    print("Fix:")
    print("  do this instead")
    """
).lstrip()

_PITFALL_FAIL = textwrap.dedent(
    """
    \"\"\"Pitfall: x.

    Expected output:
        FakeError: something went wrong
        Fix:
          do this instead
    \"\"\"
    print("everything is fine!")
    """
).lstrip()

_VALID_TEMPLATE = textwrap.dedent(
    """
    # marivo-example: template

    import marivo.analysis as mv
    import marivo.datasource as md
    import marivo.semantic as ms

    catalog = ms.load()
    metric_ids = catalog.list(kind=ms.SemanticKind.METRIC).ids()
    metric_id = "sales.revenue"
    if metric_id not in metric_ids:
        raise SystemExit(f"Metric not found: {metric_id}")

    session = mv.session.get_or_create(
        name="revenue-investigation",
        timezone="Asia/Shanghai",
        default_calendar="cn_holidays",
    )
    frame = session.observe(
        session.catalog.get(f"metric.{metric_id}"),
        window={"start": "2026-05-01", "end": "2026-05-31"},
    )
    frame.show()

    raise RuntimeError("should not run")
    """
).lstrip()


def test_pitfall_passes_when_keywords_present(tmp_path: Path) -> None:
    examples = _make_skill_tree(tmp_path, "marivo-analysis")
    (examples / "99_pitfall_x.py").write_text(_PITFALL_PASS)
    result = _run_runner(tmp_path)
    assert result.returncode == 0, result.stderr


def test_pitfall_fails_when_keywords_missing(tmp_path: Path) -> None:
    examples = _make_skill_tree(tmp_path, "marivo-analysis")
    (examples / "99_pitfall_x.py").write_text(_PITFALL_FAIL)
    result = _run_runner(tmp_path)
    assert result.returncode != 0
    assert "missing pitfall keyword" in result.stderr.lower()
    assert "99_pitfall_x.py" in result.stderr


def test_template_example_is_validated_without_execution(tmp_path: Path) -> None:
    examples = _make_skill_tree(tmp_path, "marivo-analysis")
    (examples / "00_real_project_template.py").write_text(_VALID_TEMPLATE)
    result = _run_runner(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "should not run" not in result.stderr


def test_template_example_fails_when_required_snippet_is_missing(tmp_path: Path) -> None:
    examples = _make_skill_tree(tmp_path, "marivo-analysis")
    (examples / "00_real_project_template.py").write_text(
        _VALID_TEMPLATE.replace("catalog = ms.load()", "catalog = object()")
    )
    result = _run_runner(tmp_path)
    assert result.returncode != 0
    assert "invalid template" in result.stderr
    assert "ms.load()" in result.stderr


def test_template_example_fails_when_using_fixture_shortcuts(tmp_path: Path) -> None:
    examples = _make_skill_tree(tmp_path, "marivo-analysis")
    (examples / "00_real_project_template.py").write_text(
        _VALID_TEMPLATE + "\nfrom _fixtures.tiny_semantic import ensure_loaded\n"
    )
    result = _run_runner(tmp_path)
    assert result.returncode != 0
    assert "invalid template" in result.stderr
    assert "_fixtures" in result.stderr


def test_runner_accepts_semantic_examples_matching_layering_contract(tmp_path: Path) -> None:
    runner = _load_runner_module()
    _make_semantic_example_tree(tmp_path)

    assert runner.main(["--root", str(tmp_path)]) == 0


def test_runner_rejects_empty_semantic_examples_dir(tmp_path: Path) -> None:
    runner = _load_runner_module()
    _make_skill_tree(tmp_path, "marivo-analysis")
    _make_skill_tree(tmp_path, "marivo-semantic")

    assert runner.main(["--root", str(tmp_path)]) != 0


@pytest.mark.parametrize(
    ("datasource", "model"),
    [
        pytest.param(
            _COMMENT_ONLY_DATASOURCE_EXAMPLE,
            None,
            id="comments-do-not-satisfy-required-calls",
        ),
        pytest.param(
            None,
            _VALID_MODEL_EXAMPLE + "\nmd.inspect_columns()\n",
            id="forbidden-stale-snippet",
        ),
    ],
)
def test_runner_rejects_semantic_examples_outside_layering_contract(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    datasource: str | None,
    model: str | None,
) -> None:
    runner = _load_runner_module()
    _make_semantic_example_tree(tmp_path, datasource=datasource, model=model)

    assert runner.main(["--root", str(tmp_path)]) != 0
    stderr = capsys.readouterr().err.lower()
    assert "semantic example" in stderr
    assert "contract" in stderr or "content" in stderr


def test_semantic_examples_match_layering_simplification_contract() -> None:
    names = {path.name for path in SEMANTIC_EXAMPLES.glob("*.py")}
    assert names == {"01_datasource.py", "02_semantic_model.py"}

    runner = _load_runner_module()
    examples = sorted(SEMANTIC_EXAMPLES.glob("*.py"))
    failures = runner._check_semantic_example_contract(SEMANTIC_EXAMPLES, examples)
    assert failures == []

    datasource = (SEMANTIC_EXAMPLES / "01_datasource.py").read_text()
    model = (SEMANTIC_EXAMPLES / "02_semantic_model.py").read_text()

    assert "DuckDBSpec" not in datasource
    assert "DuckDBSpec" not in model
    assert "md.register(spec)" in datasource
    assert "md.register(" not in model
    assert "md.duckdb(" in datasource
    assert 'md.ref("datasource.warehouse")' in model
