"""Current result statistics and comparison Help remain reachable."""

import re
import subprocess
import sys
from pathlib import Path

import pytest

from marivo.analysis._capabilities.dataset_model import NavigationInput
from marivo.analysis._capabilities.dataset_registry import prepare
from tests.support.paths import PROJECT_ROOT

ROOT = PROJECT_ROOT


def test_current_methods_are_reachable_through_existing_groups() -> None:
    registry = prepare()
    groups = {
        "operators": (
            "analysis_frame.select",
            "analysis_frame.where",
            "analysis_frame.top",
            "row_count",
            "analysis_frame.reduce",
            "analysis_frame.over",
            "column.share",
            "analysis_frame.align",
            "analysis_frame.compare",
        ),
        "inputs.frame": ("column.sum", "column.mean", "analysis_frame.coord"),
        "inputs.comparison": ("baseline", "ordinal", "offset"),
    }
    for group, targets in groups.items():
        owner = registry.by_canonical_id(group)
        assert isinstance(owner, NavigationInput)
        assert set(targets) <= set(owner.members)


@pytest.mark.parametrize(
    ("targets", "expected_targets", "exit_code"),
    (
        ((), ("root",), 0),
        (
            ("analysis.analysis_frame.select", "semantic.load"),
            ("analysis.analysis_frame.select", "semantic.load"),
            0,
        ),
        (
            ("analysis.analysis_frame.select", "unknown_cli_target", "semantic.load"),
            ("analysis.analysis_frame.select", "semantic.load"),
            2,
        ),
    ),
)
def test_cli_help_module_and_console_preserve_pages_and_exit_status(
    targets: tuple[str, ...],
    expected_targets: tuple[str, ...],
    exit_code: int,
    tmp_path: Path,
) -> None:
    results = [
        subprocess.run(
            [*command, "help", *targets],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=30,
        )
        for command in (
            [sys.executable, "-m", "marivo"],
            [str(ROOT / ".venv/bin/marivo")],
        )
    ]
    for result in results:
        assert result.returncode == exit_code, result.stderr
        assert re.findall(r"^Target: (.+)$", result.stdout, re.MULTILINE) == list(expected_targets)
        if exit_code:
            assert "not registered: received 'unknown_cli_target'" in result.stderr
            assert "Traceback" not in result.stderr
        else:
            assert result.stderr == ""
    assert results[0].stdout == results[1].stdout
    assert results[0].stderr == results[1].stderr
