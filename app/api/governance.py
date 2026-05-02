from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from app.api.deps import get_services, require_governance
from app.api.models import (
    GovernanceCheckRequest,
    GovernanceCheckResponse,
    PolicyCreateRequest,
    PolicyDeleteResponse,
    PolicyResponse,
    PolicyUpdateRequest,
    QualityRuleCreateRequest,
    QualityRuleDeleteResponse,
    QualityRuleResponse,
)

router = APIRouter()


@router.post("/policies", response_model=PolicyResponse)
def create_policy(payload: PolicyCreateRequest, request: Request) -> PolicyResponse:
    governance = require_governance(get_services(request))
    try:
        result = governance.create_policy(
            name=payload.name,
            policy_type=payload.policy_type,
            definition=payload.definition.model_dump(),
            scope=payload.scope.model_dump(),
        )
        return PolicyResponse.model_validate(result)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.get("/policies", response_model=list[PolicyResponse])
def list_policies(request: Request) -> list[PolicyResponse]:
    services = get_services(request)
    if services.governance_service is None:
        return []
    rows = services.governance_service.list_policies(enabled_only=False)
    return [PolicyResponse.model_validate(r) for r in rows]


@router.get("/policies/{policy_id}", response_model=PolicyResponse)
def get_policy(policy_id: str, request: Request) -> PolicyResponse:
    governance = require_governance(get_services(request))
    try:
        return PolicyResponse.model_validate(governance.get_policy(policy_id))
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.put("/policies/{policy_id}", response_model=PolicyResponse)
def update_policy(policy_id: str, payload: PolicyUpdateRequest, request: Request) -> PolicyResponse:
    governance = require_governance(get_services(request))
    defn = payload.definition.model_dump() if payload.definition is not None else None
    try:
        result = governance.update_policy(policy_id, enabled=payload.enabled, definition=defn)
        return PolicyResponse.model_validate(result)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.delete("/policies/{policy_id}", response_model=PolicyDeleteResponse)
def delete_policy(policy_id: str, request: Request) -> PolicyDeleteResponse:
    governance = require_governance(get_services(request))
    try:
        governance.delete_policy(policy_id)
        return PolicyDeleteResponse(status="deleted", policy_id=policy_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.post("/quality-rules", response_model=QualityRuleResponse)
def create_quality_rule(payload: QualityRuleCreateRequest, request: Request) -> QualityRuleResponse:
    governance = require_governance(get_services(request))
    try:
        result = governance.create_quality_rule(
            name=payload.name,
            rule_type=payload.rule_type,
            table_name=payload.table_name,
            threshold=payload.threshold.model_dump(),
            severity=payload.severity,
        )
        return QualityRuleResponse.model_validate(result)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.get("/quality-rules", response_model=list[QualityRuleResponse])
def list_quality_rules(
    request: Request, table: str | None = Query(default=None)
) -> list[QualityRuleResponse]:
    services = get_services(request)
    if services.governance_service is None:
        return []
    rows = services.governance_service.list_quality_rules(table_name=table)
    return [QualityRuleResponse.model_validate(r) for r in rows]


@router.delete("/quality-rules/{rule_id}", response_model=QualityRuleDeleteResponse)
def delete_quality_rule(rule_id: str, request: Request) -> QualityRuleDeleteResponse:
    governance = require_governance(get_services(request))
    try:
        governance.delete_quality_rule(rule_id)
        return QualityRuleDeleteResponse(status="deleted", rule_id=rule_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.post("/governance/check", response_model=GovernanceCheckResponse)
def governance_check(payload: GovernanceCheckRequest, request: Request) -> GovernanceCheckResponse:
    services = get_services(request)
    if services.governance_service is None:
        return GovernanceCheckResponse(passed=True)
    result = services.governance_service.check_step(
        session_id=payload.session_id,
        step_type=payload.step_type,
        params=payload.params,
    )
    return GovernanceCheckResponse.model_validate(result)
