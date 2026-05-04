from __future__ import annotations

from base64 import urlsafe_b64encode
from typing import Any, cast

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError

_DOCS_BY_PATH_PREFIX: tuple[tuple[str, str], ...] = (
    ("/semantic-models", "docs/api/semantic.md"),
    ("/semantic/", "docs/api/semantic.md"),
    ("/catalog/", "docs/api/semantic.md"),
    ("/compiler/", "docs/api/semantic.md"),
    ("/sessions/", "docs/api/intent-steps.md"),
    ("/sources/", "docs/api/sources.md"),
    ("/engines/", "docs/api/engines.md"),
    ("/mappings/", "docs/api/mappings.md"),
    ("/governance/", "docs/api/governance.md"),
    ("/policies/", "docs/api/governance.md"),
    ("/quality-rules/", "docs/api/governance.md"),
)

_GUIDED_EXAMPLES: dict[tuple[str, str], list[dict[str, Any]]] = {
    (
        "POST",
        "/semantic/entities",
    ): [
        {
            "summary": "Minimal typed entity create payload",
            "complexity": "minimal",
            "payload": {
                "header": {
                    "entity_ref": "entity.user",
                    "display_name": "User",
                    "entity_contract_version": "entity.v4",
                },
                "interface_contract": {
                    "identity": {
                        "key_refs": ["key.user_id"],
                        "uniqueness_scope": "global",
                        "id_stability": "stable",
                    }
                },
            },
        }
    ],
    (
        "PUT",
        "/semantic/entities/{entity_id}",
    ): [
        {
            "summary": "Minimal typed entity update payload",
            "complexity": "minimal",
            "payload": {
                "display_name": "User",
                "interface_contract": {
                    "identity": {
                        "key_refs": ["key.user_id"],
                        "uniqueness_scope": "global",
                        "id_stability": "stable",
                    }
                },
            },
        }
    ],
    (
        "POST",
        "/semantic/metrics",
    ): [
        {
            "summary": "Minimal typed metric create payload",
            "complexity": "minimal",
            "payload": {
                "header": {
                    "metric_ref": "metric.dau",
                    "display_name": "DAU",
                    "metric_family": "count_metric",
                    "observed_entity_ref": "entity.user",
                    "observation_grain_ref": "grain.user",
                    "sample_kind": "numeric",
                    "value_semantics": "count",
                    "additivity_constraints": {
                        "dimension_policy": "none",
                        "time_axis_policy": "non_additive",
                    },
                    "metric_contract_version": "metric.v1",
                },
                "payload": {
                    "metric_family": "count_metric",
                    "count_target": {
                        "name": "active_users",
                        "semantics": "distinct active users",
                        "aggregation": "count_distinct",
                    },
                },
            },
        }
    ],
    (
        "PUT",
        "/semantic/metrics/{metric_id}",
    ): [
        {
            "summary": "Minimal typed metric update payload",
            "complexity": "minimal",
            "payload": {
                "display_name": "Daily Active Users",
                "payload": {
                    "metric_family": "count_metric",
                    "count_target": {
                        "name": "active_users",
                        "semantics": "distinct active users",
                        "aggregation": "count_distinct",
                    },
                },
            },
        }
    ],
    (
        "POST",
        "/semantic/time",
    ): [
        {
            "summary": "Minimal time semantic create payload",
            "complexity": "minimal",
            "payload": {
                "header": {
                    "time_ref": "time.signup_time",
                    "display_name": "Signup Time",
                    "semantic_roles": ["business_anchor"],
                    "time_contract_version": "time.v1",
                }
            },
        }
    ],
    (
        "PUT",
        "/semantic/time/{time_contract_id}",
    ): [
        {
            "summary": "Minimal time semantic update payload",
            "complexity": "minimal",
            "payload": {
                "display_name": "User Signup Time",
                "semantic_roles": ["business_anchor", "measurement"],
            },
        }
    ],
    (
        "POST",
        "/semantic/dimensions",
    ): [
        {
            "summary": "Minimal dimension create payload",
            "complexity": "minimal",
            "payload": {
                "header": {
                    "dimension_ref": "dimension.country",
                    "display_name": "Country",
                    "dimension_contract_version": "dimension.v1",
                },
                "interface_contract": {
                    "value_domain": {
                        "structure_kind": "flat",
                        "value_type": "string",
                        "domain_kind": "open",
                    }
                },
            },
        }
    ],
    (
        "PUT",
        "/semantic/dimensions/{dimension_contract_id}",
    ): [
        {
            "summary": "Minimal dimension update payload",
            "complexity": "minimal",
            "payload": {
                "display_name": "Country",
                "interface_contract": {
                    "value_domain": {
                        "structure_kind": "flat",
                        "value_type": "string",
                        "domain_kind": "open",
                    }
                },
            },
        }
    ],
    (
        "POST",
        "/semantic/enum-sets",
    ): [
        {
            "summary": "Minimal enum set create payload",
            "complexity": "minimal",
            "payload": {
                "header": {
                    "enum_set_ref": "enum.country_code",
                    "value_type": "string",
                },
                "display_name": "Country Code",
                "description": "ISO country code values",
                "versions": [
                    {
                        "enum_version": "v1",
                        "values": [
                            {"value_key": "CN", "raw_value": "CN", "label": "China"},
                        ],
                    }
                ],
            },
        }
    ],
    (
        "PUT",
        "/semantic/enum-sets/{enum_set_contract_id}",
    ): [
        {
            "summary": "Minimal enum set update payload",
            "complexity": "minimal",
            "payload": {
                "display_name": "Country Code",
                "versions": [
                    {
                        "enum_version": "v2",
                        "values": [
                            {"value_key": "CN", "raw_value": "CN", "label": "China"},
                        ],
                    }
                ],
            },
        }
    ],
    (
        "POST",
        "/semantic/process-objects",
    ): [
        {
            "summary": "Minimal process object create payload",
            "complexity": "minimal",
            "payload": {
                "header": {
                    "process_ref": "process.signup_cohort",
                    "display_name": "Signup Cohort",
                    "process_type": "cohort_definition",
                    "process_contract_version": "process.v1",
                },
                "interface_contract": {
                    "contract_mode": "context_provider",
                    "context_kind": "cohort_membership",
                    "population_subject_ref": "subject.user",
                    "membership_cardinality": "exclusive_one",
                },
                "payload": {
                    "process_type": "cohort_definition",
                    "cohort_key": "signup_cohort",
                    "entry_population": {"base_population_ref": "population.user"},
                    "cohort_anchor_ref": "time.signup_time",
                },
            },
        }
    ],
    (
        "PUT",
        "/semantic/process-objects/{process_contract_id}",
    ): [
        {
            "summary": "Minimal process object update payload",
            "complexity": "minimal",
            "payload": {
                "display_name": "Signup Cohort",
                "payload": {
                    "process_type": "cohort_definition",
                    "cohort_key": "signup_cohort",
                    "entry_population": {"base_population_ref": "population.user"},
                    "cohort_anchor_ref": "time.signup_time",
                },
            },
        }
    ],
    (
        "POST",
        "/semantic/relationships",
    ): [
        {
            "summary": "Minimal entity relationship create payload",
            "complexity": "minimal",
            "payload": {
                "relationship_ref": "relationship.exposure_to_conversion",
                "left_entity_ref": "entity.exposure",
                "right_entity_ref": "entity.conversion",
                "key_alignment": {
                    "left_field_ref": "entity.exposure.field.user_id",
                    "right_field_ref": "entity.conversion.field.user_id",
                },
                "cardinality": "many_to_many",
            },
        }
    ],
    (
        "PUT",
        "/semantic/relationships/{relationship_id}",
    ): [
        {
            "summary": "Minimal entity relationship update payload",
            "complexity": "minimal",
            "payload": {"cardinality": "many_to_many"},
        }
    ],
    (
        "POST",
        "/compiler/compatibility-profiles",
    ): [
        {
            "summary": "Minimal compatibility profile create payload",
            "complexity": "minimal",
            "payload": {
                "profile_ref": "compiler_profile.metric_requires_signup_cohort",
                "profile_kind": "requirement",
                "schema_version": "v1",
                "subject_kind": "metric",
                "subject_ref": "metric.signup_rate",
                "requirement": {
                    "contract_modes": ["context_provider"],
                    "context_kinds": ["cohort_membership"],
                },
            },
        }
    ],
    (
        "PUT",
        "/compiler/compatibility-profiles/{profile_id}",
    ): [
        {
            "summary": "Minimal compatibility profile update payload",
            "complexity": "minimal",
            "payload": {
                "requirement": {
                    "contract_modes": ["context_provider"],
                    "context_kinds": ["cohort_membership"],
                }
            },
        }
    ],
    (
        "POST",
        "/sessions/{session_id}/intents/observe",
    ): [
        {
            "summary": "Minimal observe payload",
            "complexity": "minimal",
            "payload": {
                "metric": "metric.watch_time",
                "time_scope": {"kind": "range", "start": "2026-04-01", "end": "2026-04-08"},
            },
        }
    ],
    (
        "POST",
        "/sessions/{session_id}/intents/compare",
    ): [
        {
            "summary": "Minimal compare payload",
            "complexity": "minimal",
            "payload": {
                "left_ref": {"step_id": "step_obs_current", "step_type": "observe"},
                "right_ref": {"step_id": "step_obs_baseline", "step_type": "observe"},
                "mode": "auto",
            },
        }
    ],
    (
        "POST",
        "/sessions/{session_id}/intents/detect",
    ): [
        {
            "summary": "Minimal detect payload",
            "complexity": "minimal",
            "payload": {
                "metric": "metric.watch_time",
                "time_scope": {"kind": "range", "start": "2026-04-01", "end": "2026-04-08"},
                "granularity": "day",
            },
        }
    ],
}

_SCHEMA_NAME_BY_ROUTE: dict[tuple[str, str], str] = {
    ("POST", "/semantic/entities"): "TypedEntityCreateRequest",
    ("PUT", "/semantic/entities/{entity_id}"): "TypedEntityUpdateRequest",
    ("POST", "/semantic/metrics"): "TypedMetricCreateRequest",
    ("PUT", "/semantic/metrics/{metric_id}"): "TypedMetricUpdateRequest",
    ("POST", "/semantic/time"): "TimeCreateRequest",
    ("PUT", "/semantic/time/{time_contract_id}"): "TimeUpdateRequest",
    ("POST", "/semantic/dimensions"): "DimensionCreateRequest",
    ("PUT", "/semantic/dimensions/{dimension_contract_id}"): "DimensionUpdateRequest",
    ("POST", "/semantic/enum-sets"): "EnumSetCreateRequest",
    ("PUT", "/semantic/enum-sets/{enum_set_contract_id}"): "EnumSetUpdateRequest",
    ("POST", "/semantic/process-objects"): "ProcessObjectCreateRequest",
    ("PUT", "/semantic/process-objects/{process_contract_id}"): "ProcessObjectUpdateRequest",
    ("POST", "/semantic/batch"): "SemanticBatchRequest",
    ("POST", "/semantic/relationships"): "EntityRelationshipCreateRequest",
    ("PUT", "/semantic/relationships/{relationship_id}"): "EntityRelationshipUpdateRequest",
    ("POST", "/compiler/compatibility-profiles"): "CompatibilityProfileCreateRequest",
    ("PUT", "/compiler/compatibility-profiles/{profile_id}"): "CompatibilityProfileUpdateRequest",
    ("POST", "/sessions"): "SessionCreateRequest",
    ("POST", "/sessions/{session_id}/state/query"): "SessionStateQueryRequest",
    ("POST", "/sessions/{session_id}/intents/observe"): "ObserveRequest",
    ("POST", "/sessions/{session_id}/intents/compare"): "CompareRequest",
    ("POST", "/sessions/{session_id}/intents/decompose"): "DecomposeRequest",
    ("POST", "/sessions/{session_id}/intents/correlate"): "CorrelateRequest",
    ("POST", "/sessions/{session_id}/intents/detect"): "DetectRequest",
    ("POST", "/sessions/{session_id}/intents/test"): "IntentTestRequest",
    ("POST", "/sessions/{session_id}/intents/forecast"): "ForecastRequest",
    ("POST", "/sessions/{session_id}/intents/attribute"): "AttributeRequest",
    ("POST", "/sessions/{session_id}/intents/diagnose"): "DiagnoseRequest",
    ("POST", "/sessions/{session_id}/intents/validate"): "ValidateRequest",
}


class GuidedValidationError(Exception):
    """Raised when a route wants the shared guided 422 response body."""

    def __init__(self, payload: dict[str, Any]) -> None:
        super().__init__(payload["error"]["message"])
        self.payload = payload


def sanitize_validation_errors(
    error: ValidationError | RequestValidationError,
) -> list[dict[str, Any]]:
    try:
        detail = cast("list[dict[str, Any]]", error.errors(include_url=False))  # type: ignore[call-arg]
    except TypeError:
        detail = cast("list[dict[str, Any]]", error.errors())
    for item in detail:
        ctx = item.get("ctx")
        if not isinstance(ctx, dict):
            continue
        for key, value in list(ctx.items()):
            if isinstance(value, BaseException):
                ctx[key] = str(value)
    return detail


def _encode_openapi_path(path: str) -> str:
    return urlsafe_b64encode(path.encode("utf-8")).decode("ascii").rstrip("=")


def _normalize_route_path(request: Request) -> str:
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    if isinstance(path, str):
        return path
    return request.url.path


def _docs_url_for_path(path: str) -> str:
    for prefix, docs_path in _DOCS_BY_PATH_PREFIX:
        if path.startswith(prefix):
            return docs_path
    return "docs/api/errors.md"


_LEGACY_PHYSICAL_GROUNDING_FIELDS = frozenset(
    {
        "carrier_locator",
        "physical_column",
        "physical_name",
        "source_object_ref",
    }
)

_ENTITY_FIELD_AUTHORING_ERROR_MARKERS = frozenset(
    {
        "fully qualified entity field",
        "entity.<entity>.field.<field>",
    }
)


def _detail_mentions_legacy_physical_grounding(detail: list[dict[str, Any]]) -> bool:
    for item in detail:
        loc = item.get("loc")
        if isinstance(loc, list | tuple) and any(
            str(part) in _LEGACY_PHYSICAL_GROUNDING_FIELDS for part in loc
        ):
            return True
        message = str(item.get("msg") or "")
        if any(field in message for field in _LEGACY_PHYSICAL_GROUNDING_FIELDS):
            return True
    return False


def _detail_mentions_entity_field_authoring(detail: list[dict[str, Any]]) -> bool:
    for item in detail:
        message = str(item.get("msg") or "")
        if any(marker in message for marker in _ENTITY_FIELD_AUTHORING_ERROR_MARKERS):
            return True
    return False


def _add_entity_first_authoring_guidance(
    guidance: dict[str, Any],
    *,
    detail: list[dict[str, Any]] | None = None,
    route_path: str | None = None,
) -> None:
    semantic_routes = {
        "/semantic/metrics",
        "/semantic/process-objects",
        "/semantic/dimensions",
        "/semantic/time",
        "/semantic/predicates",
    }
    if route_path not in semantic_routes:
        return
    if detail is not None and not (
        _detail_mentions_legacy_physical_grounding(detail)
        or _detail_mentions_entity_field_authoring(detail)
    ):
        return
    guidance["authoring_model"] = "dataset_native"
    guidance["legacy_physical_grounding_fields"] = sorted(_LEGACY_PHYSICAL_GROUNDING_FIELDS)
    guidance["dataset_native_next_action"] = (
        "Move physical table/view and column grounding to OSI datasets and fields: "
        "dataset.custom_extensions[].data.datasource_id, dataset.source, and field.expression."
    )


def build_validation_error_payload(
    request: Request,
    detail: list[dict[str, Any]],
) -> dict[str, Any]:
    route_path = _normalize_route_path(request)
    method = request.method.upper()
    path_fragment_url = (
        f"/openapi/paths/{_encode_openapi_path(route_path)}"
        f"?operation={method.lower()}&expand=request,schemas&depth=6"
    )
    schema_name = _SCHEMA_NAME_BY_ROUTE.get((method, route_path))
    guidance: dict[str, Any] = {
        "docs_url": _docs_url_for_path(route_path),
        "contract_url": path_fragment_url,
    }
    if schema_name is not None:
        guidance["schema_url"] = f"/openapi/schemas/{schema_name}?depth=6"
    examples = _GUIDED_EXAMPLES.get((method, route_path))
    if examples is not None:
        guidance["examples"] = examples
    _add_entity_first_authoring_guidance(guidance, detail=detail, route_path=route_path)
    guidance["next_action"] = (
        "Start with guidance.examples, then inspect guidance.schema_url for the exact request model, "
        "and use guidance.contract_url when nested refs or route-scoped rules are unclear."
    )
    return {
        "detail": detail,
        "error": {
            "code": "request_validation_error",
            "message": "Request validation failed. Use the guided example and contract links.",
        },
        "guidance": guidance,
    }


def build_service_validation_error_payload(
    request: Request | None,
    *,
    message: str,
    code: str,
    category: str | None,
    field_path: str | None = None,
    remediation: dict[str, Any] | None = None,
    examples: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build the shared guided payload for semantic service validation errors."""

    if request is not None:
        route_path = _normalize_route_path(request)
        method = request.method.upper()
        path_fragment_url = (
            f"/openapi/paths/{_encode_openapi_path(route_path)}"
            f"?operation={method.lower()}&expand=request,schemas&depth=6"
        )
        schema_name = _SCHEMA_NAME_BY_ROUTE.get((method, route_path))
        route_examples = _GUIDED_EXAMPLES.get((method, route_path))
        docs_url = _docs_url_for_path(route_path)
    else:
        path_fragment_url = "/openapi/paths"
        schema_name = None
        route_examples = None
        docs_url = "docs/api/semantic.md"

    guidance: dict[str, Any] = {
        "docs_url": docs_url,
        "contract_url": path_fragment_url,
        "examples": examples if examples is not None else route_examples or [],
        "remediation": remediation or {},
    }
    if schema_name is not None:
        guidance["schema_url"] = f"/openapi/schemas/{schema_name}?depth=6"
    else:
        guidance["schema_url"] = "/openapi/schemas"
    _add_entity_first_authoring_guidance(guidance, route_path=route_path if request else None)
    return {
        "message": message,
        "code": code,
        "category": category or "validation",
        "field_path": field_path,
        "error": {
            "code": code,
            "message": message,
            "category": category or "validation",
            "field_path": field_path,
        },
        "guidance": guidance,
    }


async def request_validation_exception_handler(
    request: Request, error: RequestValidationError
) -> JSONResponse:
    payload = build_validation_error_payload(request, sanitize_validation_errors(error))
    return JSONResponse(status_code=422, content=payload)


async def guided_validation_exception_handler(
    _request: Request, error: GuidedValidationError
) -> JSONResponse:
    return JSONResponse(status_code=422, content=error.payload)
