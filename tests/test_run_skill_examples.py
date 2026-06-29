"""Tests for scripts/run_skill_examples.py."""

from __future__ import annotations

import contextlib
import importlib.util
import io
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


_VALID_DISCOVER_AND_GRILL_EXAMPLE = textwrap.dedent(
    """
    from types import SimpleNamespace

    def result(*args, **kwargs):
        return SimpleNamespace(table="orders", columns=[], values=[], ok=True, show=lambda: None)

    md = SimpleNamespace(
        help_text=result,
        test=result,
        table=lambda name: name,
        ref=lambda name: name,
        unpruned=lambda **kwargs: kwargs,
        discover_entity=result,
        discover_dimensions=result,
        discover_time_dimensions=result,
        discover_measures=result,
        discover_dimension_values=result,
    )
    ms = SimpleNamespace(
        load=lambda: SimpleNamespace(list=lambda: SimpleNamespace(show=lambda: None)),
        ref=lambda value: SimpleNamespace(id=value),
        verify_object=result,
    )

    md.help_text("discover_entity")
    warehouse = md.ref("datasource.warehouse")
    orders = md.table("orders")
    scope = md.unpruned(max_rows=100)
    md.test(warehouse)
    md.discover_entity(warehouse, orders, scope=scope)
    md.discover_dimensions(warehouse, orders, columns=("region",), scope=scope)
    md.discover_time_dimensions(warehouse, orders, columns=("order_date",), scope=scope)
    md.discover_measures(warehouse, orders, columns=("amount",), scope=scope)
    md.discover_dimension_values(warehouse, orders, column="status", limit=5, scope=scope)
    ms.load().list().show()
    print("GRILL: Should status='refunded' be modeled as an excluded order state or as refund amount?")
    """
).lstrip()

_VALID_AUTHOR_ONE_OBJECT_EXAMPLE = textwrap.dedent(
    """
    from pathlib import Path
    from types import SimpleNamespace

    def result(*args, **kwargs):
        return SimpleNamespace(status="passed", show=lambda: None)

    ms = SimpleNamespace(
        dimension_column=result,
        help=lambda topic: None,
        ref=lambda value: SimpleNamespace(id=value),
        verify_object=result,
        readiness=result,
    )

    declaration = "import marivo.semantic as ms\\nregion = 'declared by fixture'\\n"
    Path("models/semantic/sales").mkdir(parents=True, exist_ok=True)
    Path("models/semantic/sales/order_region.py").write_text(declaration)
    ms.help("dimension_column")
    dimension = ms.dimension_column(
        name="region",
        entity=ms.ref("entity.sales.orders"),
        column="region",
    )
    dimension.show()
    ref = ms.ref("dimension.sales.orders.region")
    verification = ms.verify_object(ref)
    verification.show()
    readiness = ms.readiness(refs=(ref,))
    readiness.show()
    print("verified:", ref.id)
    """
).lstrip()

_COMMENT_ONLY_DISCOVER_AND_GRILL_EXAMPLE = textwrap.dedent(
    """
    # md.help_text(
    # md.test(
    # md.discover_entity(
    # md.discover_dimensions(
    # md.discover_time_dimensions(
    # md.discover_measures(
    # md.discover_dimension_values(
    # GRILL:
    print("semantic discovery example ok")
    """
).lstrip()


def _make_semantic_example_tree(
    root: Path,
    *,
    discover: str | None = None,
    author: str | None = None,
) -> Path:
    _make_skill_tree(root, "marivo-analysis")
    examples = _make_skill_tree(root, "marivo-semantic")
    (examples / "01_discover_and_grill.py").write_text(
        discover if discover is not None else _VALID_DISCOVER_AND_GRILL_EXAMPLE
    )
    (examples / "02_author_one_object.py").write_text(
        author if author is not None else _VALID_AUTHOR_ONE_OBJECT_EXAMPLE
    )
    return examples


def _run_runner(root: Path) -> subprocess.CompletedProcess[str]:
    """Run the example runner in-process, reusing the loaded marivo import.

    Invokes ``runner.main`` in the test process and returns a
    ``CompletedProcess``-shaped object (returncode/stdout/stderr) so call
    sites read identically to a real subprocess. In-process execution avoids
    a fresh interpreter startup plus marivo re-import (~16s) per test; the
    runner logic under test (failure detection, pitfall keywords, template
    validation, SKILL.md caps) is pure Python and behaves the same here as
    behind the CLI ``__main__`` entry covered by ``make examples-check``.
    """
    runner = _load_runner_module()
    out = io.StringIO()
    err = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        returncode = runner.main(["--root", str(root)])
    return subprocess.CompletedProcess(
        args=[str(RUNNER)],
        returncode=returncode,
        stdout=out.getvalue(),
        stderr=err.getvalue(),
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

    def check_example(
        _example: Path,
        *,
        in_process: bool = False,
        repo_root: Path | None = None,
    ) -> object | None:
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

    def check_example(
        _example: Path,
        *,
        in_process: bool = False,
        repo_root: Path | None = None,
    ) -> object | None:
        seen_in_process.append(in_process)
        return None

    monkeypatch.setattr(runner, "_check_example", check_example)

    assert runner.main(["--root", str(tmp_path), "--subprocess"]) == 0
    assert seen_in_process == [False]


def test_subprocess_execution_supports_relative_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _load_runner_module()
    examples = _make_skill_tree(tmp_path, "marivo-analysis")
    (examples / "01_smoke.py").write_text('print("hello from subprocess")\n')
    monkeypatch.chdir(tmp_path)

    assert runner.main(["--root", ".", "--subprocess"]) == 0


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

    def timeout(_example: Path, **_kwargs: object) -> tuple[int, str, str]:
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
    ("discover", "author"),
    [
        pytest.param(
            _COMMENT_ONLY_DISCOVER_AND_GRILL_EXAMPLE,
            None,
            id="comments-do-not-satisfy-required-calls",
        ),
        pytest.param(
            None,
            _VALID_AUTHOR_ONE_OBJECT_EXAMPLE
            + "\nfrom types import SimpleNamespace as _SimpleNamespace\n"
            + "md = _SimpleNamespace(inspect_columns=lambda: None)\n"
            + "md.inspect_columns()\n",
            id="forbidden-stale-snippet",
        ),
        pytest.param(
            _VALID_DISCOVER_AND_GRILL_EXAMPLE
            + "\nms.verify_object(ms.ref('entity.sales.orders'))\n",
            None,
            id="discover-example-must-not-author",
        ),
        pytest.param(
            _VALID_DISCOVER_AND_GRILL_EXAMPLE
            + "\nms.entity = result\n"
            + 'ms.entity(name="orders")\n',
            None,
            id="discover-example-must-not-use-entity-constructor",
        ),
        pytest.param(
            _VALID_DISCOVER_AND_GRILL_EXAMPLE.replace("GRILL:", "QUESTION:"),
            None,
            id="discover-example-must-print-grill",
        ),
        pytest.param(
            None,
            _VALID_AUTHOR_ONE_OBJECT_EXAMPLE.replace(
                "ms.readiness(refs=(ref,))", "result(refs=(ref,))"
            ),
            id="author-example-must-run-readiness",
        ),
        pytest.param(
            None,
            _VALID_AUTHOR_ONE_OBJECT_EXAMPLE.replace("ms.dimension_column", "result").replace(
                "    dimension = result(\n"
                '        name="region",\n'
                '        entity=ms.ref("entity.sales.orders"),\n'
                '        column="region",\n'
                "    )\n"
                "    dimension.show()\n",
                "",
            ),
            id="author-example-must-author-one-object",
        ),
        pytest.param(
            None,
            _VALID_AUTHOR_ONE_OBJECT_EXAMPLE
            + "\nms.measure_column = result\n"
            + 'measure = ms.measure_column(name="amount", entity=ref, column="amount")\n'
            + "measure.show()\n",
            id="author-example-must-author-exactly-one-object",
        ),
        pytest.param(
            None,
            _VALID_AUTHOR_ONE_OBJECT_EXAMPLE
            + '\nsecond_dimension = ms.dimension_column(name="status", entity=ref, column="status")\n'
            + "second_dimension.show()\n",
            id="author-example-must-not-repeat-authoring-call",
        ),
        pytest.param(
            _VALID_DISCOVER_AND_GRILL_EXAMPLE
            + '\nwith open("models/semantic/sales/order_region.py", "w") as handle:\n'
            + '    handle.write("region")\n',
            None,
            id="discover-example-must-not-write-files",
        ),
    ],
)
def test_runner_rejects_semantic_examples_outside_layering_contract(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    discover: str | None,
    author: str | None,
) -> None:
    runner = _load_runner_module()
    _make_semantic_example_tree(tmp_path, discover=discover, author=author)

    assert runner.main(["--root", str(tmp_path)]) != 0
    stderr = capsys.readouterr().err.lower()
    assert "semantic example" in stderr
    assert "contract" in stderr or "content" in stderr


def test_runner_executes_example_inside_support_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _load_runner_module()
    examples = _make_skill_tree(tmp_path, "marivo-analysis")
    support = examples / "_support"
    support.mkdir()
    (support / "__init__.py").write_text("")
    (support / "example_project.py").write_text(
        "from contextlib import contextmanager\n"
        "from pathlib import Path\n"
        "\n"
        "@contextmanager\n"
        "def analysis_examples_project():\n"
        "    root = Path(__file__).resolve().parents[5] / 'fixture-root'\n"
        "    root.mkdir(exist_ok=True)\n"
        "    (root / 'marker.txt').write_text('fixture')\n"
        "    yield type('Ctx', (), {'root': root})()\n"
    )
    (examples / "01_context.py").write_text(
        "from pathlib import Path\n"
        "assert Path.cwd().name == 'fixture-root', Path.cwd()\n"
        "assert Path('marker.txt').read_text() == 'fixture'\n"
        "print('support context ok')\n"
    )

    monkeypatch.chdir(tmp_path)

    assert runner.main(["--root", str(tmp_path)]) == 0


@pytest.mark.parametrize(
    ("mode_args", "mode_name"),
    [
        pytest.param([], "in-process", id="in-process"),
        pytest.param(["--subprocess"], "subprocess", id="subprocess"),
    ],
)
def test_runner_preserves_repo_root_when_support_context_changes_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode_args: list[str],
    mode_name: str,
) -> None:
    runner = _load_runner_module()
    examples = _make_skill_tree(tmp_path, "marivo-analysis")
    support = examples / "_support"
    support.mkdir()
    (support / "__init__.py").write_text("")
    (support / "example_project.py").write_text(
        "import os\n"
        "from contextlib import contextmanager\n"
        "from pathlib import Path\n"
        "\n"
        "@contextmanager\n"
        "def analysis_examples_project():\n"
        "    previous = Path.cwd()\n"
        "    root = Path(__file__).resolve().parents[5] / 'fixture-root'\n"
        "    root.mkdir(exist_ok=True)\n"
        "    (root / 'worktree_marker.py').write_text(\"VALUE = 'stale'\\n\")\n"
        "    os.chdir(root)\n"
        "    try:\n"
        "        yield type('Ctx', (), {'root': root})()\n"
        "    finally:\n"
        "        os.chdir(previous)\n"
    )
    (tmp_path / "worktree_marker.py").write_text("VALUE = 'current'\n")
    (examples / "01_import_root_marker.py").write_text(
        "import worktree_marker\n"
        "assert worktree_marker.VALUE == 'current', worktree_marker.VALUE\n"
        "print('loaded current root')\n"
    )

    monkeypatch.chdir(tmp_path)
    sys.modules.pop("worktree_marker", None)

    assert runner.main(["--root", ".", *mode_args]) == 0, mode_name


def test_runner_rejects_public_example_importing_support_helper(tmp_path: Path) -> None:
    examples = _make_skill_tree(tmp_path, "marivo-analysis")
    (examples / "01_bad.py").write_text(
        "from _support.example_project import analysis_examples_project\nprint('bad import')\n"
    )

    result = _run_runner(tmp_path)

    assert result.returncode != 0
    assert "forbidden public example reference" in result.stderr
    assert "_support" in result.stderr
