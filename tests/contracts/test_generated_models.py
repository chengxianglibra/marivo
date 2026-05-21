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
REPO_ROOT = Path(__file__).resolve().parents[2]


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


def _aoi_time_scope() -> dict[str, str]:
    return {
        "field": "event_time",
        "start": "2026-05-01T00:00:00Z",
        "end": "2026-05-08T00:00:00Z",
    }


def test_aoi_observe_accepts_scalar_branch() -> None:
    from marivo.contracts.generated import aoi

    request = aoi.Observe.model_validate(
        {
            "metric": "metric.revenue",
            "time_scope": _aoi_time_scope(),
        }
    )

    assert request.metric == "metric.revenue"
    assert request.time_scope.field == "event_time"


@pytest.mark.parametrize("granularity", ["hour", "day", "week", "month", "quarter", "year"])
def test_aoi_observe_accepts_time_series_branch(granularity: str) -> None:
    from marivo.contracts.generated import aoi

    request = aoi.Observe.model_validate(
        {
            "metric": "metric.revenue",
            "time_scope": _aoi_time_scope(),
            "granularity": granularity,
        }
    )

    assert request.granularity == granularity


def test_aoi_observe_accepts_segmented_branch() -> None:
    from marivo.contracts.generated import aoi

    request = aoi.Observe.model_validate(
        {
            "metric": "metric.revenue",
            "time_scope": _aoi_time_scope(),
            "dimensions": ["region", "platform"],
        }
    )

    assert [dimension.root for dimension in request.dimensions] == ["region", "platform"]


def test_aoi_observe_preserves_filter_expression() -> None:
    from marivo.contracts.generated import aoi

    request = aoi.Observe.model_validate(
        {
            "metric": "metric.revenue",
            "time_scope": _aoi_time_scope(),
            "filter": {
                "dialects": [
                    {"dialect": "ANSI_SQL", "expression": "region = 'US'"},
                ]
            },
        }
    )

    assert request.filter is not None
    assert request.filter.model_dump(exclude_none=True) == {
        "dialects": [{"dialect": "ANSI_SQL", "expression": "region = 'US'"}]
    }


@pytest.mark.parametrize(
    "payload",
    [
        {
            "metric": "metric.revenue",
            "time_scope": _aoi_time_scope(),
            "unexpected": True,
        },
        {
            "metric": "metric.revenue",
            "time_scope": _aoi_time_scope(),
            "dimensions": [],
        },
        {"time_scope": _aoi_time_scope()},
        {"metric": "metric.revenue"},
    ],
)
def test_aoi_observe_rejects_invalid_contract_shapes(payload: dict[str, Any]) -> None:
    from marivo.contracts.generated import aoi

    with pytest.raises(ValidationError):
        aoi.Observe.model_validate(payload)


@pytest.mark.parametrize(
    "payload",
    [
        {
            "metric": "metric.revenue",
            "time_scope": _aoi_time_scope(),
        },
        {
            "metric": "metric.revenue",
            "time_scope": _aoi_time_scope(),
            "granularity": "day",
        },
        {
            "metric": "metric.revenue",
            "time_scope": _aoi_time_scope(),
            "dimensions": ["region"],
        },
        {
            "metric": "metric.revenue",
            "time_scope": _aoi_time_scope(),
            "granularity": "day",
            "dimensions": ["region"],
        },
    ],
)
def test_aoi_observe_accepts_valid_optional_combinations(payload: dict[str, Any]) -> None:
    from marivo.contracts.generated import aoi

    aoi.Observe.model_validate(payload)


def test_marivo_metric_extension_matches_spec() -> None:
    from marivo.transports.http.models.marivo_extensions import MarivoMetricExtension

    assert set(MarivoMetricExtension.model_fields) == {
        "decomposition_semantics",
    }
    # numerator/denominator/weight are now nested inside decomposition_semantics variants,
    # not top-level fields on MarivoMetricExtension.
    assert "numerator" not in MarivoMetricExtension.model_fields
    assert "denominator" not in MarivoMetricExtension.model_fields
    assert "weight" not in MarivoMetricExtension.model_fields


def test_decomposition_semantics_does_not_generate_named_enum() -> None:
    from marivo.contracts.generated import osi

    assert not hasattr(osi, "DecompositionSemantics")


def test_generated_metric_extension_accepts_sum_aggregation_object() -> None:
    from marivo.contracts.generated.osi import MarivoMetricExtension

    ext = MarivoMetricExtension.model_validate({"decomposition_semantics": {"type": "sum"}})

    assert ext.decomposition_semantics.type == "sum"
    dumped = ext.model_dump(mode="json")
    assert dumped["decomposition_semantics"]["type"] == "sum"


def test_generated_metric_extension_rejects_ratio_without_components() -> None:
    from marivo.contracts.generated.osi import MarivoMetricExtension

    with pytest.raises(ValidationError):
        MarivoMetricExtension.model_validate({"decomposition_semantics": {"type": "ratio"}})


def test_generated_metric_extension_rejects_weighted_average_without_components() -> None:
    from marivo.contracts.generated.osi import MarivoMetricExtension

    with pytest.raises(ValidationError):
        MarivoMetricExtension.model_validate(
            {"decomposition_semantics": {"type": "weighted_average"}}
        )


def test_generated_metric_extension_defaults_to_sum_object() -> None:
    from marivo.contracts.generated.osi import MarivoMetricExtension

    ext = MarivoMetricExtension()
    assert ext.decomposition_semantics.type == "sum"
    dumped = ext.model_dump(mode="json")
    assert dumped["decomposition_semantics"] == {"type": "sum"}


def test_generated_metric_extension_rejects_invalid_aggregation_type() -> None:
    from marivo.contracts.generated.osi import MarivoMetricExtension

    with pytest.raises(ValidationError):
        MarivoMetricExtension.model_validate({"decomposition_semantics": {"type": "average"}})


def test_generated_metric_extension_rejects_flat_string_decomposition_semantics() -> None:
    from marivo.contracts.generated.osi import MarivoMetricExtension

    with pytest.raises(ValidationError):
        MarivoMetricExtension.model_validate({"decomposition_semantics": "sum"})


def test_handwritten_metric_extension_accepts_sum_aggregation() -> None:
    from marivo.contracts.semantic_extensions import MarivoMetricExtension, SumDecomposition

    ext = MarivoMetricExtension(decomposition_semantics=SumDecomposition())
    assert ext.decomposition_semantics.type == "sum"
    assert ext.model_dump(mode="json")["decomposition_semantics"] == {"type": "sum"}


def test_handwritten_metric_extension_accepts_ratio_aggregation() -> None:
    from marivo.contracts.semantic_extensions import (
        MarivoMetricExtension,
        MetricComponentRef,
        RatioDecomposition,
    )

    ext = MarivoMetricExtension(
        decomposition_semantics=RatioDecomposition(
            numerator=MetricComponentRef(metric="metric.converted"),
            denominator=MetricComponentRef(metric="metric.total"),
        )
    )
    assert ext.decomposition_semantics.type == "ratio"
    assert ext.decomposition_semantics.numerator.metric == "metric.converted"
    assert ext.decomposition_semantics.denominator.metric == "metric.total"
    dumped = ext.model_dump(mode="json")["decomposition_semantics"]
    assert dumped["type"] == "ratio"
    assert dumped["numerator"]["metric"] == "metric.converted"
    assert dumped["denominator"]["metric"] == "metric.total"


def test_handwritten_metric_extension_accepts_weighted_average_aggregation() -> None:
    from marivo.contracts.semantic_extensions import (
        MarivoMetricExtension,
        MetricComponentRef,
        WeightedAverageDecomposition,
    )

    ext = MarivoMetricExtension(
        decomposition_semantics=WeightedAverageDecomposition(
            numerator=MetricComponentRef(metric="metric.gmv"),
            weight=MetricComponentRef(metric="metric.orders"),
        )
    )
    assert ext.decomposition_semantics.type == "weighted_average"
    assert ext.decomposition_semantics.numerator.metric == "metric.gmv"
    assert ext.decomposition_semantics.weight.metric == "metric.orders"
    dumped = ext.model_dump(mode="json")["decomposition_semantics"]
    assert dumped["type"] == "weighted_average"
    assert dumped["numerator"]["metric"] == "metric.gmv"
    assert dumped["weight"]["metric"] == "metric.orders"


def test_handwritten_metric_extension_defaults_to_sum() -> None:
    from marivo.contracts.semantic_extensions import MarivoMetricExtension

    ext = MarivoMetricExtension()
    assert ext.decomposition_semantics.type == "sum"
    assert ext.model_dump(mode="json")["decomposition_semantics"] == {"type": "sum"}


def test_handwritten_metric_extension_rejects_invalid_aggregation_type() -> None:
    from marivo.contracts.semantic_extensions import MarivoMetricExtension

    with pytest.raises(ValidationError):
        MarivoMetricExtension(decomposition_semantics={"type": "average"})


def test_marivo_metric_extension_retired_fields_not_model_fields() -> None:
    """Retired fields are not model fields. Since generated OSI MarivoMetricExtension
    uses extra='allow', retired fields go into __pydantic_extra__ rather than raising
    ValidationError. Verify they are not recognized model fields."""
    from marivo.contracts.generated.osi import MarivoMetricExtension

    assert "observed_dataset" not in MarivoMetricExtension.model_fields
    assert "observation_grain" not in MarivoMetricExtension.model_fields
    assert "primary_time_field" not in MarivoMetricExtension.model_fields

    # They go into extras rather than raising errors
    ext = MarivoMetricExtension.model_validate(
        {
            "observed_dataset": "orders",
            "observation_grain": ["day"],
            "primary_time_field": "order_date",
        }
    )
    assert ext.decomposition_semantics.type == "sum"


def test_generated_osi_has_no_retired_metric_extension_types() -> None:
    from marivo.contracts.generated import osi

    assert not hasattr(osi, "ObservationGrainItem")
    assert "observed_dataset" not in osi.MarivoMetricExtension.model_fields
    assert "observation_grain" not in osi.MarivoMetricExtension.model_fields
    assert "primary_time_field" not in osi.MarivoMetricExtension.model_fields
    # numerator/denominator/weight are now nested inside decomposition_semantics, not top-level
    assert "numerator" not in osi.MarivoMetricExtension.model_fields
    assert "denominator" not in osi.MarivoMetricExtension.model_fields
    assert "weight" not in osi.MarivoMetricExtension.model_fields
    # decomposition_semantics IS a top-level field (polymorphic object)
    assert "decomposition_semantics" in osi.MarivoMetricExtension.model_fields


def test_semantic_metrics_ddl_has_component_ref_columns() -> None:
    """DDL must have numerator/denominator/weight columns and no additive_dimensions or retired columns."""
    from marivo.adapters.schema import METADATA_DDL

    metrics_ddl = [
        stmt for stmt in METADATA_DDL if "semantic_metrics" in stmt and "CREATE TABLE" in stmt
    ]
    assert len(metrics_ddl) == 1
    ddl = metrics_ddl[0]
    assert "additive_dimensions" not in ddl
    assert "numerator" in ddl
    assert "denominator" in ddl
    assert "weight" in ddl
    assert "observed_dataset" not in ddl
    assert "observation_grain" not in ddl
    assert "primary_time_field" not in ddl


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

    aoi.Observe.model_validate({"metric": "revenue", "time_scope": time_scope})
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
            "current": {"time_scope": time_scope},
            "baseline": {"time_scope": time_scope},
            "grain": "day",
            "kind": "numeric",
            "hypothesis": {
                "family": "two_sample_mean",
                "alternative": "two_sided",
                "significance": "balanced",
            },
        }
    )
    aoi.Compare.model_validate(
        {"current_artifact_id": "artifact_left", "baseline_artifact_id": "artifact_right"}
    )
    aoi.Decompose.model_validate({"compare_artifact_id": "artifact_compare", "dimension": "region"})
    aoi.Correlate.model_validate(
        {"left_artifact_id": "artifact_left", "right_artifact_id": "artifact_right"}
    )
    aoi.Forecast.model_validate({"source_artifact_id": "artifact_source", "horizon": 7})
    aoi.Validate.model_validate(
        {
            "metric": "revenue",
            "current": {"time_scope": time_scope},
            "baseline": {"time_scope": time_scope},
            "grain": "day",
            "hypothesis": {
                "family": "two_sample_mean",
                "alternative": "two_sided",
                "significance": "balanced",
            },
        }
    )
    attribute = aoi.Attribute.model_validate(
        {
            "metric": "revenue",
            "current": {"time_scope": time_scope},
            "baseline": {"time_scope": time_scope},
            "dimensions": ["region"],
        }
    )
    assert attribute.decomposition_method == "delta_share"
    assert attribute.decomposition_limit == 5
    diagnose = aoi.Diagnose.model_validate(
        {
            "metric": "revenue",
            "time_scope": time_scope,
            "granularity": "day",
            "dimensions": ["region"],
            "strategy": "point_anomaly",
        }
    )
    assert diagnose.sensitivity == "aggressive"
    assert diagnose.candidate_limit == 3
    assert diagnose.decomposition_limit == 5


def _aoi_test_payload() -> dict[str, Any]:
    return {
        "metric": "revenue",
        "current": {"time_scope": _aoi_time_scope()},
        "baseline": {"time_scope": _aoi_time_scope()},
        "grain": "day",
        "kind": "numeric",
        "hypothesis": {
            "family": "two_sample_mean",
            "alternative": "two_sided",
            "significance": "balanced",
        },
    }


def test_aoi_attribute_accepts_required_only_shape_with_defaults() -> None:
    from marivo.contracts.generated import aoi

    request = aoi.Attribute.model_validate(
        {
            "metric": "revenue",
            "current": {"time_scope": _aoi_time_scope()},
            "baseline": {"time_scope": _aoi_time_scope()},
            "dimensions": ["region"],
        }
    )

    assert request.metric == "revenue"
    assert request.decomposition_method == "delta_share"
    assert request.decomposition_limit == 5


def test_aoi_attribute_accepts_explicit_options_and_slice_filter() -> None:
    from marivo.contracts.generated import aoi

    request = aoi.Attribute.model_validate(
        {
            "metric": "revenue",
            "current": {
                "time_scope": _aoi_time_scope(),
                "filter": {"dialects": [{"dialect": "ANSI_SQL", "expression": "region = 'US'"}]},
            },
            "baseline": {"time_scope": _aoi_time_scope()},
            "dimensions": ["region", "channel"],
            "decomposition_method": "delta_share",
            "decomposition_limit": 10,
        }
    )

    assert request.current.filter is not None
    assert request.current.filter.model_dump(exclude_none=True) == {
        "dialects": [{"dialect": "ANSI_SQL", "expression": "region = 'US'"}]
    }
    assert [dimension.root for dimension in request.dimensions] == ["region", "channel"]
    assert request.decomposition_method == "delta_share"
    assert request.decomposition_limit == 10


@pytest.mark.parametrize("alternative", ["two_sided", "greater", "less"])
@pytest.mark.parametrize("significance", ["conservative", "balanced", "aggressive"])
def test_aoi_test_accepts_all_public_options(alternative: str, significance: str) -> None:
    from marivo.contracts.generated import aoi

    payload = _aoi_test_payload()
    payload["current"]["filter"] = {
        "dialects": [{"dialect": "ANSI_SQL", "expression": "region = 'US'"}]
    }
    payload["hypothesis"]["alternative"] = alternative
    payload["hypothesis"]["significance"] = significance

    request = aoi.Test.model_validate(payload)

    assert request.kind == "numeric"
    assert request.grain == "day"
    assert request.hypothesis.family == "two_sample_mean"
    assert request.hypothesis.alternative == alternative
    assert request.hypothesis.significance == significance
    assert request.current.filter is not None
    assert request.current.filter.model_dump(exclude_none=True) == {
        "dialects": [{"dialect": "ANSI_SQL", "expression": "region = 'US'"}]
    }


def test_aoi_test_omits_absent_optional_filter_fields() -> None:
    from marivo.contracts.generated import aoi

    request = aoi.Test.model_validate(_aoi_test_payload())

    dumped = request.model_dump(exclude_none=True)
    assert "filter" not in dumped["current"]
    assert "filter" not in dumped["baseline"]


@pytest.mark.parametrize("grain", ["hour", "day", "week", "month", "quarter", "year"])
def test_aoi_test_accepts_time_granularity_grain(grain: str) -> None:
    from marivo.contracts.generated import aoi

    payload = _aoi_test_payload()
    payload["grain"] = grain

    request = aoi.Test.model_validate(payload)

    assert request.grain == grain


@pytest.mark.parametrize("grain", ["hour", "day", "week", "month", "quarter", "year"])
def test_aoi_validate_accepts_time_granularity_grain(grain: str) -> None:
    from marivo.contracts.generated import aoi

    request = aoi.Validate.model_validate(
        {
            "metric": "revenue",
            "current": {"time_scope": _aoi_time_scope()},
            "baseline": {"time_scope": _aoi_time_scope()},
            "grain": grain,
            "hypothesis": {
                "family": "two_sample_mean",
                "alternative": "two_sided",
                "significance": "balanced",
            },
        }
    )

    assert request.grain == grain


def test_aoi_grain_uses_time_granularity_directly() -> None:
    schema = _load_json(REPO_ROOT / "aoi-spec" / "schema" / "aoi.schema.json")
    primitives = schema["$defs"]["primitives"]

    assert "SampleGrain" not in primitives
    assert schema["$defs"]["requests"]["test"]["properties"]["grain"] == {
        "$ref": "#/$defs/primitives/TimeGranularity"
    }
    assert schema["$defs"]["derived_requests"]["validate"]["properties"]["grain"] == {
        "$ref": "#/$defs/primitives/TimeGranularity"
    }


@pytest.mark.parametrize(
    "missing_field", ["metric", "current", "baseline", "grain", "kind", "hypothesis"]
)
def test_aoi_test_requires_public_required_fields(missing_field: str) -> None:
    from marivo.contracts.generated import aoi

    payload = _aoi_test_payload()
    payload.pop(missing_field)

    with pytest.raises(ValidationError):
        aoi.Test.model_validate(payload)


@pytest.mark.parametrize("missing_field", ["family", "alternative", "significance"])
def test_aoi_test_requires_hypothesis_fields(missing_field: str) -> None:
    from marivo.contracts.generated import aoi

    payload = _aoi_test_payload()
    payload["hypothesis"].pop(missing_field)

    with pytest.raises(ValidationError):
        aoi.Test.model_validate(payload)


@pytest.mark.parametrize(
    "payload_patch",
    [
        {"kind": "rate"},
        {"kind": "Numeric"},
        {"grain": "minute"},
        {"grain": None},
        {"method": "welch_t"},
        {"current": {"scope": {"constraints": {"region": "US"}}}},
        {"current": {"filter": None}},
        {"hypothesis": {"family": "two_sample_proportion"}},
        {"hypothesis": {"alternative": "not_equal"}},
        {"hypothesis": {"significance": "loose"}},
        {"hypothesis": {"alpha": 0.05}},
        {"hypothesis": {"family": None}},
        {"hypothesis": {"alternative": None}},
        {"hypothesis": {"significance": None}},
    ],
)
def test_aoi_test_rejects_invalid_contract_fields(payload_patch: dict[str, Any]) -> None:
    from marivo.contracts.generated import aoi

    payload = _aoi_test_payload()
    _deep_update(payload, payload_patch)

    with pytest.raises(ValidationError):
        aoi.Test.model_validate(payload)


def _deep_update(target: dict[str, Any], patch_value: dict[str, Any]) -> None:
    for key, value in patch_value.items():
        nested = target.get(key)
        if isinstance(value, dict) and isinstance(nested, dict):
            _deep_update(nested, value)
        else:
            target[key] = value


@pytest.mark.parametrize("strategy", ["point_anomaly", "period_shift"])
@pytest.mark.parametrize("sensitivity", ["conservative", "balanced", "aggressive"])
@pytest.mark.parametrize("granularity", ["hour", "day", "week", "month", "quarter", "year"])
def test_aoi_detect_accepts_all_public_options(
    strategy: str,
    sensitivity: str,
    granularity: str,
) -> None:
    from marivo.contracts.generated import aoi

    request = aoi.Detect.model_validate(
        {
            "metric": "revenue",
            "time_scope": _aoi_time_scope(),
            "granularity": granularity,
            "filter": {
                "dialects": [
                    {"dialect": "ANSI_SQL", "expression": "region = 'US'"},
                ]
            },
            "dimension": "region",
            "strategy": strategy,
            "sensitivity": sensitivity,
            "limit": 10,
        }
    )

    assert request.granularity == granularity
    assert request.strategy == strategy
    assert request.sensitivity == sensitivity
    assert request.filter is not None
    assert request.dimension == "region"
    assert request.limit == 10


def test_aoi_detect_defaults_omitted_optional_fields() -> None:
    from marivo.contracts.generated import aoi

    request = aoi.Detect.model_validate(
        {
            "metric": "revenue",
            "time_scope": _aoi_time_scope(),
            "granularity": "day",
            "strategy": "point_anomaly",
        }
    )

    assert request.sensitivity == "aggressive"
    dumped = request.model_dump(exclude_none=True)
    assert "filter" not in dumped
    assert "dimension" not in dumped
    assert "limit" not in dumped


def test_aoi_decompose_accepts_public_options() -> None:
    from marivo.contracts.generated import aoi

    request = aoi.Decompose.model_validate(
        {
            "compare_artifact_id": "artifact_compare",
            "dimension": "region",
            "limit": 10,
        }
    )

    assert request.compare_artifact_id == "artifact_compare"
    assert request.dimension == "region"
    assert request.limit == 10


def test_aoi_decompose_defaults_omitted_optional_fields() -> None:
    from marivo.contracts.generated import aoi

    request = aoi.Decompose.model_validate(
        {"compare_artifact_id": "artifact_compare", "dimension": "region"}
    )

    dumped = request.model_dump(exclude_none=True)
    assert dumped == {"compare_artifact_id": "artifact_compare", "dimension": "region"}


@pytest.mark.parametrize(
    "payload_patch",
    [
        {"compare_artifact_id": ""},
        {"dimension": ""},
        {"limit": 0},
        {"limit": -1},
        {"compare_ref": {"step_id": "step_compare"}},
        {"method": "delta_share"},
        {"scope": {"predicate": "region = 'US'"}},
    ],
)
def test_aoi_decompose_rejects_invalid_contract_fields(payload_patch: dict[str, Any]) -> None:
    from marivo.contracts.generated import aoi

    payload = {"compare_artifact_id": "artifact_compare", "dimension": "region"}
    payload.update(payload_patch)

    with pytest.raises(ValidationError):
        aoi.Decompose.model_validate(payload)


@pytest.mark.parametrize("missing_field", ["compare_artifact_id", "dimension"])
def test_aoi_decompose_requires_public_required_fields(missing_field: str) -> None:
    from marivo.contracts.generated import aoi

    payload = {"compare_artifact_id": "artifact_compare", "dimension": "region"}
    payload.pop(missing_field)

    with pytest.raises(ValidationError):
        aoi.Decompose.model_validate(payload)


@pytest.mark.parametrize(
    "payload_patch",
    [
        {"metric": ""},
        {"granularity": "minute"},
        {"strategy": "zscore_raw"},
        {"sensitivity": "extreme"},
        {"limit": 0},
        {"limit": -1},
        {"dimension": ""},
        {"scope": {"predicate": "region = 'US'"}},
        {"split_by": ["region"]},
        {"profile": "auto"},
        {"max_series": 10},
        {
            "time_scope": {
                "field": "time",
                "start": "2026-05-01T00:00:00Z",
                "end": "2026-05-08T00:00:00Z",
                "mode": "range",
            }
        },
    ],
)
def test_aoi_detect_rejects_invalid_contract_fields(payload_patch: dict[str, Any]) -> None:
    from marivo.contracts.generated import aoi

    payload = {
        "metric": "revenue",
        "time_scope": _aoi_time_scope(),
        "granularity": "day",
        "strategy": "point_anomaly",
    }
    payload.update(payload_patch)

    with pytest.raises(ValidationError):
        aoi.Detect.model_validate(payload)


@pytest.mark.parametrize("missing_field", ["metric", "time_scope", "granularity", "strategy"])
def test_aoi_detect_requires_public_required_fields(missing_field: str) -> None:
    from marivo.contracts.generated import aoi

    payload = {
        "metric": "revenue",
        "time_scope": _aoi_time_scope(),
        "granularity": "day",
        "strategy": "point_anomaly",
    }
    payload.pop(missing_field)

    with pytest.raises(ValidationError):
        aoi.Detect.model_validate(payload)


def test_aoi_forecast_accepts_public_required_fields() -> None:
    from marivo.contracts.generated import aoi

    request = aoi.Forecast.model_validate({"source_artifact_id": "artifact_source", "horizon": 7})

    assert request.source_artifact_id == "artifact_source"
    assert request.horizon == 7


@pytest.mark.parametrize(
    "payload_patch",
    [
        {"source_artifact_id": ""},
        {"horizon": 0},
        {"horizon": -1},
        {"source_ref": {"step_id": "step_1"}},
        {"profile": "auto"},
        {"profile": None},
        {"interval_level": 0.95},
        {"interval_level": None},
        {"unexpected": True},
    ],
)
def test_aoi_forecast_rejects_invalid_contract_fields(payload_patch: dict[str, Any]) -> None:
    from marivo.contracts.generated import aoi

    payload = {"source_artifact_id": "artifact_source", "horizon": 7}
    payload.update(payload_patch)

    with pytest.raises(ValidationError):
        aoi.Forecast.model_validate(payload)


@pytest.mark.parametrize("missing_field", ["source_artifact_id", "horizon"])
def test_aoi_forecast_requires_public_required_fields(missing_field: str) -> None:
    from marivo.contracts.generated import aoi

    payload = {"source_artifact_id": "artifact_source", "horizon": 7}
    payload.pop(missing_field)

    with pytest.raises(ValidationError):
        aoi.Forecast.model_validate(payload)


@pytest.mark.parametrize(
    ("model_name", "payload"),
    [
        (
            "Observe",
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
            "Observe",
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
                "current_artifact_id": "artifact_left",
                "baseline_artifact_id": "artifact_right",
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
                "current": {
                    "time_scope": {
                        "field": "event_time",
                        "start": "2026-01-01T00:00:00Z",
                        "end": "2026-01-02T00:00:00Z",
                    },
                    "filter": None,
                },
                "baseline": {
                    "time_scope": {
                        "field": "event_time",
                        "start": "2026-01-01T00:00:00Z",
                        "end": "2026-01-02T00:00:00Z",
                    }
                },
                "grain": "day",
                "hypothesis": {
                    "family": "two_sample_mean",
                    "alternative": "two_sided",
                    "significance": "balanced",
                },
            },
        ),
        (
            "Attribute",
            {
                "metric": "revenue",
                "current": {
                    "time_scope": {
                        "field": "event_time",
                        "start": "2026-01-01T00:00:00Z",
                        "end": "2026-01-02T00:00:00Z",
                    },
                    "filter": None,
                },
                "baseline": {
                    "time_scope": {
                        "field": "event_time",
                        "start": "2026-01-01T00:00:00Z",
                        "end": "2026-01-02T00:00:00Z",
                    }
                },
                "dimensions": ["region"],
            },
        ),
        (
            "Attribute",
            {
                "metric": "revenue",
                "current": {
                    "time_scope": {
                        "field": "event_time",
                        "start": "2026-01-01T00:00:00Z",
                        "end": "2026-01-02T00:00:00Z",
                    }
                },
                "baseline": {
                    "time_scope": {
                        "field": "event_time",
                        "start": "2026-01-01T00:00:00Z",
                        "end": "2026-01-02T00:00:00Z",
                    }
                },
                "dimensions": ["region"],
                "decomposition_method": None,
            },
        ),
        (
            "Attribute",
            {
                "metric": "revenue",
                "current": {
                    "time_scope": {
                        "field": "event_time",
                        "start": "2026-01-01T00:00:00Z",
                        "end": "2026-01-02T00:00:00Z",
                    }
                },
                "baseline": {
                    "time_scope": {
                        "field": "event_time",
                        "start": "2026-01-01T00:00:00Z",
                        "end": "2026-01-02T00:00:00Z",
                    }
                },
                "dimensions": ["region"],
                "decomposition_limit": None,
            },
        ),
        (
            "Diagnose",
            {
                "metric": "revenue",
                "time_scope": {
                    "field": "event_time",
                    "start": "2026-01-01T00:00:00Z",
                    "end": "2026-01-02T00:00:00Z",
                },
                "granularity": "day",
                "filter": None,
                "dimensions": ["region"],
                "strategy": "point_anomaly",
            },
        ),
        (
            "Diagnose",
            {
                "metric": "revenue",
                "time_scope": {
                    "field": "event_time",
                    "start": "2026-01-01T00:00:00Z",
                    "end": "2026-01-02T00:00:00Z",
                },
                "granularity": "day",
                "scan_dimension": None,
                "dimensions": ["region"],
                "strategy": "point_anomaly",
            },
        ),
        (
            "Diagnose",
            {
                "metric": "revenue",
                "time_scope": {
                    "field": "event_time",
                    "start": "2026-01-01T00:00:00Z",
                    "end": "2026-01-02T00:00:00Z",
                },
                "granularity": "day",
                "dimensions": ["region"],
                "strategy": "point_anomaly",
                "candidate_limit": None,
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
            "current": {
                "time_scope": {
                    "field": "event_time",
                    "start": "2026-01-01T00:00:00Z",
                    "end": "2026-01-02T00:00:00Z",
                },
                "scope": {"constraints": {"region": "US"}},
            },
            "baseline": {
                "time_scope": {
                    "field": "event_time",
                    "start": "2026-01-01T00:00:00Z",
                    "end": "2026-01-02T00:00:00Z",
                }
            },
            "grain": "day",
            "hypothesis": {
                "family": "two_sample_mean",
                "alternative": "two_sided",
                "significance": "balanced",
            },
        },
        {
            "metric": "revenue",
            "current": {
                "time_scope": {
                    "field": "event_time",
                    "start": "2026-01-01T00:00:00Z",
                    "end": "2026-01-02T00:00:00Z",
                }
            },
            "baseline": {
                "time_scope": {
                    "field": "event_time",
                    "start": "2026-01-01T00:00:00Z",
                    "end": "2026-01-02T00:00:00Z",
                }
            },
            "grain": "day",
            "hypothesis": {
                "family": "two_sample_mean",
                "alternative": "two_sided",
                "significance": "balanced",
            },
            "method": "welch_t",
        },
        {
            "metric": "revenue",
            "current": {
                "time_scope": {
                    "field": "event_time",
                    "start": "2026-01-01T00:00:00Z",
                    "end": "2026-01-02T00:00:00Z",
                }
            },
            "baseline": {
                "time_scope": {
                    "field": "event_time",
                    "start": "2026-01-01T00:00:00Z",
                    "end": "2026-01-02T00:00:00Z",
                }
            },
            "grain": "minute",
            "hypothesis": {
                "family": "two_sample_mean",
                "alternative": "two_sided",
                "significance": "balanced",
            },
        },
        {
            "metric": "revenue",
            "current": {
                "time_scope": {
                    "field": "event_time",
                    "start": "2026-01-01T00:00:00Z",
                    "end": "2026-01-02T00:00:00Z",
                }
            },
            "baseline": {
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


@pytest.mark.parametrize(
    "payload",
    [
        {
            "metric": "revenue",
            "current": {
                "time_scope": {
                    "field": "event_time",
                    "start": "2026-01-01T00:00:00Z",
                    "end": "2026-01-02T00:00:00Z",
                },
                "scope": {"constraints": {"region": "US"}},
            },
            "baseline": {
                "time_scope": {
                    "field": "event_time",
                    "start": "2026-01-01T00:00:00Z",
                    "end": "2026-01-02T00:00:00Z",
                }
            },
            "dimensions": ["region"],
        },
        {
            "metric": "revenue",
            "current": {
                "time_scope": {
                    "field": "event_time",
                    "start": "2026-01-01T00:00:00Z",
                    "end": "2026-01-02T00:00:00Z",
                }
            },
            "baseline": {
                "time_scope": {
                    "field": "event_time",
                    "start": "2026-01-01T00:00:00Z",
                    "end": "2026-01-02T00:00:00Z",
                }
            },
            "dimensions": [],
        },
        {
            "metric": "revenue",
            "current": {
                "time_scope": {
                    "field": "event_time",
                    "start": "2026-01-01T00:00:00Z",
                    "end": "2026-01-02T00:00:00Z",
                }
            },
            "baseline": {
                "time_scope": {
                    "field": "event_time",
                    "start": "2026-01-01T00:00:00Z",
                    "end": "2026-01-02T00:00:00Z",
                }
            },
            "dimensions": ["region"],
            "decomposition_method": "ratio_share",
        },
        {
            "metric": "revenue",
            "current": {
                "time_scope": {
                    "field": "event_time",
                    "start": "2026-01-01T00:00:00Z",
                    "end": "2026-01-02T00:00:00Z",
                }
            },
            "baseline": {
                "time_scope": {
                    "field": "event_time",
                    "start": "2026-01-01T00:00:00Z",
                    "end": "2026-01-02T00:00:00Z",
                }
            },
            "dimensions": ["region"],
            "decomposition_limit": 0,
        },
        {
            "metric": "revenue",
            "current": {
                "time_scope": {
                    "field": "event_time",
                    "start": "2026-01-01T00:00:00Z",
                    "end": "2026-01-02T00:00:00Z",
                }
            },
            "baseline": {
                "time_scope": {
                    "field": "event_time",
                    "start": "2026-01-01T00:00:00Z",
                    "end": "2026-01-02T00:00:00Z",
                }
            },
            "dimensions": ["region"],
            "unknown": True,
        },
    ],
)
def test_aoi_attribute_rejects_non_contract_fields(payload: dict[str, Any]) -> None:
    from marivo.contracts.generated import aoi

    with pytest.raises(ValidationError):
        aoi.Attribute.model_validate(payload)


@pytest.mark.parametrize(
    "payload",
    [
        {
            "metric": "revenue",
            "dimensions": ["region"],
            "strategy": "point_anomaly",
        },
        {
            "mode": "explicit_compare",
            "metric": "revenue",
            "current": {
                "time_scope": {
                    "field": "event_time",
                    "start": "2026-01-01T00:00:00Z",
                    "end": "2026-01-02T00:00:00Z",
                }
            },
            "dimensions": ["region"],
            "strategy": "point_anomaly",
        },
        {
            "mode": "explicit_compare",
            "metric": "revenue",
            "current": {
                "time_scope": {
                    "field": "event_time",
                    "start": "2026-01-01T00:00:00Z",
                    "end": "2026-01-02T00:00:00Z",
                }
            },
            "baseline": {
                "time_scope": {
                    "field": "event_time",
                    "start": "2026-01-01T00:00:00Z",
                    "end": "2026-01-02T00:00:00Z",
                }
            },
            "time_scope": {
                "field": "event_time",
                "start": "2026-01-01T00:00:00Z",
                "end": "2026-01-02T00:00:00Z",
            },
            "dimensions": ["region"],
            "strategy": "point_anomaly",
        },
        {
            "metric": "revenue",
            "time_scope": {
                "field": "event_time",
                "start": "2026-01-01T00:00:00Z",
                "end": "2026-01-02T00:00:00Z",
            },
            "granularity": "day",
            "scope": {"constraints": {"region": "US"}},
            "dimensions": ["region"],
            "strategy": "point_anomaly",
        },
        {
            "metric": "revenue",
            "time_scope": {
                "field": "event_time",
                "start": "2026-01-01T00:00:00Z",
                "end": "2026-01-02T00:00:00Z",
            },
            "granularity": "day",
            "baseline_policy": "previous_adjacent_equal_length",
            "dimensions": ["region"],
            "strategy": "point_anomaly",
        },
    ],
)
def test_aoi_diagnose_rejects_non_contract_fields_and_bad_mode_shapes(
    payload: dict[str, Any],
) -> None:
    from marivo.contracts.generated import aoi

    with pytest.raises(ValidationError):
        aoi.Diagnose.model_validate(payload)


@pytest.mark.parametrize("granularity", ["quarter", "year"])
def test_aoi_diagnose_accepts_generic_time_granularities(granularity: str) -> None:
    from marivo.contracts.generated import aoi

    request = aoi.Diagnose.model_validate(
        {
            "metric": "revenue",
            "time_scope": {
                "field": "event_time",
                "start": "2026-01-01T00:00:00Z",
                "end": "2026-01-02T00:00:00Z",
            },
            "granularity": granularity,
            "dimensions": ["region"],
            "strategy": "point_anomaly",
        }
    )

    assert request.granularity == granularity


def test_aoi_diagnose_rejects_invalid_granularity() -> None:
    from marivo.contracts.generated import aoi

    with pytest.raises(ValidationError):
        aoi.Diagnose.model_validate(
            {
                "metric": "revenue",
                "time_scope": {
                    "field": "event_time",
                    "start": "2026-01-01T00:00:00Z",
                    "end": "2026-01-02T00:00:00Z",
                },
                "granularity": "minute",
                "dimensions": ["region"],
                "strategy": "point_anomaly",
            }
        )


@pytest.mark.parametrize("granularity", ["quarter", "year"])
def test_aoi_detect_keeps_generic_time_granularities(granularity: str) -> None:
    from marivo.contracts.generated import aoi

    request = aoi.Detect.model_validate(
        {
            "metric": "revenue",
            "time_scope": {
                "field": "event_time",
                "start": "2026-01-01T00:00:00Z",
                "end": "2026-01-02T00:00:00Z",
            },
            "granularity": granularity,
            "strategy": "point_anomaly",
        }
    )

    assert request.granularity == granularity


def test_aoi_result_nullable_fields_still_accept_explicit_null() -> None:
    from marivo.contracts.generated import aoi

    aoi.MetricFramePoint.model_validate({"value": None})
    aoi.ScalarDeltaResult.model_validate(
        {
            "current_value": None,
            "baseline_value": None,
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


def test_component_ref_validation() -> None:
    """MetricComponentRef requires a non-empty metric string."""
    from marivo.transports.http.models.marivo_extensions import MetricComponentRef

    ref = MetricComponentRef(metric="metric.revenue")
    assert ref.metric == "metric.revenue"

    with pytest.raises(ValidationError):
        MetricComponentRef(metric="")

    with pytest.raises(ValidationError):
        MetricComponentRef()


def test_metric_extension_component_consistency_sum() -> None:
    """sum aggregation does not accept component refs; SumDecomposition has extra='forbid'."""
    from marivo.contracts.semantic_extensions import MarivoMetricExtension, SumDecomposition

    ext = MarivoMetricExtension()
    assert ext.decomposition_semantics.type == "sum"

    # SumDecomposition forbids extra fields, so passing numerator is rejected structurally
    with pytest.raises(ValidationError):
        MarivoMetricExtension(
            decomposition_semantics=SumDecomposition(numerator={"metric": "metric.gmv"})
        )


def test_metric_extension_component_consistency_ratio() -> None:
    """ratio aggregation requires numerator and denominator; no weight field exists on RatioDecomposition."""
    from marivo.contracts.semantic_extensions import (
        MarivoMetricExtension,
        MetricComponentRef,
        RatioDecomposition,
    )

    ext = MarivoMetricExtension(
        decomposition_semantics=RatioDecomposition(
            numerator=MetricComponentRef(metric="metric.converted"),
            denominator=MetricComponentRef(metric="metric.total"),
        )
    )
    assert ext.decomposition_semantics.type == "ratio"
    assert ext.decomposition_semantics.numerator.metric == "metric.converted"
    assert ext.decomposition_semantics.denominator.metric == "metric.total"

    # Missing required numerator raises ValidationError
    with pytest.raises(ValidationError):
        RatioDecomposition(denominator=MetricComponentRef(metric="metric.total"))

    # Missing required denominator raises ValidationError
    with pytest.raises(ValidationError):
        RatioDecomposition(numerator=MetricComponentRef(metric="metric.converted"))

    # RatioDecomposition forbids extra fields (e.g. weight)
    with pytest.raises(ValidationError):
        RatioDecomposition(
            numerator=MetricComponentRef(metric="metric.converted"),
            denominator=MetricComponentRef(metric="metric.total"),
            weight=MetricComponentRef(metric="metric.orders"),
        )


def test_metric_extension_component_consistency_weighted_average() -> None:
    """weighted_average aggregation requires numerator and weight; no denominator field exists."""
    from marivo.contracts.semantic_extensions import (
        MarivoMetricExtension,
        MetricComponentRef,
        WeightedAverageDecomposition,
    )

    ext = MarivoMetricExtension(
        decomposition_semantics=WeightedAverageDecomposition(
            numerator=MetricComponentRef(metric="metric.gmv"),
            weight=MetricComponentRef(metric="metric.order_count"),
        )
    )
    assert ext.decomposition_semantics.type == "weighted_average"
    assert ext.decomposition_semantics.numerator.metric == "metric.gmv"
    assert ext.decomposition_semantics.weight.metric == "metric.order_count"

    # Missing required numerator raises ValidationError
    with pytest.raises(ValidationError):
        WeightedAverageDecomposition(weight=MetricComponentRef(metric="metric.order_count"))

    # Missing required weight raises ValidationError
    with pytest.raises(ValidationError):
        WeightedAverageDecomposition(numerator=MetricComponentRef(metric="metric.gmv"))

    # WeightedAverageDecomposition forbids extra fields (e.g. denominator)
    with pytest.raises(ValidationError):
        WeightedAverageDecomposition(
            numerator=MetricComponentRef(metric="metric.gmv"),
            weight=MetricComponentRef(metric="metric.order_count"),
            denominator=MetricComponentRef(metric="metric.total"),
        )


def test_generated_osi_additive_dimensions_not_a_model_field() -> None:
    """additive_dimensions is a retired field; it is not a recognized model field.
    Since MarivoMetricExtension uses extra='allow', additive_dimensions would be
    accepted into __pydantic_extra__ rather than rejected outright. Verify it is
    not a declared model field."""
    from marivo.contracts.generated.osi import MarivoMetricExtension

    assert "additive_dimensions" not in MarivoMetricExtension.model_fields

    # It goes into __pydantic_extra__ rather than being rejected
    ext = MarivoMetricExtension.model_validate({"additive_dimensions": ["region"]})
    assert ext.decomposition_semantics.type == "sum"
    assert "additive_dimensions" not in MarivoMetricExtension.model_fields


def test_generated_osi_component_ref_accepts_valid() -> None:
    """Generated OSI MetricComponentRef accepts valid metric references."""
    from marivo.contracts.generated.osi import MetricComponentRef

    ref = MetricComponentRef.model_validate({"metric": "metric.gmv"})
    assert ref.metric == "metric.gmv"

    with pytest.raises(ValidationError):
        MetricComponentRef.model_validate({"metric": ""})

    with pytest.raises(ValidationError):
        MetricComponentRef.model_validate({})


def test_expression_component_requires_non_empty_expression() -> None:
    """ExpressionComponent requires a non-empty expression string and forbids extra fields."""
    from marivo.contracts.semantic_extensions import ExpressionComponent

    comp = ExpressionComponent(expression="SUM(order_amount)")
    assert comp.expression == "SUM(order_amount)"

    with pytest.raises(ValidationError):
        ExpressionComponent(expression="")

    with pytest.raises(ValidationError):
        ExpressionComponent()

    # Extra fields are forbidden
    with pytest.raises(ValidationError):
        ExpressionComponent(expression="SUM(order_amount)", metric="metric.gmv")


def test_component_spec_union_accepts_metric_ref_and_expression() -> None:
    """ComponentSpec union type accepts both MetricComponentRef and ExpressionComponent."""
    from marivo.contracts.semantic_extensions import (
        ExpressionComponent,
        MetricComponentRef,
    )

    # MetricComponentRef variant
    ref = MetricComponentRef(metric="metric.gmv")
    assert isinstance(ref, MetricComponentRef)

    # ExpressionComponent variant
    expr = ExpressionComponent(expression="SUM(order_amount)")
    assert isinstance(expr, ExpressionComponent)


def test_polymorphic_construction_from_dict_sum() -> None:
    """MarivoMetricExtension.model_validate accepts object-format decomposition_semantics for sum."""
    from marivo.contracts.semantic_extensions import MarivoMetricExtension

    ext = MarivoMetricExtension.model_validate({"decomposition_semantics": {"type": "sum"}})
    assert ext.decomposition_semantics.type == "sum"


def test_polymorphic_construction_from_dict_ratio() -> None:
    """MarivoMetricExtension.model_validate accepts object-format decomposition_semantics for ratio."""
    from marivo.contracts.semantic_extensions import MarivoMetricExtension

    ext = MarivoMetricExtension.model_validate(
        {
            "decomposition_semantics": {
                "type": "ratio",
                "numerator": {"metric": "metric.converted"},
                "denominator": {"metric": "metric.total"},
            }
        }
    )
    assert ext.decomposition_semantics.type == "ratio"
    assert ext.decomposition_semantics.numerator.metric == "metric.converted"
    assert ext.decomposition_semantics.denominator.metric == "metric.total"


def test_polymorphic_construction_from_dict_weighted_average() -> None:
    """MarivoMetricExtension.model_validate accepts object-format decomposition_semantics for weighted_average."""
    from marivo.contracts.semantic_extensions import MarivoMetricExtension

    ext = MarivoMetricExtension.model_validate(
        {
            "decomposition_semantics": {
                "type": "weighted_average",
                "numerator": {"metric": "metric.gmv"},
                "weight": {"metric": "metric.order_count"},
            }
        }
    )
    assert ext.decomposition_semantics.type == "weighted_average"
    assert ext.decomposition_semantics.numerator.metric == "metric.gmv"
    assert ext.decomposition_semantics.weight.metric == "metric.order_count"


def test_polymorphic_construction_from_dict_ratio_with_expression_component() -> None:
    """RatioDecomposition numerator/denominator can be ExpressionComponent via dict validation."""
    from marivo.contracts.semantic_extensions import MarivoMetricExtension

    ext = MarivoMetricExtension.model_validate(
        {
            "decomposition_semantics": {
                "type": "ratio",
                "numerator": {"expression": "SUM(converted_flag)"},
                "denominator": {"metric": "metric.total"},
            }
        }
    )
    assert ext.decomposition_semantics.type == "ratio"
    # ExpressionComponent variant
    assert ext.decomposition_semantics.numerator.expression == "SUM(converted_flag)"
    # MetricComponentRef variant
    assert ext.decomposition_semantics.denominator.metric == "metric.total"


def test_flat_string_decomposition_semantics_rejected() -> None:
    """Flat string format for decomposition_semantics is no longer valid; must be an object."""
    from marivo.contracts.semantic_extensions import MarivoMetricExtension

    with pytest.raises(ValidationError):
        MarivoMetricExtension.model_validate({"decomposition_semantics": "sum"})

    with pytest.raises(ValidationError):
        MarivoMetricExtension.model_validate({"decomposition_semantics": "ratio"})
