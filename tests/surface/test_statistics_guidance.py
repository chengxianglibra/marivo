"""Current result statistics and comparison Help remain reachable."""

import subprocess

from marivo.analysis._capabilities.dataset_model import NavigationInput
from marivo.analysis._capabilities.dataset_registry import prepare
from tests.support.paths import PROJECT_ROOT

ROOT = PROJECT_ROOT


def test_nine_current_methods_are_reachable_through_existing_groups() -> None:
    registry = prepare()
    groups = {
        "methods.rows": ("Query.select", "Query.where", "Query.top"),
        "methods.metric.summary": ("row_count", "Query.reduce", "Column.sum", "Column.mean"),
        "methods.compare": ("Query.align", "Query.compare"),
    }
    for group, targets in groups.items():
        owner = registry.by_canonical_id(group)
        assert isinstance(owner, NavigationInput)
        assert set(targets) <= set(owner.members)


def test_cli_help_exits_successfully() -> None:
    result = subprocess.run(
        [str(ROOT / ".venv/bin/marivo"), "help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout
