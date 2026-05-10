"""Phase A gate: generated models validate all spec examples."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
OSI_EXAMPLES = ROOT / "osi-marivo-spec" / "examples"
AOI_EXAMPLES = ROOT / "aoi-spec" / "examples"


def _collect_json_files(base: Path) -> list[Path]:
    return sorted(base.rglob("*.json"))


def _load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    assert isinstance(data, dict)
    return data


@pytest.fixture(params=_collect_json_files(OSI_EXAMPLES), ids=lambda p: str(p.relative_to(ROOT)))
def osi_example(request: pytest.FixtureRequest) -> dict[str, Any]:
    return _load_json(request.param)


def test_osi_example_validates(osi_example: dict[str, Any]) -> None:
    from marivo.contracts.generated.osi import (
        OsiCoreMetadataSpecificationWithMarivoVendorExtensions as OSIDocument,
    )

    OSIDocument.model_validate(osi_example)


@pytest.fixture(params=_collect_json_files(AOI_EXAMPLES), ids=lambda p: str(p.relative_to(ROOT)))
def aoi_example(request: pytest.FixtureRequest) -> dict[str, Any]:
    return _load_json(request.param)


def test_aoi_example_validates(aoi_example: dict[str, Any]) -> None:
    from marivo.contracts.generated.aoi import AoiV01

    AoiV01.model_validate(aoi_example)


def test_version_constants_exist() -> None:
    from marivo.contracts.generated import AOI_SPEC_VERSION, OSI_MARIVO_SPEC_VERSION

    assert OSI_MARIVO_SPEC_VERSION == "0.1.1"
    assert AOI_SPEC_VERSION == "0.1.0"


def test_marivo_metric_extension_matches_spec() -> None:
    from marivo.transports.http.models.marivo_extensions import MarivoMetricExtension

    assert set(MarivoMetricExtension.model_fields) == {"additive_dimensions"}


def test_semantic_metrics_ddl_has_additive_dimensions() -> None:
    """DDL must have additive_dimensions and must not have legacy metric columns."""
    from marivo.adapters.schema import METADATA_DDL

    metrics_ddl = [
        stmt for stmt in METADATA_DDL if "semantic_metrics" in stmt and "CREATE TABLE" in stmt
    ]
    assert len(metrics_ddl) == 1
    ddl = metrics_ddl[0]
    assert "additive_dimensions" in ddl
    assert "observed_dataset" not in ddl
    assert "observation_grain" not in ddl
    assert "primary_time_field" not in ddl
    assert "additivity " not in ddl
    assert "filters " not in ddl


def test_malformed_extension_data_rejected() -> None:
    """E9: malformed JSON in MARIVO extension data field."""
    from marivo.core.semantic.extensions import extract_marivo_extension
    from marivo.transports.http.models.marivo_extensions import MarivoDatasetExtension

    class FakeExt:
        vendor_name = "MARIVO"
        data = "{not valid json"

    with pytest.raises(Exception):
        extract_marivo_extension([FakeExt()], MarivoDatasetExtension)
