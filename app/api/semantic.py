from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query, Request
from pydantic import BaseModel, ValidationError

from app.api.deps import get_services
from app.api.errors import build_service_validation_error_payload, sanitize_validation_errors
from app.api.models import (
    CompatibilityProfileCreateRequest,
    CompatibilityProfileResponse,
    CompatibilityProfileUpdateRequest,
    DimensionCreateRequest,
    DimensionResponse,
    DimensionUpdateRequest,
    EnumSetCreateRequest,
    EnumSetResponse,
    EnumSetUpdateRequest,
    MetricRevisionCreateRequest,
    PredicateCreateRequest,
    PredicateResponse,
    PredicateUpdateRequest,
    ProcessObjectCreateRequest,
    ProcessObjectResponse,
    ProcessObjectUpdateRequest,
    SemanticBatchRequest,
    SemanticBatchResponse,
    SemanticValidateActionResponse,
    TimeCreateRequest,
    TimeResponse,
    TimeUpdateRequest,
    TypedBindingCreateRequest,
    TypedBindingResponse,
    TypedBindingUpdateRequest,
    TypedEntityCreateRequest,
    TypedEntityResponse,
    TypedEntityUpdateRequest,
    TypedMetricCreateRequest,
    TypedMetricResponse,
    TypedMetricUpdateRequest,
)

router = APIRouter()


_CREATE_MODEL_BY_BATCH_KIND: dict[str, type[BaseModel]] = {
    "time": TimeCreateRequest,
    "dimension": DimensionCreateRequest,
    "enum_set": EnumSetCreateRequest,
    "entity": TypedEntityCreateRequest,
    "process_object": ProcessObjectCreateRequest,
    "metric": TypedMetricCreateRequest,
    "binding": TypedBindingCreateRequest,
}

_BATCH_CREATE_ORDER = {
    "time": 0,
    "dimension": 1,
    "enum_set": 2,
    "entity": 3,
    "process_object": 4,
    "metric": 5,
    "binding": 6,
}

_BATCH_ACTIVATE_ORDER = {
    "time": 0,
    "dimension": 1,
    "enum_set": 2,
    "entity": 3,
    "process_object": 4,
    "metric": 5,
    "binding": 6,
}


def _merge_batch_defaults(
    payload: dict[str, Any],
    request: SemanticBatchRequest,
) -> dict[str, Any]:
    if request.defaults is None:
        return payload
    expanded = dict(payload)
    contract = dict(expanded.get("interface_contract") or {})
    carrier_refs = list(contract.pop("carrier_binding_refs", []) or [])
    time_refs = list(contract.pop("time_binding_refs", []) or [])
    if carrier_refs:
        local_keys = {
            str(item.get("binding_key") or "")
            for item in list(contract.get("carrier_bindings") or [])
            if isinstance(item, dict)
        }
        carriers = list(contract.get("carrier_bindings") or [])
        for ref in carrier_refs:
            default = request.defaults.carrier_bindings.get(str(ref))
            if default is None:
                raise ValueError(f"Unknown carrier binding default: {ref}")
            binding_key = str(default.get("binding_key") or "")
            if binding_key in local_keys:
                raise ValueError(
                    f"carrier binding default conflicts with local binding_key: {binding_key}"
                )
            local_keys.add(binding_key)
            carriers.append(dict(default))
        contract["carrier_bindings"] = carriers
    if time_refs:
        local_time_keys = {
            (
                str(item.get("carrier_binding_key") or ""),
                str((item.get("target") or {}).get("target_kind") or ""),
                str(item.get("semantic_ref") or ""),
            )
            for item in list(contract.get("time_bindings") or [])
            if isinstance(item, dict)
        }
        time_bindings = list(contract.get("time_bindings") or [])
        for ref in time_refs:
            default = request.defaults.time_bindings.get(str(ref))
            if default is None:
                raise ValueError(f"Unknown time binding default: {ref}")
            key = (
                str(default.get("carrier_binding_key") or ""),
                str((default.get("target") or {}).get("target_kind") or ""),
                str(default.get("semantic_ref") or ""),
            )
            if key in local_time_keys:
                raise ValueError(f"time binding default conflicts with local target: {ref}")
            local_time_keys.add(key)
            time_bindings.append(dict(default))
        contract["time_bindings"] = time_bindings
    if contract:
        expanded["interface_contract"] = contract
    return expanded


def _coverage_from_result(result: dict[str, Any] | None) -> dict[str, Any] | None:
    if not result:
        return None
    capabilities = result.get("capabilities")
    if not isinstance(capabilities, dict):
        return None
    keys = {
        "required_targets",
        "covered_targets",
        "missing_required_targets",
        "imported_covered_targets",
        "covers_required_targets",
    }
    coverage = {key: capabilities[key] for key in keys if key in capabilities}
    return coverage or None


def _batch_error_payload(
    message: str,
    code: str = "semantic_batch_item_failed",
    *,
    remediation: dict[str, Any] | None = None,
    examples: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    payload = build_service_validation_error_payload(
        request=None,
        message=message,
        code=code,
        category="validation",
        remediation=remediation,
        examples=examples,
    )
    return {"error": payload["error"], "guidance": payload["guidance"]}


def _created_id(kind: str, result: dict[str, Any]) -> str | None:
    id_fields = {
        "time": "time_contract_id",
        "dimension": "dimension_contract_id",
        "enum_set": "enum_set_contract_id",
        "entity": "entity_contract_id",
        "process_object": "process_contract_id",
        "metric": "metric_contract_id",
        "binding": "binding_id",
    }
    value = result.get(id_fields[kind])
    return str(value) if value is not None else None


def _run_route_action(
    action: Callable[[], dict[str, Any]],
    *,
    request: Request | None = None,
    value_error_status: int = 422,
    structured_value_error: bool = False,
) -> dict[str, Any]:
    try:
        return action()
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        error_code = getattr(error, "code", None)
        error_category = getattr(error, "category", None)
        if structured_value_error and isinstance(error_code, str):
            raise HTTPException(
                status_code=int(getattr(error, "status_code", None) or value_error_status),
                detail=build_service_validation_error_payload(
                    request=request,
                    message=str(error),
                    code=error_code,
                    category=error_category,
                    field_path=getattr(error, "field_path", None),
                    remediation=getattr(error, "remediation", None),
                    examples=getattr(error, "examples", None),
                ),
            ) from error
        raise HTTPException(status_code=value_error_status, detail=str(error)) from error


@router.post("/semantic/batch", response_model=SemanticBatchResponse)
def semantic_batch(request: Request, payload: SemanticBatchRequest = Body(...)) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    items: list[dict[str, Any]] = []
    created_ids: dict[str, str] = {}
    summary = {"total": len(payload.items), "succeeded": 0, "failed": 0, "skipped": 0}
    stop_after_error = False

    def create_item(kind: str, item_payload: dict[str, Any]) -> dict[str, Any]:
        if kind == "time":
            return semantic_service.create_time_semantic(
                TimeCreateRequest.model_validate(item_payload)
            )
        if kind == "dimension":
            return semantic_service.create_dimension(
                DimensionCreateRequest.model_validate(item_payload)
            )
        if kind == "enum_set":
            return semantic_service.create_enum_set(
                EnumSetCreateRequest.model_validate(item_payload)
            )
        if kind == "entity":
            return semantic_service.create_typed_entity(
                TypedEntityCreateRequest.model_validate(item_payload)
            )
        if kind == "process_object":
            return semantic_service.create_process_object(
                ProcessObjectCreateRequest.model_validate(item_payload)
            )
        if kind == "metric":
            return semantic_service.create_typed_metric(
                TypedMetricCreateRequest.model_validate(item_payload)
            )
        if kind == "binding":
            return semantic_service.create_typed_binding(
                TypedBindingCreateRequest.model_validate(item_payload)
            )
        raise ValueError(f"Unsupported batch kind: {kind}")

    def validate_item(kind: str, object_id: str) -> dict[str, Any]:
        if kind == "time":
            return semantic_service.validate_time_semantic(object_id)
        if kind == "dimension":
            return semantic_service.validate_dimension(object_id)
        if kind == "enum_set":
            return semantic_service.validate_enum_set(object_id)
        if kind == "entity":
            return semantic_service.validate_typed_entity(object_id)
        if kind == "process_object":
            return semantic_service.validate_process_object(object_id)
        if kind == "metric":
            return semantic_service.validate_typed_metric(object_id)
        if kind == "binding":
            return semantic_service.validate_typed_binding(object_id)
        raise ValueError(f"Unsupported batch kind: {kind}")

    def activate_item(kind: str, object_id: str) -> dict[str, Any]:
        if kind == "time":
            return semantic_service.activate_time_semantic(object_id)
        if kind == "dimension":
            return semantic_service.activate_dimension(object_id)
        if kind == "enum_set":
            return semantic_service.activate_enum_set(object_id)
        if kind == "entity":
            return semantic_service.activate_typed_entity(object_id)
        if kind == "process_object":
            return semantic_service.activate_process_object(object_id)
        if kind == "metric":
            return semantic_service.activate_typed_metric(object_id)
        if kind == "binding":
            return semantic_service.activate_typed_binding(object_id)
        raise ValueError(f"Unsupported batch kind: {kind}")

    if (
        payload.mode == "apply"
        and payload.lifecycle == "create_validate_activate"
        and all(item.action == "create" for item in payload.items)
    ):
        records: list[dict[str, Any]] = []
        ordered_items = sorted(
            enumerate(payload.items), key=lambda pair: (_BATCH_CREATE_ORDER[pair[1].kind], pair[0])
        )
        for index, item in ordered_items:
            if stop_after_error:
                summary["skipped"] += 1
                items.append(
                    {
                        "op_key": item.op_key,
                        "kind": item.kind,
                        "action": item.action,
                        "status": "skipped",
                        "result": None,
                        "error": {
                            "code": "semantic_batch_skipped",
                            "message": "Skipped after previous error.",
                        },
                        "guidance": None,
                        "coverage": None,
                    }
                )
                continue
            try:
                item_payload = _merge_batch_defaults(dict(item.payload), payload)
                _CREATE_MODEL_BY_BATCH_KIND[item.kind].model_validate(item_payload)
                created = create_item(item.kind, item_payload)
                object_id = _created_id(item.kind, created)
                if object_id is not None:
                    created_ids[item.op_key] = object_id
                records.append(
                    {
                        "item": item,
                        "index": index,
                        "object_id": object_id or item.op_key,
                        "create_result": created,
                    }
                )
            except ValidationError as error:
                summary["failed"] += 1
                detail = sanitize_validation_errors(error)
                err = _batch_error_payload(
                    "Batch item request validation failed.", "request_validation_error"
                )
                err["error"]["detail"] = detail
                items.append(
                    {
                        "op_key": item.op_key,
                        "kind": item.kind,
                        "action": item.action,
                        "status": "failed",
                        "result": None,
                        "error": err["error"],
                        "guidance": err["guidance"],
                        "coverage": None,
                    }
                )
                stop_after_error = not payload.continue_on_error
            except (KeyError, ValueError) as error:
                summary["failed"] += 1
                err = _batch_error_payload(
                    str(error),
                    str(getattr(error, "code", None) or "semantic_batch_item_failed"),
                    remediation=getattr(error, "remediation", None),
                    examples=getattr(error, "examples", None),
                )
                items.append(
                    {
                        "op_key": item.op_key,
                        "kind": item.kind,
                        "action": item.action,
                        "status": "failed",
                        "result": None,
                        "error": err["error"],
                        "guidance": err["guidance"],
                        "coverage": None,
                    }
                )
                stop_after_error = not payload.continue_on_error

        readiness_counts: dict[str, int] = {}
        deferred_metric_records: list[dict[str, Any]] = []
        for record in sorted(
            records,
            key=lambda item_record: (
                _BATCH_ACTIVATE_ORDER[item_record["item"].kind],
                item_record["index"],
            ),
        ):
            item = record["item"]
            try:
                validate_item(item.kind, record["object_id"])
                activation_result = activate_item(item.kind, record["object_id"])
                if item.kind == "metric":
                    deferred_metric_records.append(record)
                    continue
                readiness = activation_result.get("readiness_status")
                if isinstance(readiness, str):
                    readiness_counts[readiness] = readiness_counts.get(readiness, 0) + 1
                summary["succeeded"] += 1
                items.append(
                    {
                        "op_key": item.op_key,
                        "kind": item.kind,
                        "action": item.action,
                        "status": "succeeded",
                        "result": activation_result,
                        "error": None,
                        "guidance": None,
                        "coverage": _coverage_from_result(activation_result),
                    }
                )
            except (KeyError, ValueError) as error:
                summary["failed"] += 1
                err = _batch_error_payload(
                    str(error),
                    str(getattr(error, "code", None) or "semantic_batch_item_failed"),
                    remediation=getattr(error, "remediation", None),
                    examples=getattr(error, "examples", None),
                )
                items.append(
                    {
                        "op_key": item.op_key,
                        "kind": item.kind,
                        "action": item.action,
                        "status": "failed",
                        "result": None,
                        "error": err["error"],
                        "guidance": err["guidance"],
                        "coverage": None,
                    }
                )
                if not payload.continue_on_error:
                    break
        final_metrics: list[dict[str, Any]] = []
        for record in deferred_metric_records:
            item = record["item"]
            try:
                final = validate_item("metric", record["object_id"])
            except (KeyError, ValueError) as error:
                summary["failed"] += 1
                err = _batch_error_payload(
                    str(error),
                    str(getattr(error, "code", None) or "semantic_batch_item_failed"),
                    remediation=getattr(error, "remediation", None),
                    examples=getattr(error, "examples", None),
                )
                items.append(
                    {
                        "op_key": item.op_key,
                        "kind": item.kind,
                        "action": item.action,
                        "status": "failed",
                        "result": None,
                        "error": err["error"],
                        "guidance": err["guidance"],
                        "coverage": None,
                    }
                )
                if not payload.continue_on_error:
                    break
                continue
            final_object = final.get("semantic_object") if isinstance(final, dict) else None
            if not isinstance(final_object, dict):
                final_object = final
            readiness = final_object.get("readiness_status")
            if isinstance(readiness, str):
                readiness_counts[readiness] = readiness_counts.get(readiness, 0) + 1
            summary["succeeded"] += 1
            items.append(
                {
                    "op_key": item.op_key,
                    "kind": item.kind,
                    "action": item.action,
                    "status": "succeeded",
                    "result": final_object,
                    "error": None,
                    "guidance": None,
                    "coverage": _coverage_from_result(final_object),
                }
            )
            final_metrics.append(
                {
                    "op_key": item.op_key,
                    "object_id": record["object_id"],
                    "readiness_status": final_object.get("readiness_status"),
                    "blocking_requirements": final_object.get("blocking_requirements", []),
                }
            )
        return {
            "ok": summary["failed"] == 0,
            "mode": payload.mode,
            "summary": summary,
            "items": items,
            "readiness_summary": {
                "counts": readiness_counts,
                "final_metrics": final_metrics,
                "activation_order": [
                    record["item"].op_key
                    for record in sorted(
                        records,
                        key=lambda item_record: (
                            _BATCH_ACTIVATE_ORDER[item_record["item"].kind],
                            item_record["index"],
                        ),
                    )
                ],
            },
        }

    for item in payload.items:
        if stop_after_error:
            summary["skipped"] += 1
            items.append(
                {
                    "op_key": item.op_key,
                    "kind": item.kind,
                    "action": item.action,
                    "status": "skipped",
                    "result": None,
                    "error": {
                        "code": "semantic_batch_skipped",
                        "message": "Skipped after previous error.",
                    },
                    "guidance": None,
                    "coverage": None,
                }
            )
            continue
        try:
            item_payload = _merge_batch_defaults(dict(item.payload), payload)
            model = _CREATE_MODEL_BY_BATCH_KIND[item.kind]
            item_result: dict[str, Any] | None = None
            if item.action == "create":
                model.model_validate(item_payload)
                if payload.mode == "dry_run":
                    if item.kind == "binding":
                        parsed_binding = TypedBindingCreateRequest.model_validate(item_payload)
                        binding_ref = parsed_binding.header.binding_ref
                        binding_scope = parsed_binding.header.binding_scope
                        bound_object_ref = parsed_binding.header.bound_object_ref
                        interface_contract = parsed_binding.interface_contract.model_dump(
                            mode="json"
                        )

                        def validate_binding_payload(
                            binding_ref: str = binding_ref,
                            binding_scope: str = binding_scope,
                            bound_object_ref: str = bound_object_ref,
                            interface_contract: dict[str, Any] = interface_contract,
                        ) -> None:
                            semantic_service.bindings._validate_typed_binding_contract(
                                binding_ref=binding_ref,
                                binding_scope=binding_scope,
                                bound_object_ref=bound_object_ref,
                                interface_contract=interface_contract,
                                require_published_dependencies=False,
                            )

                        semantic_service._invoke(validate_binding_payload)
                    item_result = {"would_create": True, "payload": item_payload}
                else:
                    item_result = create_item(item.kind, item_payload)
                    object_id = _created_id(item.kind, item_result)
                    if object_id is not None:
                        created_ids[item.op_key] = object_id
                    if payload.lifecycle in {"create_and_validate", "create_validate_activate"}:
                        item_result = validate_item(item.kind, object_id or item.op_key)
                    if payload.lifecycle == "create_validate_activate":
                        item_result = activate_item(item.kind, object_id or item.op_key)
            else:
                object_id = str(
                    item_payload.get("id")
                    or item_payload.get("object_id")
                    or created_ids.get(item.op_key)
                    or item.op_key
                )
                if payload.mode == "dry_run":
                    item_result = {"would_run": item.action, "object_id": object_id}
                elif item.action == "validate":
                    item_result = validate_item(item.kind, object_id)
                else:
                    item_result = activate_item(item.kind, object_id)
            summary["succeeded"] += 1
            items.append(
                {
                    "op_key": item.op_key,
                    "kind": item.kind,
                    "action": item.action,
                    "status": "succeeded",
                    "result": item_result,
                    "error": None,
                    "guidance": None,
                    "coverage": _coverage_from_result(item_result),
                }
            )
        except ValidationError as error:
            summary["failed"] += 1
            detail = sanitize_validation_errors(error)
            err = _batch_error_payload(
                "Batch item request validation failed.", "request_validation_error"
            )
            err["error"]["detail"] = detail
            items.append(
                {
                    "op_key": item.op_key,
                    "kind": item.kind,
                    "action": item.action,
                    "status": "failed",
                    "result": None,
                    "error": err["error"],
                    "guidance": err["guidance"],
                    "coverage": None,
                }
            )
            stop_after_error = not payload.continue_on_error
        except (KeyError, ValueError) as error:
            summary["failed"] += 1
            err = _batch_error_payload(
                str(error),
                str(getattr(error, "code", None) or "semantic_batch_item_failed"),
                remediation=getattr(error, "remediation", None),
                examples=getattr(error, "examples", None),
            )
            items.append(
                {
                    "op_key": item.op_key,
                    "kind": item.kind,
                    "action": item.action,
                    "status": "failed",
                    "result": None,
                    "error": err["error"],
                    "guidance": err["guidance"],
                    "coverage": None,
                }
            )
            stop_after_error = not payload.continue_on_error
    return {"ok": summary["failed"] == 0, "mode": payload.mode, "summary": summary, "items": items}


@router.get("/semantic/grains")
def list_grains(request: Request) -> dict[str, Any]:
    """List grain refs observed in metric headers, process objects, and carrier bindings."""
    return get_services(request).semantic_service.list_grains()


@router.post("/semantic/entities", response_model=TypedEntityResponse)
def create_entity(
    request: Request, payload: TypedEntityCreateRequest = Body(...)
) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(lambda: semantic_service.create_typed_entity(payload))


@router.get("/semantic/entities")
def list_entities(
    request: Request,
    status: str | None = Query(default=None),
    lifecycle_status: str | None = Query(default=None),
    readiness_status: str | None = Query(default=None),
    detail: bool = Query(
        default=False, description="Return full detail instead of lightweight format."
    ),
) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(
        lambda: semantic_service.list_typed_entities(
            status=status,
            lifecycle_status=lifecycle_status,
            readiness_status=readiness_status,
            detail=detail,
        )
    )


@router.get("/semantic/entities/{entity_id}", response_model=TypedEntityResponse)
def get_entity(
    entity_id: str,
    request: Request,
) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(lambda: semantic_service.read_typed_entity(entity_id))


@router.put("/semantic/entities/{entity_id}", response_model=TypedEntityResponse)
def update_entity(
    entity_id: str,
    request: Request,
    payload: TypedEntityUpdateRequest = Body(...),
) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(lambda: semantic_service.update_typed_entity(entity_id, payload))


@router.post("/semantic/entities/{entity_id}/publish")
def publish_entity(entity_id: str, request: Request) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(
        lambda: semantic_service.publish_typed_entity(entity_id),
        structured_value_error=True,
    )


@router.post(
    "/semantic/entities/{entity_id}/validate", response_model=SemanticValidateActionResponse
)
def validate_entity(entity_id: str, request: Request) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(
        lambda: semantic_service.validate_typed_entity(entity_id),
        structured_value_error=True,
    )


@router.post("/semantic/entities/{entity_id}/activate", response_model=TypedEntityResponse)
def activate_entity(entity_id: str, request: Request) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(
        lambda: semantic_service.activate_typed_entity(entity_id),
        structured_value_error=True,
    )


@router.post("/semantic/entities/{entity_id}/deprecate", response_model=TypedEntityResponse)
def deprecate_entity(entity_id: str, request: Request) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(
        lambda: semantic_service.deprecate_typed_entity(entity_id),
        structured_value_error=True,
    )


@router.post("/semantic/metrics", response_model=TypedMetricResponse)
def create_metric(
    request: Request, payload: TypedMetricCreateRequest = Body(...)
) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(
        lambda: semantic_service.create_typed_metric(payload),
        request=request,
        structured_value_error=True,
    )


@router.get("/semantic/metrics")
def list_metrics(
    request: Request,
    status: str | None = Query(default=None),
    lifecycle_status: str | None = Query(default=None),
    readiness_status: str | None = Query(default=None),
    detail: bool = Query(
        default=False, description="Return full detail instead of lightweight format."
    ),
) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(
        lambda: semantic_service.list_typed_metrics(
            status=status,
            lifecycle_status=lifecycle_status,
            readiness_status=readiness_status,
            detail=detail,
        )
    )


@router.post("/semantic/metrics/{metric_id_or_ref}/revisions", response_model=TypedMetricResponse)
def create_metric_revision(
    metric_id_or_ref: str,
    request: Request,
    payload: MetricRevisionCreateRequest = Body(...),
) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(
        lambda: semantic_service.create_metric_revision(metric_id_or_ref, payload),
        request=request,
        structured_value_error=True,
    )


@router.get("/semantic/metrics/{metric_ref}/revisions")
def list_metric_revisions(metric_ref: str, request: Request) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(lambda: semantic_service.list_metric_revisions(metric_ref))


@router.get(
    "/semantic/metrics/{metric_ref}/revisions/{revision}", response_model=TypedMetricResponse
)
def get_metric_revision(metric_ref: str, revision: int, request: Request) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(lambda: semantic_service.read_metric_revision(metric_ref, revision))


@router.post(
    "/semantic/metrics/{metric_id_or_ref}/revisions/{revision}/validate",
    response_model=SemanticValidateActionResponse,
)
def validate_metric_revision(
    metric_id_or_ref: str, revision: int, request: Request
) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(
        lambda: semantic_service.validate_metric_revision(metric_id_or_ref, revision),
        structured_value_error=True,
    )


@router.post(
    "/semantic/metrics/{metric_id_or_ref}/revisions/{revision}/activate",
    response_model=TypedMetricResponse,
)
def activate_metric_revision(
    metric_id_or_ref: str, revision: int, request: Request
) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(
        lambda: semantic_service.activate_metric_revision(metric_id_or_ref, revision),
        structured_value_error=True,
    )


@router.get("/semantic/metrics/{metric_id}", response_model=TypedMetricResponse)
def get_metric(
    metric_id: str,
    request: Request,
) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(lambda: semantic_service.read_typed_metric(metric_id))


@router.put("/semantic/metrics/{metric_id}", response_model=TypedMetricResponse)
def update_metric(
    metric_id: str,
    request: Request,
    payload: TypedMetricUpdateRequest = Body(...),
) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(
        lambda: semantic_service.update_typed_metric(metric_id, payload),
        request=request,
        structured_value_error=True,
    )


@router.post("/semantic/metrics/{metric_id}/publish")
def publish_metric(metric_id: str, request: Request) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(
        lambda: semantic_service.publish_typed_metric(metric_id),
        structured_value_error=True,
    )


@router.post(
    "/semantic/metrics/{metric_id}/validate", response_model=SemanticValidateActionResponse
)
def validate_metric(metric_id: str, request: Request) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(
        lambda: semantic_service.validate_typed_metric(metric_id),
        structured_value_error=True,
    )


@router.post("/semantic/metrics/{metric_id}/activate", response_model=TypedMetricResponse)
def activate_metric(metric_id: str, request: Request) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(
        lambda: semantic_service.activate_typed_metric(metric_id),
        structured_value_error=True,
    )


@router.post("/semantic/metrics/{metric_id}/deprecate", response_model=TypedMetricResponse)
def deprecate_metric(metric_id: str, request: Request) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(
        lambda: semantic_service.deprecate_typed_metric(metric_id),
        structured_value_error=True,
    )


@router.post("/semantic/process-objects", response_model=ProcessObjectResponse)
def create_process_object(
    request: Request, payload: ProcessObjectCreateRequest = Body(...)
) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(lambda: semantic_service.create_process_object(payload))


@router.get("/semantic/process-objects")
def list_process_objects(
    request: Request,
    status: str | None = Query(default=None),
    lifecycle_status: str | None = Query(default=None),
    readiness_status: str | None = Query(default=None),
    detail: bool = Query(
        default=False, description="Return full detail instead of lightweight format."
    ),
) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(
        lambda: semantic_service.list_process_objects(
            status=status,
            lifecycle_status=lifecycle_status,
            readiness_status=readiness_status,
            detail=detail,
        )
    )


@router.get("/semantic/process-objects/{process_contract_id}", response_model=ProcessObjectResponse)
def get_process_object(process_contract_id: str, request: Request) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(lambda: semantic_service.read_process_object(process_contract_id))


@router.put("/semantic/process-objects/{process_contract_id}", response_model=ProcessObjectResponse)
def update_process_object(
    process_contract_id: str,
    request: Request,
    payload: ProcessObjectUpdateRequest = Body(...),
) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(
        lambda: semantic_service.update_process_object(process_contract_id, payload)
    )


@router.post("/semantic/process-objects/{process_contract_id}/publish")
def publish_process_object(process_contract_id: str, request: Request) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(
        lambda: semantic_service.publish_process_object(process_contract_id),
        structured_value_error=True,
    )


@router.post(
    "/semantic/process-objects/{process_contract_id}/validate",
    response_model=SemanticValidateActionResponse,
)
def validate_process_object(process_contract_id: str, request: Request) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(
        lambda: semantic_service.validate_process_object(process_contract_id),
        structured_value_error=True,
    )


@router.post(
    "/semantic/process-objects/{process_contract_id}/activate",
    response_model=ProcessObjectResponse,
)
def activate_process_object(process_contract_id: str, request: Request) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(
        lambda: semantic_service.activate_process_object(process_contract_id),
        structured_value_error=True,
    )


@router.post(
    "/semantic/process-objects/{process_contract_id}/deprecate",
    response_model=ProcessObjectResponse,
)
def deprecate_process_object(process_contract_id: str, request: Request) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(
        lambda: semantic_service.deprecate_process_object(process_contract_id),
        structured_value_error=True,
    )


@router.post("/semantic/dimensions", response_model=DimensionResponse)
def create_dimension(
    request: Request, payload: DimensionCreateRequest = Body(...)
) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(lambda: semantic_service.create_dimension(payload))


@router.get("/semantic/dimensions")
def list_dimensions(
    request: Request,
    status: str | None = Query(default=None),
    lifecycle_status: str | None = Query(default=None),
    readiness_status: str | None = Query(default=None),
    detail: bool = Query(
        default=False, description="Return full detail instead of lightweight format."
    ),
) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(
        lambda: semantic_service.list_dimensions(
            status=status,
            lifecycle_status=lifecycle_status,
            readiness_status=readiness_status,
            detail=detail,
        )
    )


@router.get("/semantic/dimensions/{dimension_contract_id}", response_model=DimensionResponse)
def get_dimension(dimension_contract_id: str, request: Request) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(lambda: semantic_service.read_dimension(dimension_contract_id))


@router.put("/semantic/dimensions/{dimension_contract_id}", response_model=DimensionResponse)
def update_dimension(
    dimension_contract_id: str,
    request: Request,
    payload: DimensionUpdateRequest = Body(...),
) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(
        lambda: semantic_service.update_dimension(dimension_contract_id, payload)
    )


@router.post("/semantic/dimensions/{dimension_contract_id}/publish")
def publish_dimension(dimension_contract_id: str, request: Request) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(
        lambda: semantic_service.publish_dimension(dimension_contract_id),
        structured_value_error=True,
    )


@router.post(
    "/semantic/dimensions/{dimension_contract_id}/validate",
    response_model=SemanticValidateActionResponse,
)
def validate_dimension(dimension_contract_id: str, request: Request) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(
        lambda: semantic_service.validate_dimension(dimension_contract_id),
        structured_value_error=True,
    )


@router.post(
    "/semantic/dimensions/{dimension_contract_id}/activate", response_model=DimensionResponse
)
def activate_dimension(dimension_contract_id: str, request: Request) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(
        lambda: semantic_service.activate_dimension(dimension_contract_id),
        structured_value_error=True,
    )


@router.post(
    "/semantic/dimensions/{dimension_contract_id}/deprecate", response_model=DimensionResponse
)
def deprecate_dimension(dimension_contract_id: str, request: Request) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(
        lambda: semantic_service.deprecate_dimension(dimension_contract_id),
        structured_value_error=True,
    )


# ---------------------------------------------------------------------------
# Predicate CRUD
# ---------------------------------------------------------------------------


@router.post("/semantic/predicates", response_model=PredicateResponse)
def create_predicate(
    request: Request, payload: PredicateCreateRequest = Body(...)
) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(lambda: semantic_service.create_predicate(payload))


@router.get("/semantic/predicates")
def list_predicates(
    request: Request,
    status: str | None = Query(default=None),
    lifecycle_status: str | None = Query(default=None),
    readiness_status: str | None = Query(default=None),
    detail: bool = Query(
        default=False, description="Return full detail instead of lightweight format."
    ),
) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(
        lambda: semantic_service.list_predicates(
            status=status,
            lifecycle_status=lifecycle_status,
            readiness_status=readiness_status,
            detail=detail,
        )
    )


@router.get("/semantic/predicates/{predicate_contract_id}", response_model=PredicateResponse)
def get_predicate(predicate_contract_id: str, request: Request) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(lambda: semantic_service.read_predicate(predicate_contract_id))


@router.put("/semantic/predicates/{predicate_contract_id}", response_model=PredicateResponse)
def update_predicate(
    predicate_contract_id: str,
    request: Request,
    payload: PredicateUpdateRequest = Body(...),
) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(
        lambda: semantic_service.update_predicate(predicate_contract_id, payload)
    )


@router.post("/semantic/predicates/{predicate_contract_id}/publish")
def publish_predicate(predicate_contract_id: str, request: Request) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(
        lambda: semantic_service.publish_predicate(predicate_contract_id),
        structured_value_error=True,
    )


@router.post(
    "/semantic/predicates/{predicate_contract_id}/validate",
    response_model=SemanticValidateActionResponse,
)
def validate_predicate(predicate_contract_id: str, request: Request) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(
        lambda: semantic_service.validate_predicate(predicate_contract_id),
        structured_value_error=True,
    )


@router.post(
    "/semantic/predicates/{predicate_contract_id}/activate", response_model=PredicateResponse
)
def activate_predicate(predicate_contract_id: str, request: Request) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(
        lambda: semantic_service.activate_predicate(predicate_contract_id),
        structured_value_error=True,
    )


@router.post(
    "/semantic/predicates/{predicate_contract_id}/deprecate", response_model=PredicateResponse
)
def deprecate_predicate(predicate_contract_id: str, request: Request) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(
        lambda: semantic_service.deprecate_predicate(predicate_contract_id),
        structured_value_error=True,
    )


@router.post("/semantic/time", response_model=TimeResponse)
def create_time_semantic(
    request: Request, payload: TimeCreateRequest = Body(...)
) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(lambda: semantic_service.create_time_semantic(payload))


@router.get("/semantic/time")
def list_time_semantics(
    request: Request,
    status: str | None = Query(default=None),
    lifecycle_status: str | None = Query(default=None),
    readiness_status: str | None = Query(default=None),
    detail: bool = Query(
        default=False, description="Return full detail instead of lightweight format."
    ),
) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(
        lambda: semantic_service.list_time_semantics(
            status=status,
            lifecycle_status=lifecycle_status,
            readiness_status=readiness_status,
            detail=detail,
        )
    )


@router.get("/semantic/time/{time_contract_id}", response_model=TimeResponse)
def get_time_semantic(time_contract_id: str, request: Request) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(lambda: semantic_service.read_time_semantic(time_contract_id))


@router.put("/semantic/time/{time_contract_id}", response_model=TimeResponse)
def update_time_semantic(
    time_contract_id: str,
    request: Request,
    payload: TimeUpdateRequest = Body(...),
) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(
        lambda: semantic_service.update_time_semantic(time_contract_id, payload)
    )


@router.post("/semantic/time/{time_contract_id}/publish")
def publish_time_semantic(time_contract_id: str, request: Request) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(
        lambda: semantic_service.publish_time_semantic(time_contract_id),
        structured_value_error=True,
    )


@router.post(
    "/semantic/time/{time_contract_id}/validate", response_model=SemanticValidateActionResponse
)
def validate_time_semantic(time_contract_id: str, request: Request) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(
        lambda: semantic_service.validate_time_semantic(time_contract_id),
        structured_value_error=True,
    )


@router.post("/semantic/time/{time_contract_id}/activate", response_model=TimeResponse)
def activate_time_semantic(time_contract_id: str, request: Request) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(
        lambda: semantic_service.activate_time_semantic(time_contract_id),
        structured_value_error=True,
    )


@router.post("/semantic/time/{time_contract_id}/deprecate", response_model=TimeResponse)
def deprecate_time_semantic(time_contract_id: str, request: Request) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(
        lambda: semantic_service.deprecate_time_semantic(time_contract_id),
        structured_value_error=True,
    )


@router.post("/semantic/enum-sets", response_model=EnumSetResponse)
def create_enum_set(request: Request, payload: EnumSetCreateRequest = Body(...)) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(lambda: semantic_service.create_enum_set(payload))


@router.get("/semantic/enum-sets")
def list_enum_sets(
    request: Request,
    status: str | None = Query(default=None),
    lifecycle_status: str | None = Query(default=None),
    readiness_status: str | None = Query(default=None),
    detail: bool = Query(
        default=False, description="Return full detail instead of lightweight format."
    ),
) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(
        lambda: semantic_service.list_enum_sets(
            status=status,
            lifecycle_status=lifecycle_status,
            readiness_status=readiness_status,
            detail=detail,
        )
    )


@router.get("/semantic/enum-sets/{enum_set_contract_id}", response_model=EnumSetResponse)
def get_enum_set(enum_set_contract_id: str, request: Request) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(lambda: semantic_service.read_enum_set(enum_set_contract_id))


@router.put("/semantic/enum-sets/{enum_set_contract_id}", response_model=EnumSetResponse)
def update_enum_set(
    enum_set_contract_id: str,
    request: Request,
    payload: EnumSetUpdateRequest = Body(...),
) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(
        lambda: semantic_service.update_enum_set(enum_set_contract_id, payload)
    )


@router.post("/semantic/enum-sets/{enum_set_contract_id}/publish")
def publish_enum_set(enum_set_contract_id: str, request: Request) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(
        lambda: semantic_service.publish_enum_set(enum_set_contract_id),
        structured_value_error=True,
    )


@router.post(
    "/semantic/enum-sets/{enum_set_contract_id}/validate",
    response_model=SemanticValidateActionResponse,
)
def validate_enum_set(enum_set_contract_id: str, request: Request) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(
        lambda: semantic_service.validate_enum_set(enum_set_contract_id),
        structured_value_error=True,
    )


@router.post("/semantic/enum-sets/{enum_set_contract_id}/activate", response_model=EnumSetResponse)
def activate_enum_set(enum_set_contract_id: str, request: Request) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(
        lambda: semantic_service.activate_enum_set(enum_set_contract_id),
        structured_value_error=True,
    )


@router.post("/semantic/enum-sets/{enum_set_contract_id}/deprecate", response_model=EnumSetResponse)
def deprecate_enum_set(enum_set_contract_id: str, request: Request) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(
        lambda: semantic_service.deprecate_enum_set(enum_set_contract_id),
        structured_value_error=True,
    )


@router.post("/semantic/bindings", response_model=TypedBindingResponse)
def create_typed_binding(
    request: Request, payload: TypedBindingCreateRequest = Body(...)
) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(
        lambda: semantic_service.create_typed_binding(payload),
        request=request,
        structured_value_error=True,
    )


@router.get("/semantic/bindings")
def list_typed_bindings(
    request: Request,
    status: str | None = Query(default=None),
    lifecycle_status: str | None = Query(default=None),
    readiness_status: str | None = Query(default=None),
    detail: bool = Query(
        default=False, description="Return full detail instead of lightweight format."
    ),
) -> dict[str, Any]:
    return _run_route_action(
        lambda: get_services(request).semantic_service.list_typed_bindings(
            status=status,
            lifecycle_status=lifecycle_status,
            readiness_status=readiness_status,
            detail=detail,
        )
    )


@router.get("/semantic/bindings/{binding_id}", response_model=TypedBindingResponse)
def get_typed_binding(binding_id: str, request: Request) -> dict[str, Any]:
    return _run_route_action(
        lambda: get_services(request).semantic_service.read_typed_binding(binding_id)
    )


@router.put("/semantic/bindings/{binding_id}", response_model=TypedBindingResponse)
def update_typed_binding(
    binding_id: str,
    request: Request,
    payload: TypedBindingUpdateRequest = Body(...),
) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(
        lambda: semantic_service.update_typed_binding(binding_id, payload),
        request=request,
        structured_value_error=True,
    )


@router.post("/semantic/bindings/{binding_id}/publish")
def publish_typed_binding(binding_id: str, request: Request) -> dict[str, Any]:
    return _run_route_action(
        lambda: get_services(request).semantic_service.publish_typed_binding(binding_id),
        structured_value_error=True,
    )


@router.post(
    "/semantic/bindings/{binding_id}/validate", response_model=SemanticValidateActionResponse
)
def validate_typed_binding(binding_id: str, request: Request) -> dict[str, Any]:
    return _run_route_action(
        lambda: get_services(request).semantic_service.validate_typed_binding(binding_id),
        structured_value_error=True,
    )


@router.post("/semantic/bindings/{binding_id}/activate", response_model=TypedBindingResponse)
def activate_typed_binding(binding_id: str, request: Request) -> dict[str, Any]:
    return _run_route_action(
        lambda: get_services(request).semantic_service.activate_typed_binding(binding_id),
        structured_value_error=True,
    )


@router.post("/semantic/bindings/{binding_id}/deprecate", response_model=TypedBindingResponse)
def deprecate_typed_binding(binding_id: str, request: Request) -> dict[str, Any]:
    return _run_route_action(
        lambda: get_services(request).semantic_service.deprecate_typed_binding(binding_id),
        structured_value_error=True,
    )


@router.post("/compiler/compatibility-profiles", response_model=CompatibilityProfileResponse)
def create_compatibility_profile(
    request: Request, payload: CompatibilityProfileCreateRequest = Body(...)
) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(lambda: semantic_service.create_compatibility_profile(payload))


@router.get("/compiler/compatibility-profiles")
def list_compatibility_profiles(
    request: Request,
    status: str | None = Query(default=None),
    lifecycle_status: str | None = Query(default=None),
    readiness_status: str | None = Query(default=None),
    detail: bool = Query(
        default=False, description="Return full detail instead of lightweight format."
    ),
) -> dict[str, Any]:
    return _run_route_action(
        lambda: get_services(request).semantic_service.list_compatibility_profiles(
            status=status,
            lifecycle_status=lifecycle_status,
            readiness_status=readiness_status,
            detail=detail,
        )
    )


@router.get(
    "/compiler/compatibility-profiles/{profile_id}", response_model=CompatibilityProfileResponse
)
def get_compatibility_profile(profile_id: str, request: Request) -> dict[str, Any]:
    return _run_route_action(
        lambda: get_services(request).semantic_service.read_compatibility_profile(profile_id)
    )


@router.put(
    "/compiler/compatibility-profiles/{profile_id}", response_model=CompatibilityProfileResponse
)
def update_compatibility_profile(
    profile_id: str,
    request: Request,
    payload: CompatibilityProfileUpdateRequest = Body(...),
) -> dict[str, Any]:
    semantic_service = get_services(request).semantic_service
    return _run_route_action(
        lambda: semantic_service.update_compatibility_profile(profile_id, payload)
    )


@router.post("/compiler/compatibility-profiles/{profile_id}/publish")
def publish_compatibility_profile(profile_id: str, request: Request) -> dict[str, Any]:
    return _run_route_action(
        lambda: get_services(request).semantic_service.publish_compatibility_profile(profile_id),
        structured_value_error=True,
    )


@router.post(
    "/compiler/compatibility-profiles/{profile_id}/validate",
    response_model=SemanticValidateActionResponse,
)
def validate_compatibility_profile(profile_id: str, request: Request) -> dict[str, Any]:
    return _run_route_action(
        lambda: get_services(request).semantic_service.validate_compatibility_profile(profile_id),
        structured_value_error=True,
    )


@router.post(
    "/compiler/compatibility-profiles/{profile_id}/activate",
    response_model=CompatibilityProfileResponse,
)
def activate_compatibility_profile(profile_id: str, request: Request) -> dict[str, Any]:
    return _run_route_action(
        lambda: get_services(request).semantic_service.activate_compatibility_profile(profile_id),
        structured_value_error=True,
    )


@router.post(
    "/compiler/compatibility-profiles/{profile_id}/deprecate",
    response_model=CompatibilityProfileResponse,
)
def deprecate_compatibility_profile(profile_id: str, request: Request) -> dict[str, Any]:
    return _run_route_action(
        lambda: get_services(request).semantic_service.deprecate_compatibility_profile(profile_id),
        structured_value_error=True,
    )
