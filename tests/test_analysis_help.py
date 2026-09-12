"""Public Dataset Help preserves environment, native examples and hard budgets."""

import ast
import sys
from pathlib import Path

import marivo
from marivo._help.render import render_help_text
from marivo.analysis._capabilities.dataset_model import NavigationInput
from marivo.analysis._capabilities.model import ReadCapability
from marivo.analysis._capabilities.registry import REGISTRY
from marivo.introspection.live.model import SURFACE_LIMITS


def test_root_environment_fingerprint_is_current_and_resolved() -> None:
    text = render_help_text("analysis")[0]
    assert text.splitlines()[:3] == [
        f"Marivo: {marivo.__version__}",
        f"Python: {sys.executable}",
        f"Package: {Path(marivo.__file__).resolve()}",
    ]
    assert len(text.splitlines()) <= SURFACE_LIMITS.root_help_max_lines
    assert len(text) <= SURFACE_LIMITS.root_help_max_codepoints


def test_root_routes_are_the_six_native_hubs() -> None:
    root = REGISTRY.by_canonical_id("")
    assert isinstance(root, NavigationInput)
    assert root.members == ("entry", "methods", "inputs", "artifacts", "evidence", "runtime")
    text = render_help_text("analysis")[0]
    for target in root.members:
        assert f"marivo.help('analysis.{target}')" in text
    assert "session.compare(" not in text
    assert "to_pandas(" not in text


def test_catalog_reads_have_owner_examples_and_reflected_signatures() -> None:
    for descriptor in REGISTRY.descriptors:
        if not isinstance(descriptor, ReadCapability):
            continue
        assert descriptor.example
        ast.parse(descriptor.example)
        text = render_help_text("analysis." + descriptor.canonical_id)[0]
        assert descriptor.example in text
        assert "Requires:" in text
        if descriptor.receiver_family != "SemanticCatalog" or descriptor.canonical_id in (
            "catalog.require",
            "catalog.readiness",
        ):
            assert "Signature:" in text
