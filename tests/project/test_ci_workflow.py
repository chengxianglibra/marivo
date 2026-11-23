"""Keep CI from cancelling required main, release, or merge-queue runs."""

from pathlib import Path

import yaml

from tests.support.paths import PROJECT_ROOT


def test_pull_request_runs_cancel_only_their_own_superseded_attempt() -> None:
    workflow_path: Path = PROJECT_ROOT / ".github/workflows/ci.yml"
    workflow = yaml.load(workflow_path.read_text(), Loader=yaml.BaseLoader)
    concurrency = workflow["concurrency"]
    assert concurrency["cancel-in-progress"] == "${{ github.event_name == 'pull_request' }}"
    assert concurrency["group"] == (
        "ci-${{ github.event_name }}-${{ github.event.pull_request.number || github.run_id }}"
    )
    versions = ["3.10", "3.12", "3.13", "3.14"]
    check = workflow["jobs"]["check"]
    assert check["name"] == "check (${{ matrix.python-version }})"
    assert check["strategy"]["matrix"]["python-version"] == versions
    assert check["needs"] == ["typecheck", "tests", "formal"]
    assert check["if"] == "always() && !cancelled()"
    tests = workflow["jobs"]["tests"]
    assert tests["name"] == "tests (${{ matrix.python-version }} shard ${{ matrix.shard }})"
    assert tests["strategy"]["fail-fast"] == "false"
    assert tests["strategy"]["matrix"]["python-version"] == versions
    assert tests["strategy"]["matrix"]["shard"] == ["1", "2", "3", "4"]
    assert workflow["jobs"]["typecheck"]["strategy"]["matrix"]["python-version"] == versions
    formal = workflow["jobs"]["formal"]
    assert not formal.get("needs")
    bootstrap = next(
        step for step in formal["steps"] if step.get("uses") == "./.github/actions/bootstrap"
    )
    assert bootstrap["with"]["python-version"] == "3.12"
    assert any(step.get("run") == "make verify-formal" for step in formal["steps"])
