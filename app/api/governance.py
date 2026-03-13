from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from app.api.deps import get_services, require_governance
from app.api.models import GovernanceCheckRequest, PolicyCreateRequest, PolicyUpdateRequest, QualityRuleCreateRequest


router = APIRouter()


@router.post("/policies")
def create_policy(payload: PolicyCreateRequest, request: Request) -> dict[str, object]:
    governance = require_governance(get_services(request))
    try:
        return governance.create_policy(
            name=payload.name,
            policy_type=payload.policy_type,
            definition=payload.definition,
            scope=payload.scope,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.get("/policies")
def list_policies(request: Request) -> list[dict[str, object]]:
    services = get_services(request)
    if services.governance_service is None:
        return []
    return services.governance_service.list_policies(enabled_only=False)


@router.get("/policies/{policy_id}")
def get_policy(policy_id: str, request: Request) -> dict[str, object]:
    governance = require_governance(get_services(request))
    try:
        return governance.get_policy(policy_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.put("/policies/{policy_id}")
def update_policy(policy_id: str, payload: PolicyUpdateRequest, request: Request) -> dict[str, object]:
    governance = require_governance(get_services(request))
    try:
        return governance.update_policy(
            policy_id,
            enabled=payload.enabled,
            definition=payload.definition,
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.delete("/policies/{policy_id}")
def delete_policy(policy_id: str, request: Request) -> dict[str, object]:
    governance = require_governance(get_services(request))
    try:
        return governance.delete_policy(policy_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.post("/quality-rules")
def create_quality_rule(payload: QualityRuleCreateRequest, request: Request) -> dict[str, object]:
    governance = require_governance(get_services(request))
    try:
        return governance.create_quality_rule(
            name=payload.name,
            rule_type=payload.rule_type,
            table_name=payload.table_name,
            threshold=payload.threshold,
            severity=payload.severity,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.get("/quality-rules")
def list_quality_rules(request: Request, table: str | None = Query(default=None)) -> list[dict[str, object]]:
    services = get_services(request)
    if services.governance_service is None:
        return []
    return services.governance_service.list_quality_rules(table_name=table)


@router.delete("/quality-rules/{rule_id}")
def delete_quality_rule(rule_id: str, request: Request) -> dict[str, object]:
    governance = require_governance(get_services(request))
    try:
        return governance.delete_quality_rule(rule_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.post("/governance/check")
def governance_check(payload: GovernanceCheckRequest, request: Request) -> dict[str, object]:
    services = get_services(request)
    if services.governance_service is None:
        return {"passed": True, "violations": [], "warnings": []}
    return services.governance_service.check_step(
        session_id=payload.session_id,
        step_type=payload.step_type,
        params=payload.params,
    )
