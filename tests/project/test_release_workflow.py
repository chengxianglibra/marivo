"""Protect the build-only release preflight from publishing to PyPI."""

from pathlib import Path

import yaml

from tests.support.paths import PROJECT_ROOT


def test_manual_release_preflight_cannot_publish() -> None:
    workflow_path: Path = PROJECT_ROOT / ".github/workflows/release.yml"
    workflow = yaml.load(workflow_path.read_text(), Loader=yaml.BaseLoader)
    assert set(workflow["on"]) == {"release", "workflow_dispatch"}
    build = workflow["jobs"]["build"]
    checkout = next(
        step for step in build["steps"] if step.get("uses", "").startswith("actions/checkout@")
    )
    assert checkout["with"]["ref"] == (
        "${{ github.event_name == 'release' && "
        "format('refs/tags/{0}', github.event.release.tag_name) || github.sha }}"
    )
    publish = workflow["jobs"]["publish"]
    assert publish["if"] == "github.event_name == 'release'"
    assert publish["needs"] == "build"
    assert publish["permissions"]["id-token"] == "write"
