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


def _aoi_time_scope() -> dict[str, str]:
    return {
        "field": "event_time",
        "start": "2026-05-01T00:00:00Z",
        "end": "2026-05-08T00:00:00Z",
    }


def test_aoi_observe_accepts_scalar_branch() -> None:
    from marivo.contracts.generated import aoi

    request = aoi.Observe1.model_validate(
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

    request = aoi.Observe2.model_validate(
        {
            "metric": "metric.revenue",
            "time_scope": _aoi_time_scope(),
            "granularity": granularity,
        }
    )

    assert request.granularity == granularity


def test_aoi_observe_accepts_segmented_branch() -> None:
    from marivo.contracts.generated import aoi

    request = aoi.Observe3.model_validate(
        {
            "metric": "metric.revenue",
            "time_scope": _aoi_time_scope(),
            "dimensions": ["region", "platform"],
        }
    )

    assert [dimension.root for dimension in request.dimensions] == ["region", "platform"]


def test_aoi_observe_preserves_filter_expression() -> None:
    from marivo.contracts.generated import aoi

    request = aoi.Observe1.model_validate(
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
            "filter": None,
        },
        {
            "metric": "metric.revenue",
            "time_scope": _aoi_time_scope(),
            "granularity": None,
        },
        {
            "metric": "metric.revenue",
            "time_scope": _aoi_time_scope(),
            "dimensions": None,
        },
        {
            "metric": "metric.revenue",
            "time_scope": _aoi_time_scope(),
            "dimensions": [],
        },
        {
            "metric": "metric.revenue",
            "time_scope": _aoi_time_scope(),
            "granularity": "day",
            "dimensions": ["region"],
        },
        {"time_scope": _aoi_time_scope()},
        {"metric": "metric.revenue"},
    ],
)
def test_aoi_observe_rejects_invalid_contract_shapes(payload: dict[str, Any]) -> None:
    from marivo.contracts.generated import aoi

    for model in (aoi.Observe1, aoi.Observe2, aoi.Observe3):
        with pytest.raises(ValidationError):
            model.model_validate(payload)


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
    attribute = aoi.Attribute.model_validate(
        {
            "metric": "revenue",
            "left": {"time_scope": time_scope},
            "right": {"time_scope": time_scope},
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
            "candidate_dimensions": ["region"],
            "strategy": "point_anomaly",
        }
    )
    assert diagnose.mode == "auto_detect"
    assert diagnose.sensitivity == "aggressive"
    assert diagnose.followup_limit == 3
    assert diagnose.decomposition_limit == 5
    explicit = aoi.Diagnose.model_validate(
        {
            "mode": "explicit_compare",
            "metric": "revenue",
            "current": {"time_scope": time_scope},
            "baseline": {"time_scope": time_scope},
            "candidate_dimensions": ["region"],
            "strategy": "period_shift",
        }
    )
    assert explicit.current is not None
    assert explicit.baseline is not None


def _aoi_test_payload() -> dict[str, Any]:
    return {
        "metric": "revenue",
        "left": {"time_scope": _aoi_time_scope()},
        "right": {"time_scope": _aoi_time_scope()},
        "kind": "numeric",
        "hypothesis": {
            "family": "two_sample_mean",
            "alternative": "two_sided",
            "significance": "balanced",
        },
    }


@pytest.mark.parametrize("alternative", ["two_sided", "greater", "less"])
@pytest.mark.parametrize("significance", ["conservative", "balanced", "aggressive"])
def test_aoi_test_accepts_all_public_options(alternative: str, significance: str) -> None:
    from marivo.contracts.generated import aoi

    payload = _aoi_test_payload()
    payload["left"]["filter"] = {
        "dialects": [{"dialect": "ANSI_SQL", "expression": "region = 'US'"}]
    }
    payload["hypothesis"]["alternative"] = alternative
    payload["hypothesis"]["significance"] = significance

    request = aoi.Test.model_validate(payload)

    assert request.kind == "numeric"
    assert request.hypothesis.family == "two_sample_mean"
    assert request.hypothesis.alternative == alternative
    assert request.hypothesis.significance == significance
    assert request.left.filter is not None
    assert request.left.filter.model_dump(exclude_none=True) == {
        "dialects": [{"dialect": "ANSI_SQL", "expression": "region = 'US'"}]
    }


def test_aoi_test_omits_absent_optional_filter_fields() -> None:
    from marivo.contracts.generated import aoi

    request = aoi.Test.model_validate(_aoi_test_payload())

    dumped = request.model_dump(exclude_none=True)
    assert "filter" not in dumped["left"]
    assert "filter" not in dumped["right"]


@pytest.mark.parametrize("missing_field", ["metric", "left", "right", "kind", "hypothesis"])
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
        {"method": "welch_t"},
        {"left": {"scope": {"constraints": {"region": "US"}}}},
        {"left": {"filter": None}},
        {"hypothesis": {"family": "two_sample_proportion"}},
        {"hypothesis": {"alternative": "not_equal"}},
        {"hypothesis": {"significance": "loose"}},
        {"hypothesis": {"alpha": 0.05}},
        {"hypothesis": {"label": "legacy label"}},
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
                "kind": "range",
                "start": "2026-05-01T00:00:00Z",
                "end": "2026-05-08T00:00:00Z",
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
        (
            "Attribute",
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
                "dimensions": ["region"],
            },
        ),
        (
            "Attribute",
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
                "dimensions": ["region"],
                "decomposition_method": None,
            },
        ),
        (
            "Attribute",
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
                "candidate_dimensions": ["region"],
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
                "detect_dimension": None,
                "candidate_dimensions": ["region"],
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
                "candidate_dimensions": ["region"],
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
            "dimensions": ["region"],
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
            "dimensions": [],
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
            "dimensions": ["region"],
            "decomposition_method": "ratio_share",
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
            "candidate_dimensions": ["region"],
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
            "candidate_dimensions": ["region"],
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
            "candidate_dimensions": ["region"],
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
            "candidate_dimensions": ["region"],
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
            "candidate_dimensions": ["region"],
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
def test_aoi_diagnose_rejects_unsupported_granularity(granularity: str) -> None:
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
                "granularity": granularity,
                "candidate_dimensions": ["region"],
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
