"""The CI shard map and the preserved check gate stay complete."""

from __future__ import annotations

from pathlib import Path

from scripts.ci_require_checks import JobView, gate_errors
from scripts.ci_shard_files import (
    discover_test_files,
    files_for_shard,
    load_shards,
    pack_files,
    validate_map,
)
from tests.support.paths import PROJECT_ROOT


def _job(
    name: str,
    conclusion: str | None = "success",
    *,
    attempt: int = 1,
    steps: tuple[tuple[str, str | None], ...] = (),
) -> JobView:
    return JobView(name=name, conclusion=conclusion, run_attempt=attempt, steps=steps)


def _passing_jobs() -> list[JobView]:
    jobs = [
        _job(
            "typecheck (3.12)",
            steps=(("Lint", "success"), ("Docs", "success"), ("Typecheck", "success")),
        ),
        _job("typecheck (3.10)", steps=(("Typecheck", "success"),)),
    ]
    for shard in (1, 2):
        jobs.append(_job(f"tests (3.10 shard {shard})"))
        jobs.append(_job(f"tests (3.12 shard {shard})"))
    return jobs


def test_gate_passes_when_this_versions_shards_typecheck_lint_and_docs_passed() -> None:
    assert (
        gate_errors(_passing_jobs(), python_version="3.10", shard_count=2, static_python="3.12")
        == []
    )


def test_gate_reports_a_failed_shard_without_blaming_another_python() -> None:
    jobs = _passing_jobs()
    jobs.append(_job("tests (3.10 shard 2)", "failure", attempt=2))
    errors = gate_errors(jobs, python_version="3.10", shard_count=2, static_python="3.12")
    assert errors == ["job 'tests (3.10 shard 2)' concluded 'failure'; expected 'success'"]
    assert gate_errors(jobs, python_version="3.12", shard_count=2, static_python="3.12") == []


def test_gate_requires_lint_and_docs_for_every_python_version() -> None:
    jobs = _passing_jobs()
    jobs[0] = _job(
        "typecheck (3.12)",
        "failure",
        steps=(("Lint", "success"), ("Docs", "failure"), ("Typecheck", "failure")),
    )
    errors = gate_errors(jobs, python_version="3.10", shard_count=2, static_python="3.12")
    assert any("Docs" in error for error in errors)
    assert all("tests (3.10" not in error for error in errors)


def test_gate_rejects_a_missing_shard() -> None:
    jobs = [job for job in _passing_jobs() if job.name != "tests (3.10 shard 2)"]
    errors = gate_errors(jobs, python_version="3.10", shard_count=2, static_python="3.12")
    assert "missing job 'tests (3.10 shard 2)'" in errors


def test_pack_files_balances_longest_modules_first() -> None:
    packed = pack_files(
        {"slow.py": 10.0, "a.py": 1.0, "b.py": 1.0, "c.py": 1.0, "d.py": 1.0},
        2,
    )
    assert packed[1] == ["slow.py"]
    assert packed[2] == ["a.py", "b.py", "c.py", "d.py"]


def test_shard_one_runs_modules_missing_from_the_map(tmp_path: Path) -> None:
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_known.py").write_text("def test_known():\n    assert True\n")
    (tests / "test_new.py").write_text("def test_new():\n    assert True\n")
    shards = {1: ["tests/test_known.py"], 2: []}
    assert files_for_shard(tmp_path, shards, 1) == [
        "tests/test_known.py",
        "tests/test_new.py",
    ]
    assert files_for_shard(tmp_path, shards, 2) == []
    assert validate_map(tmp_path, shards) == [
        "1 test modules are not in the shard map: tests/test_new.py"
    ]


def test_committed_shard_map_matches_collected_test_modules() -> None:
    shards = load_shards(PROJECT_ROOT / "scripts" / "ci_shards.json")
    assert validate_map(PROJECT_ROOT, shards) == []
    assert discover_test_files(PROJECT_ROOT)
    assert set(shards) == {1, 2, 3, 4}


def test_validate_map_rejects_duplicates_and_unknown_paths(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_one.py").write_text("")
    errors = validate_map(
        tmp_path,
        {1: ["tests/test_one.py", "tests/missing.py"], 2: ["tests/test_one.py"]},
    )
    assert any("assigned to shards" in error for error in errors)
    assert any("would not collect" in error for error in errors)


def test_discover_test_files_uses_pytest_module_patterns(tmp_path: Path) -> None:
    tests = tmp_path / "tests" / "nested"
    tests.mkdir(parents=True)
    (tests / "test_one.py").write_text("def test_one():\n    assert True\n")
    (tests / "helper.py").write_text("def test_hidden():\n    assert True\n")
    (tests / "also_test.py").write_text("def test_also():\n    assert True\n")
    assert discover_test_files(tmp_path) == {
        "tests/nested/test_one.py",
        "tests/nested/also_test.py",
    }
