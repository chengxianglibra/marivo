"""Pin the public ``__all__`` of each marivo surface module.

Any added or removed public symbol must be a deliberate edit here.
"""

from __future__ import annotations

import json
import subprocess
import sys

import marivo.analysis as ma
import marivo.datasource as md
import marivo.semantic as ms
from tests.surface.exports import ANALYSIS_PUBLIC, DATASOURCE_PUBLIC, SEMANTIC_PUBLIC


def test_partition_scope_constructor_signature_is_stable() -> None:
    import inspect

    import marivo.datasource as md

    assert str(inspect.signature(md.PartitionScope)) == (
        "(values: 'tuple[tuple[str, str], ...]', max_rows: 'int', timeout_seconds: 'int') -> None"
    )


def test_top_level_package_does_not_add_public_convenience_exports() -> None:
    script = (
        "import json, marivo; "
        "print(json.dumps(sorted(name for name in dir(marivo) "
        "if name == '__version__' or not name.startswith('_'))))"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == ["__version__", "help"]


def test_semantic_all_is_pinned() -> None:
    assert set(ms.__all__) == SEMANTIC_PUBLIC


def test_analysis_all_is_pinned() -> None:
    assert tuple(ma.__all__) == ANALYSIS_PUBLIC
    assert set(dir(ma)) == set(ANALYSIS_PUBLIC)


def test_datasource_all_is_pinned() -> None:
    assert set(md.__all__) == DATASOURCE_PUBLIC
