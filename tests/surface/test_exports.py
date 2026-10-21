"""Pin the public ``__all__`` of each marivo surface module.

Any added or removed public symbol must be a deliberate edit here.
"""

from __future__ import annotations

import json
import subprocess
import sys
import types

import pytest

import marivo.analysis as ma
import marivo.datasource as md
import marivo.semantic as ms
from tests.surface.exports import ANALYSIS_PUBLIC, DATASOURCE_PUBLIC, SEMANTIC_PUBLIC


@pytest.mark.parametrize(
    "module,names",
    (
        (
            md,
            (
                "PhysicalExtent",
                "Partitioning",
                "ExecutionCapabilities",
                "DatasourceFailure",
                "PartitionScope",
                "UnprunedScope",
            ),
        ),
        (
            ms,
            (
                "ReadinessInputSummary",
                "ReadinessIssue",
                "SourceHealthCheckResult",
                "AiContextValue",
            ),
        ),
        (
            ma,
            (
                "FrameMethodValue",
                "FrameMethodCoordinate",
                "FrameMethodSupport",
                "FrameAdditiveMethod",
                "FrameRatioMethod",
                "FrameLinearTerm",
                "FrameLinearMethod",
                "FrameMethodMetric",
                "FrameMethodParent",
            ),
        ),
    ),
)
def test_returned_records_do_not_leak_root_constructor_bindings(
    module: types.ModuleType, names: tuple[str, ...]
) -> None:
    for name in names:
        assert name not in module.__all__
        assert name not in dir(module)
        assert not hasattr(module, name)


def test_partition_scope_factory_signature_is_stable() -> None:
    import inspect

    import marivo.datasource as md

    assert tuple(inspect.signature(md.partition).parameters) == (
        "values",
        "max_rows",
        "timeout_seconds",
    )
    scope = md.partition({"dt": "2026-10-21"}, max_rows=10, timeout_seconds=2)
    assert scope.values == (("dt", "2026-10-21"),)
    assert (scope.max_rows, scope.timeout_seconds) == (10, 2)
    assert not hasattr(md, "PartitionScope")
    assert not hasattr(md, "UnprunedScope")


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


def test_attribute_entry_has_one_concrete_typed_signature_and_no_alias() -> None:
    import inspect

    signature = inspect.signature(ma.AnalysisFrame.attribute)
    assert tuple(signature.parameters) == ("self", "by")
    assert signature.parameters["by"].kind is inspect.Parameter.KEYWORD_ONLY
    assert (
        str(signature.parameters["by"].annotation) == "tuple[Deferred | Grouping, ...] | Population"
    )
    assert str(signature.return_annotation) == "AnalysisFrame"
    assert not hasattr(ma.Session, "attribute")
    for name in (
        "AttributeEndpoint",
        "attribute_endpoint",
        "Classification",
        "ParentTarget",
        "MethodScalar",
        "SavedFrameMethodFacts",
    ):
        assert name not in ma.__all__ and not hasattr(ma, name)
    doc = inspect.getdoc(ma.AnalysisFrame.attribute)
    assert doc is not None
    for section in ("Args:", "Returns:", "Example:", "Constraints:"):
        assert section in doc


def test_datasource_all_is_pinned() -> None:
    assert set(md.__all__) == DATASOURCE_PUBLIC
