"""Independent retirement, current-owner and archive-content rejection guards."""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

import marivo.analysis as mv
from marivo.analysis.materialization import contracts
from marivo.analysis.materialization.store import SessionStore
from scripts.r101_package_contents import (
    REQUIRED_PATHS,
    RETIRED_PATHS,
    source_contents,
    validate_contents,
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "name",
    (
        "Dataset",
        "LogicalDataset",
        "MaterializedDataset",
        "DatasetFieldRef",
        "DatasetContract",
        "AnalysisPredicate",
        "LogicalPopulationDataset",
        "MaterializedPopulationDataset",
        "LogicalMetricDataset",
        "MaterializedMetricDataset",
        "gt",
        "gte",
        "lt",
        "lte",
        "eq",
        "ne",
        "is_null",
        "not_null",
    ),
)
def test_retired_public_symbols_are_absent(name: str) -> None:
    assert name not in mv.__all__
    assert not hasattr(mv, name)


def test_retired_store_and_codec_entry_shapes_are_absent() -> None:
    assert "_generation" not in inspect.signature(SessionStore).parameters
    assert not hasattr(SessionStore, "_graph_store")
    assert not hasattr(mv.Session, "population")
    assert not hasattr(mv.Session, "observe")
    for name in ("ArtifactDescriptor", "RunDatasetInput", "decode_descriptor", "decode_run_input"):
        assert not hasattr(contracts, name)
    assert "MaterializedDataset" not in str(
        inspect.signature(mv.Session.artifact).return_annotation
    )


def test_retired_modules_have_no_current_product_imports_or_files() -> None:
    retired = {path[:-3].replace("/", ".") for path in RETIRED_PATHS}
    assert all(not (ROOT / path).exists() for path in RETIRED_PATHS)
    for path in (ROOT / "marivo").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                imports = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports = (node.module, *(node.module + "." + alias.name for alias in node.names))
            else:
                continue
            assert not retired.intersection(imports), path


def test_package_inventory_preserves_current_owners_and_rejects_retired_or_changed_bytes() -> None:
    expected = source_contents(ROOT)
    assert expected.keys() >= REQUIRED_PATHS
    validate_contents(expected, expected)
    for retired in RETIRED_PATHS:
        with pytest.raises(ValueError, match="retired="):
            validate_contents({**expected, retired: "stale"}, expected)
    for required in REQUIRED_PATHS:
        damaged = dict(expected)
        del damaged[required]
        with pytest.raises(ValueError, match="missing="):
            validate_contents(damaged, expected)
    with pytest.raises(ValueError, match="hashes"):
        validate_contents({**expected, "marivo/analysis/public_dsl.py": "changed"}, expected)
    with pytest.raises(ValueError, match="hashes"):
        validate_contents(
            {**expected, "marivo/skills/marivo-analysis/examples/old.py": "extra"}, expected
        )
