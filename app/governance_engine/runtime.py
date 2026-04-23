"""Governance runtime: policy checks, data quality, and audit hooks."""

from __future__ import annotations

import json
import time
from datetime import date
from typing import Any

from app.governance_engine.repository import GovernanceRepository
from app.observability import MetricsCollector, observability_context
from app.runtime_contracts import PolicyApplicationResult, PolicyDecision
from app.storage.analytics import AnalyticsEngine

VALID_POLICY_TYPES = ("aggregate_only", "field_mask", "row_filter", "max_rows")
VALID_QUALITY_RULE_TYPES = ("freshness", "null_rate", "row_count_min")
VALID_QUALITY_SEVERITIES = ("warn", "block")


def policy_matches_scope(
    policy: dict[str, Any],
    *,
    step_type: str | None = None,
    tables: set[str] | None = None,
) -> bool:
    """Check whether a governance policy's scope matches the given context."""
    scope = policy.get("scope", {})
    if scope.get("step_types") and step_type not in scope["step_types"]:
        return False
    scope_tables = scope.get("tables")
    return not (scope_tables and tables is not None and tables and not tables & set(scope_tables))


class GovernanceRuntime:
    def __init__(
        self,
        repository: GovernanceRepository,
        analytics: AnalyticsEngine,
        metrics: MetricsCollector | None = None,
    ) -> None:
        self.repository = repository
        self.analytics = analytics
        self.metrics = metrics

    def create_policy(
        self,
        name: str,
        policy_type: str,
        definition: dict[str, Any] | None = None,
        scope: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if policy_type not in VALID_POLICY_TYPES:
            raise ValueError(
                f"Invalid policy_type: {policy_type}. Must be one of {VALID_POLICY_TYPES}"
            )
        return self.repository.create_policy(
            name=name,
            policy_type=policy_type,
            definition=definition or {},
            scope=scope or {},
        )

    def list_policies(self, enabled_only: bool = True) -> list[dict[str, Any]]:
        return self.repository.list_policies(enabled_only=enabled_only)

    def get_policy(self, policy_id: str) -> dict[str, Any]:
        return self.repository.get_policy(policy_id)

    def update_policy(
        self,
        policy_id: str,
        enabled: bool | None = None,
        definition: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.repository.update_policy(policy_id, enabled=enabled, definition=definition)

    def delete_policy(self, policy_id: str) -> dict[str, str]:
        return self.repository.delete_policy(policy_id)

    def create_quality_rule(
        self,
        name: str,
        rule_type: str,
        table_name: str,
        threshold: dict[str, Any],
        severity: str = "warn",
    ) -> dict[str, Any]:
        if rule_type not in VALID_QUALITY_RULE_TYPES:
            raise ValueError(
                f"Invalid rule_type: {rule_type}. Must be one of {VALID_QUALITY_RULE_TYPES}"
            )
        if severity not in VALID_QUALITY_SEVERITIES:
            raise ValueError(f"Invalid severity: {severity}. Must be 'warn' or 'block'")
        return self.repository.create_quality_rule(
            name=name,
            rule_type=rule_type,
            table_name=table_name,
            threshold=threshold,
            severity=severity,
        )

    def list_quality_rules(self, table_name: str | None = None) -> list[dict[str, Any]]:
        return self.repository.list_quality_rules(table_name=table_name)

    def delete_quality_rule(self, rule_id: str) -> dict[str, str]:
        return self.repository.delete_quality_rule(rule_id)

    def check_policies(
        self,
        session_id: str,
        step_type: str,
        params: dict[str, Any] | None = None,
        tables: list[str] | None = None,
    ) -> dict[str, Any]:
        """Check governance policies and compute transforms.

        Priority ordering (per predicate-schema-contract):
        1. governance_policy_filters — highest, non-overridable, non-bypassable
        2. carrier_row_filters — carrier consumption invariants
        3. request_scope_constraints — per-request narrowing only
        4. metric_default_predicates + component_qualifier_predicates — baseline

        Conflict rule: governance policy conflicts with metric/binding predicates
        are readiness failures, not silent empty results.
        """
        del session_id
        policies = self.list_policies(enabled_only=True)
        decisions: list[PolicyDecision] = []
        transforms: dict[str, Any] = {
            "aggregate_only": False,
            "masked_fields": [],
            "row_filters": [],
            "max_rows_scanned": None,
        }

        queried_tables: set[str] = set(tables or [])
        if params and params.get("table_name"):
            queried_tables.add(str(params["table_name"]))

        for policy in policies:
            scope = policy.get("scope", {})
            if not policy_matches_scope(policy, step_type=step_type, tables=queried_tables or None):
                continue

            policy_type = policy["policy_type"]
            if policy_type == "aggregate_only":
                transforms["aggregate_only"] = True
                decisions.append(
                    PolicyDecision(
                        code="aggregate_only_enabled",
                        decision="apply",
                        effect="apply",
                        policy_id=policy["policy_id"],
                        policy_name=policy["name"],
                        policy_type=policy_type,
                        scope=scope,
                        message=f"Policy '{policy['name']}' requires aggregate-only access mode.",
                    )
                )
                if step_type == "sample_rows":
                    decisions.append(
                        PolicyDecision(
                            code="aggregate_only_forbids_sample_rows",
                            decision="deny",
                            effect="block",
                            policy_id=policy["policy_id"],
                            policy_name=policy["name"],
                            policy_type=policy_type,
                            scope=scope,
                            message=(
                                f"Policy '{policy['name']}' forbids sample_rows "
                                "(aggregate-only mode)."
                            ),
                            detail={"step_type": step_type},
                        )
                    )
            elif policy_type == "field_mask":
                masked_fields = policy.get("definition", {}).get("fields", [])
                if masked_fields:
                    transforms["masked_fields"].extend(
                        field for field in masked_fields if field not in transforms["masked_fields"]
                    )
                    decisions.append(
                        PolicyDecision(
                            code="field_mask_applied",
                            decision="apply",
                            effect="apply",
                            policy_id=policy["policy_id"],
                            policy_name=policy["name"],
                            policy_type=policy_type,
                            scope=scope,
                            message=f"Policy '{policy['name']}' marks fields {masked_fields} as sensitive.",
                            detail={"fields": list(masked_fields)},
                        )
                    )
                if params and masked_fields:
                    params_str = json.dumps(params)
                    for field in masked_fields:
                        if field in params_str:
                            decisions.append(
                                PolicyDecision(
                                    code="field_mask_blocks_sensitive_field",
                                    decision="deny",
                                    effect="block",
                                    policy_id=policy["policy_id"],
                                    policy_name=policy["name"],
                                    policy_type=policy_type,
                                    scope=scope,
                                    message=f"Policy '{policy['name']}' forbids access to field '{field}'.",
                                    detail={"field": field},
                                )
                            )
            elif policy_type == "row_filter":
                definition = policy.get("definition", {})
                predicate_ref = definition.get("predicate_ref")
                filter_expression = (
                    definition.get("sql") or definition.get("predicate") or predicate_ref or ""
                )
                if filter_expression:
                    transforms["row_filters"].append(
                        {
                            "policy_id": policy["policy_id"],
                            "policy_name": policy["name"],
                            "expression": filter_expression,
                        }
                    )
                    decisions.append(
                        PolicyDecision(
                            code="row_filter_applied",
                            decision="apply",
                            effect="apply",
                            policy_id=policy["policy_id"],
                            policy_name=policy["name"],
                            policy_type=policy_type,
                            scope=scope,
                            message=f"Policy '{policy['name']}' contributes a row filter constraint.",
                            detail={"expression": filter_expression},
                        )
                    )
            elif policy_type == "max_rows":
                max_rows = policy.get("definition", {}).get("max_rows_scanned", 0)
                if max_rows > 0 and params:
                    existing_limit = transforms.get("max_rows_scanned")
                    transforms["max_rows_scanned"] = (
                        max_rows if existing_limit is None else min(existing_limit, max_rows)
                    )
                    table_name = params.get("table_name")
                    if table_name:
                        try:
                            count = self.analytics.table_row_count(table_name)
                        except Exception:
                            count = None
                        if count is not None and count > max_rows:
                            decisions.append(
                                PolicyDecision(
                                    code="max_rows_exceeded",
                                    decision="deny",
                                    effect="block",
                                    policy_id=policy["policy_id"],
                                    policy_name=policy["name"],
                                    policy_type=policy_type,
                                    scope=scope,
                                    message=(
                                        f"Policy '{policy['name']}': table "
                                        f"'{table_name}' has {count} rows "
                                        f"exceeding limit of {max_rows}."
                                    ),
                                    detail={
                                        "table_name": table_name,
                                        "row_count": count,
                                        "max_rows_scanned": max_rows,
                                    },
                                )
                            )

        return PolicyApplicationResult(decisions=decisions, transforms=transforms).to_dict()

    def check_quality(self, table_name: str) -> dict[str, Any]:
        rules = self.list_quality_rules(table_name=table_name)
        warnings: list[dict[str, str]] = []
        blockers: list[dict[str, str]] = []

        for rule in rules:
            rule_type = rule["rule_type"]
            threshold = rule["threshold"]
            issue: dict[str, str] | None = None

            try:
                if rule_type == "freshness":
                    max_age_hours = threshold.get("max_age_hours", 24)
                    rows = self.analytics.query_rows(
                        f"SELECT MAX(event_date) AS max_date FROM {table_name}"
                    )
                    if rows:
                        max_date = rows[0].get("max_date")
                        if max_date is not None:
                            if isinstance(max_date, str):
                                max_date = date.fromisoformat(max_date)
                            age_hours = (date.today() - max_date).total_seconds() / 3600
                            if age_hours > max_age_hours:
                                issue = {
                                    "rule_id": rule["rule_id"],
                                    "name": rule["name"],
                                    "message": (
                                        f"Quality rule '{rule['name']}': table '{table_name}' "
                                        f"data is {age_hours:.0f}h old (limit: {max_age_hours}h)."
                                    ),
                                }
                elif rule_type == "null_rate":
                    column = threshold.get("column", "")
                    max_null_rate = threshold.get("max_null_rate", 0.1)
                    if column:
                        stats = self.analytics.query_rows(
                            f"SELECT COUNT(*) AS total, COUNT({column}) AS non_null FROM {table_name}"
                        )
                        if stats:
                            total = stats[0]["total"]
                            non_null = stats[0]["non_null"]
                            null_rate = 1 - non_null / max(total, 1)
                            if null_rate > max_null_rate:
                                issue = {
                                    "rule_id": rule["rule_id"],
                                    "name": rule["name"],
                                    "message": (
                                        f"Quality rule '{rule['name']}': column '{column}' in '{table_name}' "
                                        f"has null_rate={null_rate:.4f} exceeding limit of {max_null_rate}."
                                    ),
                                }
                elif rule_type == "row_count_min":
                    min_rows = threshold.get("min_rows", 0)
                    count = self.analytics.table_row_count(table_name)
                    if count < min_rows:
                        issue = {
                            "rule_id": rule["rule_id"],
                            "name": rule["name"],
                            "message": (
                                f"Quality rule '{rule['name']}': table '{table_name}' "
                                f"has {count} rows, below minimum of {min_rows}."
                            ),
                        }
            except Exception:
                continue

            if issue is not None:
                if rule["severity"] == "block":
                    blockers.append(issue)
                else:
                    warnings.append(issue)

        return {"passed": len(blockers) == 0, "warnings": warnings, "blockers": blockers}

    def check_step(
        self,
        session_id: str,
        step_type: str,
        params: dict[str, Any] | None = None,
        tables: list[str] | None = None,
    ) -> dict[str, Any]:
        start = time.perf_counter()
        with observability_context(
            session_id=session_id,
            execution_stage="governance",
            governance_scope=step_type,
        ):
            policy_result = self.check_policies(session_id, step_type, params, tables=tables)
            all_violations = list(policy_result["violations"])

            quality_warnings: list[dict[str, str]] = []
            quality_blockers: list[dict[str, str]] = []
            if tables:
                for table in tables:
                    quality_result = self.check_quality(table)
                    quality_warnings.extend(quality_result["warnings"])
                    quality_blockers.extend(quality_result["blockers"])
                    all_violations.extend(quality_result["blockers"])

            passed = len(all_violations) == 0
            result = {
                "passed": passed,
                "violations": all_violations,
                "warnings": quality_warnings,
                "decisions": policy_result.get("decisions", []),
                "transforms": policy_result.get("transforms", {}),
                "hard_constraints": [
                    *policy_result.get("hard_constraints", []),
                    *quality_blockers,
                ],
                "soft_signals": [
                    *policy_result.get("soft_signals", []),
                    *quality_warnings,
                ],
            }
            self.repository.record_event(
                session_id=session_id,
                subject_type="step",
                subject_id=step_type,
                event_type="governance_step_checked",
                detail={
                    "passed": passed,
                    "table_count": len(tables or []),
                    "decision_count": len(result["decisions"]),
                    "violation_count": len(result["violations"]),
                },
            )
        if self.metrics is not None:
            self.metrics.record_execution_stage(
                "governance_check",
                (time.perf_counter() - start) * 1000,
                governance_policy=step_type,
            )
        return result

    def list_audit_events(
        self,
        *,
        subject_type: str | None = None,
        subject_id: str | None = None,
        session_id: str | None = None,
    ) -> list[dict[str, Any]]:
        return self.repository.list_events(
            subject_type=subject_type,
            subject_id=subject_id,
            session_id=session_id,
        )
