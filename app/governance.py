"""Governance enforcement: policy checks and data quality rules."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from app.runtime_contracts import PolicyApplicationResult, PolicyDecision
from app.storage.analytics import AnalyticsEngine
from app.storage.metadata import MetadataStore


class GovernanceService:
    def __init__(self, metadata: MetadataStore, analytics: AnalyticsEngine) -> None:
        self.metadata = metadata
        self.analytics = analytics

    # ── Policy CRUD ──────────────────────────────────────────────

    def create_policy(
        self,
        name: str,
        policy_type: str,
        definition: dict[str, Any] | None = None,
        scope: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        valid_types = ("aggregate_only", "field_mask", "row_filter", "max_rows")
        if policy_type not in valid_types:
            raise ValueError(f"Invalid policy_type: {policy_type}. Must be one of {valid_types}")
        policy_id = f"pol_{uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()
        self.metadata.execute(
            """
            INSERT INTO policies (policy_id, name, policy_type, definition_json, scope_json, enabled, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 1, ?, ?)
            """,
            [policy_id, name, policy_type, json.dumps(definition or {}), json.dumps(scope or {}), now, now],
        )
        return {
            "policy_id": policy_id,
            "name": name,
            "policy_type": policy_type,
            "definition": definition or {},
            "scope": scope or {},
            "enabled": True,
        }

    def list_policies(self, enabled_only: bool = True) -> list[dict[str, Any]]:
        if enabled_only:
            rows = self.metadata.query_rows("SELECT * FROM policies WHERE enabled = 1 ORDER BY created_at")
        else:
            rows = self.metadata.query_rows("SELECT * FROM policies ORDER BY created_at")
        return [self._deserialize_policy(r) for r in rows]

    def get_policy(self, policy_id: str) -> dict[str, Any]:
        row = self.metadata.query_one("SELECT * FROM policies WHERE policy_id = ?", [policy_id])
        if row is None:
            raise KeyError(f"Unknown policy: {policy_id}")
        return self._deserialize_policy(row)

    def update_policy(
        self,
        policy_id: str,
        enabled: bool | None = None,
        definition: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.get_policy(policy_id)  # raises KeyError if missing
        now = datetime.now(timezone.utc).isoformat()
        if enabled is not None:
            self.metadata.execute(
                "UPDATE policies SET enabled = ?, updated_at = ? WHERE policy_id = ?",
                [1 if enabled else 0, now, policy_id],
            )
        if definition is not None:
            self.metadata.execute(
                "UPDATE policies SET definition_json = ?, updated_at = ? WHERE policy_id = ?",
                [json.dumps(definition), now, policy_id],
            )
        return self.get_policy(policy_id)

    def delete_policy(self, policy_id: str) -> dict[str, str]:
        self.get_policy(policy_id)
        self.metadata.execute("DELETE FROM policies WHERE policy_id = ?", [policy_id])
        return {"status": "deleted", "policy_id": policy_id}

    # ── Quality rule CRUD ────────────────────────────────────────

    def create_quality_rule(
        self,
        name: str,
        rule_type: str,
        table_name: str,
        threshold: dict[str, Any],
        severity: str = "warn",
    ) -> dict[str, Any]:
        valid_types = ("freshness", "null_rate", "row_count_min")
        if rule_type not in valid_types:
            raise ValueError(f"Invalid rule_type: {rule_type}. Must be one of {valid_types}")
        if severity not in ("warn", "block"):
            raise ValueError(f"Invalid severity: {severity}. Must be 'warn' or 'block'")
        rule_id = f"qr_{uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()
        self.metadata.execute(
            """
            INSERT INTO quality_rules (rule_id, name, rule_type, table_name, threshold_json, severity, enabled, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
            """,
            [rule_id, name, rule_type, table_name, json.dumps(threshold), severity, now, now],
        )
        return {
            "rule_id": rule_id,
            "name": name,
            "rule_type": rule_type,
            "table_name": table_name,
            "threshold": threshold,
            "severity": severity,
            "enabled": True,
        }

    def list_quality_rules(self, table_name: str | None = None) -> list[dict[str, Any]]:
        if table_name:
            rows = self.metadata.query_rows(
                "SELECT * FROM quality_rules WHERE enabled = 1 AND table_name = ? ORDER BY created_at",
                [table_name],
            )
        else:
            rows = self.metadata.query_rows("SELECT * FROM quality_rules WHERE enabled = 1 ORDER BY created_at")
        return [self._deserialize_rule(r) for r in rows]

    def delete_quality_rule(self, rule_id: str) -> dict[str, str]:
        row = self.metadata.query_one("SELECT rule_id FROM quality_rules WHERE rule_id = ?", [rule_id])
        if row is None:
            raise KeyError(f"Unknown quality rule: {rule_id}")
        self.metadata.execute("DELETE FROM quality_rules WHERE rule_id = ?", [rule_id])
        return {"status": "deleted", "rule_id": rule_id}

    # ── Enforcement ──────────────────────────────────────────────

    def check_policies(
        self,
        session_id: str,
        step_type: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Check all enabled policies against the proposed step."""
        del session_id
        policies = self.list_policies(enabled_only=True)
        decisions: list[PolicyDecision] = []
        transforms: dict[str, Any] = {
            "aggregate_only": False,
            "masked_fields": [],
            "row_filters": [],
            "max_rows_scanned": None,
        }

        for policy in policies:
            scope = policy.get("scope", {})
            # Check scope filtering
            if scope.get("step_types") and step_type not in scope["step_types"]:
                continue

            ptype = policy["policy_type"]
            if ptype == "aggregate_only":
                transforms["aggregate_only"] = True
                decisions.append(
                    PolicyDecision(
                        code="aggregate_only_enabled",
                        decision="apply",
                        effect="apply",
                        policy_id=policy["policy_id"],
                        policy_name=policy["name"],
                        policy_type=ptype,
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
                            policy_type=ptype,
                            scope=scope,
                            message=(
                                f"Policy '{policy['name']}' forbids sample_rows "
                                "(aggregate-only mode)."
                            ),
                            detail={"step_type": step_type},
                        )
                    )
            elif ptype == "field_mask":
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
                            policy_type=ptype,
                            scope=scope,
                            message=(
                                f"Policy '{policy['name']}' marks fields "
                                f"{masked_fields} as sensitive."
                            ),
                            detail={"fields": list(masked_fields)},
                        )
                    )
                if params and masked_fields:
                    # Check if params reference masked fields
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
                                    policy_type=ptype,
                                    scope=scope,
                                    message=(
                                        f"Policy '{policy['name']}' forbids access "
                                        f"to field '{field}'."
                                    ),
                                    detail={"field": field},
                                )
                            )
            elif ptype == "row_filter":
                filter_expression = (
                    policy.get("definition", {}).get("sql")
                    or policy.get("definition", {}).get("predicate")
                    or ""
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
                            policy_type=ptype,
                            scope=scope,
                            message=(
                                f"Policy '{policy['name']}' contributes a row filter "
                                "constraint."
                            ),
                            detail={"expression": filter_expression},
                        )
                    )
            elif ptype == "max_rows":
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
                            if count > max_rows:
                                decisions.append(
                                    PolicyDecision(
                                        code="max_rows_exceeded",
                                        decision="deny",
                                        effect="block",
                                        policy_id=policy["policy_id"],
                                        policy_name=policy["name"],
                                        policy_type=ptype,
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
                        except Exception:
                            pass

        return PolicyApplicationResult(decisions=decisions, transforms=transforms).to_dict()

    def check_quality(self, table_name: str) -> dict[str, Any]:
        """Run quality checks for a table. Returns warnings and blockers."""
        rules = self.list_quality_rules(table_name=table_name)
        warnings: list[dict[str, str]] = []
        blockers: list[dict[str, str]] = []

        for rule in rules:
            rtype = rule["rule_type"]
            threshold = rule["threshold"]
            issue: dict[str, str] | None = None

            try:
                if rtype == "freshness":
                    max_age_hours = threshold.get("max_age_hours", 24)
                    row = self.analytics.query_rows(f"SELECT MAX(event_date) AS max_date FROM {table_name}")
                    if row:
                        max_date = row[0].get("max_date")
                        if max_date is not None:
                            from datetime import date, timedelta
                            today = date.today()
                            if isinstance(max_date, str):
                                max_date = date.fromisoformat(max_date)
                            age_hours = (today - max_date).total_seconds() / 3600
                            if age_hours > max_age_hours:
                                issue = {
                                    "rule_id": rule["rule_id"],
                                    "name": rule["name"],
                                    "message": (
                                        f"Quality rule '{rule['name']}': table '{table_name}' "
                                        f"data is {age_hours:.0f}h old (limit: {max_age_hours}h)."
                                    ),
                                }
                elif rtype == "null_rate":
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
                elif rtype == "row_count_min":
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

        passed = len(blockers) == 0
        return {"passed": passed, "warnings": warnings, "blockers": blockers}

    def check_step(
        self,
        session_id: str,
        step_type: str,
        params: dict[str, Any] | None = None,
        tables: list[str] | None = None,
    ) -> dict[str, Any]:
        """Run all governance checks for a proposed step. Combines policy and quality checks."""
        policy_result = self.check_policies(session_id, step_type, params)
        all_violations = list(policy_result["violations"])

        quality_warnings: list[dict[str, str]] = []
        quality_blockers: list[dict[str, str]] = []
        if tables:
            for table in tables:
                qr = self.check_quality(table)
                quality_warnings.extend(qr["warnings"])
                quality_blockers.extend(qr["blockers"])
                all_violations.extend(qr["blockers"])

        passed = len(all_violations) == 0
        return {
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

    # ── Internal helpers ─────────────────────────────────────────

    def _deserialize_policy(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "policy_id": row["policy_id"],
            "name": row["name"],
            "policy_type": row["policy_type"],
            "definition": json.loads(row["definition_json"]),
            "scope": json.loads(row["scope_json"]),
            "enabled": bool(row["enabled"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def _deserialize_rule(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "rule_id": row["rule_id"],
            "name": row["name"],
            "rule_type": row["rule_type"],
            "table_name": row["table_name"],
            "threshold": json.loads(row["threshold_json"]),
            "severity": row["severity"],
            "enabled": bool(row["enabled"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
