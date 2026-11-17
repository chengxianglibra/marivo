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
    check = workflow["jobs"]["check"]
    assert check["strategy"]["matrix"]["python-version"] == ["3.10", "3.12", "3.13", "3.14"]
