"""Phase A gate: generated models validate all spec examples."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

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
    from marivo.contracts.generated.aoi import AoiV02

    AoiV02.model_validate(aoi_example)


def test_version_constants_exist() -> None:
    from marivo.contracts.generated import AOI_SPEC_VERSION, OSI_MARIVO_SPEC_VERSION

    assert OSI_MARIVO_SPEC_VERSION == "0.1.1"
    assert AOI_SPEC_VERSION == "0.2.0"


def test_marivo_metric_extension_matches_spec() -> None:
    from marivo.transports.http.models.marivo_extensions import MarivoMetricExtension

    assert set(MarivoMetricExtension.model_fields) == {
        "additive_dimensions",
        "aggregation_semantics",
    }


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


def test_aoi_timescope_requires_field() -> None:
    """AOI TimeScope must require a non-empty field."""
    from marivo.contracts.generated.aoi import TimeScope

    ts = TimeScope(
        field="order_date",
        start="2024-01-01T00:00:00Z",
        end="2024-02-01T00:00:00Z",
    )
    assert ts.field == "order_date"

    with pytest.raises(ValidationError):
        TimeScope(start="2024-01-01T00:00:00Z", end="2024-02-01T00:00:00Z")

    with pytest.raises(ValidationError):
        TimeScope(
            field="",
            start="2024-01-01T00:00:00Z",
            end="2024-02-01T00:00:00Z",
        )


def test_aoi_request_optional_fields_may_be_omitted() -> None:
    from marivo.contracts.generated import aoi

    time_scope = {
        "field": "event_time",
        "start": "2026-01-01T00:00:00Z",
        "end": "2026-01-02T00:00:00Z",
    }

    aoi.Observe1.model_validate({"metric": "revenue", "time_scope": time_scope})
    aoi.Detect.model_validate(
        {
            "metric": "revenue",
            "time_scope": time_scope,
            "granularity": "day",
            "strategy": "point_anomaly",
        }
    )
    aoi.Test.model_validate(
        {
            "metric": "revenue",
            "left": {"time_scope": time_scope},
            "right": {"time_scope": time_scope},
            "kind": "numeric",
            "hypothesis": {
                "family": "two_sample_mean",
                "alternative": "two_sided",
                "significance": "balanced",
            },
        }
    )
    aoi.Compare.model_validate(
        {"left_artifact_id": "artifact_left", "right_artifact_id": "artifact_right"}
    )
    aoi.Decompose.model_validate({"compare_artifact_id": "artifact_compare", "dimension": "region"})
    aoi.Correlate.model_validate(
        {"left_artifact_id": "artifact_left", "right_artifact_id": "artifact_right"}
    )
    aoi.Forecast.model_validate({"source_artifact_id": "artifact_source", "horizon": 7})
    aoi.Validate.model_validate(
        {
            "metric": "revenue",
            "left": {"time_scope": time_scope},
            "right": {"time_scope": time_scope},
            "hypothesis": {
                "family": "two_sample_mean",
                "alternative": "two_sided",
                "significance": "balanced",
            },
        }
    )


@pytest.mark.parametrize(
    ("model_name", "payload"),
    [
        (
            "Observe1",
            {
                "metric": "revenue",
                "time_scope": {
                    "field": "event_time",
                    "start": "2026-01-01T00:00:00Z",
                    "end": "2026-01-02T00:00:00Z",
                },
                "filter": None,
            },
        ),
        (
            "Observe1",
            {
                "metric": "revenue",
                "time_scope": {
                    "field": "event_time",
                    "start": "2026-01-01T00:00:00Z",
                    "end": "2026-01-02T00:00:00Z",
                },
                "granularity": None,
            },
        ),
        (
            "Detect",
            {
                "metric": "revenue",
                "time_scope": {
                    "field": "event_time",
                    "start": "2026-01-01T00:00:00Z",
                    "end": "2026-01-02T00:00:00Z",
                },
                "granularity": "day",
                "filter": None,
                "strategy": "point_anomaly",
            },
        ),
        (
            "Compare",
            {
                "left_artifact_id": "artifact_left",
                "right_artifact_id": "artifact_right",
                "compare_type": None,
            },
        ),
        (
            "Decompose",
            {"compare_artifact_id": "artifact_compare", "dimension": "region", "limit": None},
        ),
        (
            "Correlate",
            {
                "left_artifact_id": "artifact_left",
                "right_artifact_id": "artifact_right",
                "method": None,
            },
        ),
        ("Forecast", {"source_artifact_id": "artifact_source", "horizon": 7, "profile": None}),
        (
            "Slice",
            {
                "time_scope": {
                    "field": "event_time",
                    "start": "2026-01-01T00:00:00Z",
                    "end": "2026-01-02T00:00:00Z",
                },
                "filter": None,
            },
        ),
        (
            "Validate",
            {
                "metric": "revenue",
                "left": {
                    "time_scope": {
                        "field": "event_time",
                        "start": "2026-01-01T00:00:00Z",
                        "end": "2026-01-02T00:00:00Z",
                    },
                    "filter": None,
                },
                "right": {
                    "time_scope": {
                        "field": "event_time",
                        "start": "2026-01-01T00:00:00Z",
                        "end": "2026-01-02T00:00:00Z",
                    }
                },
                "hypothesis": {
                    "family": "two_sample_mean",
                    "alternative": "two_sided",
                    "significance": "balanced",
                },
            },
        ),
    ],
)
def test_aoi_request_optional_fields_reject_explicit_null(
    model_name: str,
    payload: dict[str, Any],
) -> None:
    from marivo.contracts.generated import aoi

    model = getattr(aoi, model_name)

    with pytest.raises(ValidationError):
        model.model_validate(payload)


@pytest.mark.parametrize(
    "payload",
    [
        {
            "metric": "revenue",
            "left": {
                "time_scope": {
                    "field": "event_time",
                    "start": "2026-01-01T00:00:00Z",
                    "end": "2026-01-02T00:00:00Z",
                },
                "scope": {"constraints": {"region": "US"}},
            },
            "right": {
                "time_scope": {
                    "field": "event_time",
                    "start": "2026-01-01T00:00:00Z",
                    "end": "2026-01-02T00:00:00Z",
                }
            },
            "hypothesis": {
                "family": "two_sample_mean",
                "alternative": "two_sided",
                "significance": "balanced",
            },
        },
        {
            "metric": "revenue",
            "left": {
                "time_scope": {
                    "field": "event_time",
                    "start": "2026-01-01T00:00:00Z",
                    "end": "2026-01-02T00:00:00Z",
                }
            },
            "right": {
                "time_scope": {
                    "field": "event_time",
                    "start": "2026-01-01T00:00:00Z",
                    "end": "2026-01-02T00:00:00Z",
                }
            },
            "hypothesis": {
                "family": "two_sample_mean",
                "alternative": "two_sided",
                "significance": "balanced",
            },
            "method": "welch_t",
        },
        {
            "metric": "revenue",
            "left": {
                "time_scope": {
                    "field": "event_time",
                    "start": "2026-01-01T00:00:00Z",
                    "end": "2026-01-02T00:00:00Z",
                }
            },
            "right": {
                "time_scope": {
                    "field": "event_time",
                    "start": "2026-01-01T00:00:00Z",
                    "end": "2026-01-02T00:00:00Z",
                }
            },
        },
    ],
)
def test_aoi_validate_rejects_non_contract_fields(payload: dict[str, Any]) -> None:
    from marivo.contracts.generated import aoi

    with pytest.raises(ValidationError):
        aoi.Validate.model_validate(payload)


def test_aoi_result_nullable_fields_still_accept_explicit_null() -> None:
    from marivo.contracts.generated import aoi

    aoi.ScalarObservationResult.model_validate({"value": None})
    aoi.ScalarDeltaResult.model_validate(
        {
            "left_value": None,
            "right_value": None,
            "delta": None,
            "matched_time_scope": None,
        }
    )
    aoi.AssociationResult.model_validate(
        {"coefficient": 0.2, "p_value": None, "n_pairs": 10, "matched_time_scope": None}
    )
    aoi.HypothesisTestResult.model_validate(
        {
            "statistic": 1.2,
            "p_value": 0.04,
            "decision": {"reject_null": None},
            "assumption_notes": [],
        }
    )


def test_additive_dimensions_validation() -> None:
    """additive_dimensions defaults to empty list; empty list means non-additive."""
    from marivo.transports.http.models.marivo_extensions import MarivoMetricExtension

    ext = MarivoMetricExtension(additive_dimensions=["region", "channel"])
    assert ext.additive_dimensions == ["region", "channel"]

    ext = MarivoMetricExtension()
    assert ext.additive_dimensions == []

    ext = MarivoMetricExtension(additive_dimensions=[])
    assert ext.additive_dimensions == []
