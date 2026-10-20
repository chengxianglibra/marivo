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


def test_attribute_entry_has_one_concrete_typed_signature_and_no_alias() -> None:
    import inspect

    endpoint = inspect.signature(ma.AttributeEndpoint)
    assert tuple(endpoint.parameters) == ("metrics", "during", "where", "memberships")
    assert str(endpoint.parameters["where"].annotation) == "Deferred | None"
    assert str(endpoint.parameters["memberships"].annotation) == "tuple[Grouping, ...]"
    assert endpoint.parameters["memberships"].default == ()
    signature = inspect.signature(ma.Session.attribute)
    assert tuple(signature.parameters) == (
        "self",
        "left",
        "right",
        "by",
        "parents",
        "parent_on",
        "parent_how",
        "contribution",
    )
    assert str(signature.parameters["left"].annotation) == "AttributeEndpoint"
    assert str(signature.parameters["right"].annotation) == "AttributeEndpoint"
    assert str(signature.return_annotation) == "AnalysisFrame"
    assert signature.parameters["by"].kind is inspect.Parameter.KEYWORD_ONLY
    assert (
        str(signature.parameters["by"].annotation) == "tuple[Deferred | Grouping, ...] | Population"
    )
    assert "FrameAxis" in str(signature.parameters["parent_on"].annotation)
    assert signature.parameters["parents"].default is None
    assert signature.parameters["parent_on"].default == ()
    assert signature.parameters["parent_how"].default == "exact"
    assert signature.parameters["contribution"].default is False
    assert str(signature.parameters["contribution"].annotation) == "bool"
    assert not hasattr(ma.Session, "_attribute") and not hasattr(ma.AnalysisFrame, "attribute")
    for name in ("Classification", "ParentTarget", "MethodScalar", "SavedFrameMethodFacts"):
        assert name not in ma.__all__ and not hasattr(ma, name)
    doc = inspect.getdoc(ma.Session.attribute)
    assert doc is not None
    for section in ("Args:", "Returns:", "Example:", "Constraints:"):
        assert section in doc


def test_datasource_all_is_pinned() -> None:
    assert set(md.__all__) == DATASOURCE_PUBLIC
