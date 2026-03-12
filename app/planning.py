"""PlanningService — CRUD and execution for typed analysis plans.

A plan is a sequence of steps with dependencies, validation, and cost
estimation.  Plans follow a lifecycle:

    draft → validated → approved → executing → completed
                                            → failed

Each step in a plan has:
    step_type, params, dependencies (list of step indices), estimated_cost, status
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from math import inf
from typing import TYPE_CHECKING
from typing import Any
from uuid import uuid4

from app.execution.costing import CostModel
from app.analysis_core.ir import AnalysisStepIR, ExecutionPlanIR, from_legacy_step
from app.analysis_core.step_runners import SUPPORTED_STEP_TYPES
from app.runtime_contracts import (
    BudgetCheckResult,
    CostEstimate,
    PlanValidationIssue,
    PlanValidationResult,
)
from app.semantic_runtime import SemanticResolver
from app.storage.analytics import AnalyticsEngine
from app.storage.metadata import MetadataStore

if TYPE_CHECKING:
    from app.governance import GovernanceService
    from app.routing import QueryRouter


# Valid step types (must match SemanticLayerService.run_step dispatcher)
VALID_STEP_TYPES = frozenset(SUPPORTED_STEP_TYPES)

PLAN_STATUS_TRANSITIONS = {
    "draft": {"validated", "deleted"},
    "validated": {"approved", "draft", "deleted"},
    "approved": {"executing", "deleted"},
    "executing": {"completed", "failed"},
    "completed": set(),
    "failed": {"draft"},
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class PlanningService:
    """CRUD, validation, and execution for analysis plans."""

    def __init__(
        self,
        metadata: MetadataStore,
        analytics_engine: AnalyticsEngine | None = None,
        query_router: QueryRouter | None = None,
        governance: GovernanceService | None = None,
        semantic_resolver: SemanticResolver | None = None,
        cost_model: CostModel | None = None,
    ) -> None:
        self.metadata = metadata
        self.analytics = analytics_engine
        self.query_router = query_router
        self.governance = governance
        self.semantic_resolver = semantic_resolver or SemanticResolver(metadata)
        self.cost_model = cost_model or CostModel(
            analytics_engine=analytics_engine,
            query_router=query_router,
        )

    # ── CRUD ──────────────────────────────────────────────────────

    def draft_plan(self, session_id: str, steps: list[dict[str, Any]]) -> dict[str, Any]:
        """Create a new plan in 'draft' status.

        Each step is: {step_type, params?, dependencies?}
        """
        plan_id = f"plan_{uuid4().hex[:12]}"
        now = _now_iso()

        normalized = self._normalize_steps(steps)

        self.metadata.execute(
            """
            INSERT INTO plans (plan_id, session_id, status, steps_json, created_at, updated_at)
            VALUES (?, ?, 'draft', ?, ?, ?)
            """,
            [plan_id, session_id, json.dumps(normalized), now, now],
        )
        return self.get_plan(plan_id)

    def get_plan(self, plan_id: str) -> dict[str, Any]:
        row = self.metadata.query_one("SELECT * FROM plans WHERE plan_id = ?", [plan_id])
        if row is None:
            raise KeyError(f"Unknown plan: {plan_id}")
        return self._row_to_plan(row)

    def list_plans(self, session_id: str) -> list[dict[str, Any]]:
        rows = self.metadata.query_rows(
            "SELECT * FROM plans WHERE session_id = ? ORDER BY created_at", [session_id]
        )
        return [self._row_to_plan(r) for r in rows]

    def patch_plan(self, plan_id: str, steps: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        """Modify steps in a draft plan. Only works on 'draft' plans."""
        plan = self.get_plan(plan_id)
        if plan["status"] != "draft":
            raise ValueError(f"Can only patch plans in 'draft' status, got '{plan['status']}'")

        if steps is not None:
            normalized = self._normalize_steps(steps)
            self.metadata.execute(
                "UPDATE plans SET steps_json = ?, updated_at = ? WHERE plan_id = ?",
                [json.dumps(normalized), _now_iso(), plan_id],
            )

        return self.get_plan(plan_id)

    def delete_plan(self, plan_id: str) -> dict[str, Any]:
        plan = self.get_plan(plan_id)
        if plan["status"] in ("executing",):
            raise ValueError("Cannot delete a plan that is currently executing")
        self.metadata.execute("DELETE FROM plans WHERE plan_id = ?", [plan_id])
        return {"plan_id": plan_id, "status": "deleted"}

    # ── Validation ────────────────────────────────────────────────

    def validate_plan(self, plan_id: str) -> dict[str, Any]:
        """Validate step types and dependency graph. Transitions draft → validated."""
        plan = self.get_plan(plan_id)
        if plan["status"] != "draft":
            raise ValueError(f"Can only validate plans in 'draft' status, got '{plan['status']}'")

        steps = plan["steps"]
        validation = self._validate_steps(plan)
        if not validation.valid:
            return validation.to_dict()

        # Transition to validated
        self._transition(plan_id, "validated")
        return validation.to_dict()

    def approve_plan(self, plan_id: str) -> dict[str, Any]:
        """Transition from validated → approved."""
        plan = self.get_plan(plan_id)
        if plan["status"] != "validated":
            raise ValueError(f"Can only approve plans in 'validated' status, got '{plan['status']}'")
        self._transition(plan_id, "approved")
        return self.get_plan(plan_id)

    # ── Execution ─────────────────────────────────────────────────

    def execute_plan(self, plan_id: str, service: Any) -> dict[str, Any]:
        """Execute an approved plan by running steps in dependency order.

        Args:
            service: SemanticLayerService instance for running steps
        """
        plan = self.get_plan(plan_id)
        if plan["status"] != "approved":
            raise ValueError(f"Can only execute plans in 'approved' status, got '{plan['status']}'")

        self._transition(plan_id, "executing")
        steps = plan["steps"]
        plan_ir = self.get_execution_plan_ir(plan_id)
        session_id = plan["session_id"]

        # Build execution order (topological sort)
        execution_order = self._topological_sort(steps)
        step_results: list[dict[str, Any]] = []

        try:
            for idx in execution_order:
                step = steps[idx]
                step_ir = plan_ir.steps[idx]
                estimate = self.cost_model.estimate_step(
                    step_ir,
                    analytics_engine=self.analytics,
                    query_router=self.query_router,
                )
                step["estimated_cost"] = estimate.estimated_rows
                step["estimated_cost_detail"] = self.cost_model.serialize_estimate(estimate)
                # Update step status to running
                step["status"] = "running"
                self._update_steps(plan_id, steps)

                params = step_ir.params
                started = datetime.now(timezone.utc)
                result = service.run_step(session_id, step_ir.step_type, params=params if params else None)
                duration_ms = (datetime.now(timezone.utc) - started).total_seconds() * 1000

                step["status"] = "completed"
                step["result_summary"] = result.get("summary", "")
                step["actual_cost_feedback"] = self.cost_model.build_actual_feedback(
                    step_ir,
                    result,
                    duration_ms,
                    estimate=estimate,
                )
                self._update_steps(plan_id, steps)
                step_results.append(
                    {
                        "index": idx,
                        "step_type": step_ir.step_type,
                        "summary": result.get("summary", ""),
                        "cost_estimate": self.cost_model.serialize_estimate(estimate),
                        "actual_cost_feedback": step["actual_cost_feedback"],
                    }
                )

            self._transition(plan_id, "completed")
        except Exception as e:
            # Mark current step as failed, plan as failed
            step["status"] = "failed"
            step["error"] = str(e)
            self._update_steps(plan_id, steps)
            self._transition(plan_id, "failed")
            raise

        return {
            "plan_id": plan_id,
            "status": "completed",
            "step_results": step_results,
        }

    def get_execution_plan_ir(self, plan_id: str) -> ExecutionPlanIR:
        plan = self.get_plan(plan_id)
        return ExecutionPlanIR(steps=self._plan_step_irs(plan["steps"]))

    def explain_plan(self, plan_id: str) -> dict[str, Any]:
        """Return a human-readable summary of the plan."""
        plan = self.get_plan(plan_id)
        steps = plan["steps"]
        lines = [f"Plan {plan_id} ({plan['status']}): {len(steps)} steps"]
        for step in steps:
            deps = f" (depends on: {step['dependencies']})" if step["dependencies"] else ""
            cost = f" [est. cost: {step['estimated_cost']}]" if step.get("estimated_cost") else ""
            lines.append(f"  {step['index']}. {step['step_type']}{deps}{cost}")
        return {
            "plan_id": plan_id,
            "status": plan["status"],
            "explanation": "\n".join(lines),
            "total_estimated_cost": sum(s.get("estimated_cost") or 0 for s in steps),
        }

    # ── Cost estimation ───────────────────────────────────────────

    def estimate_costs(self, plan_id: str, analytics_engine: Any) -> dict[str, Any]:
        """Estimate cost for each step using the shared cost model seam.

        Updates the plan's steps in-place with estimated_cost fields.
        """
        plan = self.get_plan(plan_id)
        steps = plan["steps"]
        for step in steps:
            estimate = self.cost_model.estimate_step(
                from_legacy_step(step["index"], step),
                analytics_engine=analytics_engine,
                query_router=self.query_router,
            )
            step["estimated_cost"] = estimate.estimated_rows if estimate is not None else None
            step["estimated_cost_detail"] = self.cost_model.serialize_estimate(estimate)

        self._update_steps(plan_id, steps)
        total = sum(s.get("estimated_cost") or 0 for s in steps)
        return {
            "plan_id": plan_id,
            "total_estimated_cost": total,
            "cost_estimates": [
                step.get("estimated_cost_detail")
                for step in steps
                if step.get("estimated_cost_detail") is not None
            ],
            "steps": steps,
        }

    def check_budget(self, plan_id: str, session_id: str) -> dict[str, Any]:
        """Check if plan total cost fits within session budget."""
        plan = self.get_plan(plan_id)
        row = self.metadata.query_one("SELECT budget_json FROM sessions WHERE session_id = ?", [session_id])
        if row is None:
            raise KeyError(f"Unknown session: {session_id}")
        budget = json.loads(row["budget_json"])
        max_rows = budget.get("max_rows_scanned", inf)
        estimates: list[CostEstimate] = []
        for step in plan["steps"]:
            if isinstance(step.get("estimated_cost_detail"), dict):
                estimates.append(CostEstimate(**step["estimated_cost_detail"]))
                continue
            if step.get("estimated_cost") is not None:
                estimates.append(
                    CostEstimate(
                        subject=f"step:{step['index']}",
                        estimated_rows=step["estimated_cost"],
                        confidence="medium",
                        detail={"step_type": step["step_type"]},
                    )
                )
                continue
            estimates.append(
                self.cost_model.estimate_step(
                    from_legacy_step(step["index"], step),
                    analytics_engine=self.analytics,
                    query_router=self.query_router,
                )
            )
        return self.cost_model.check_budget(plan_id, max_rows, estimates).to_dict()

    # ── Internal helpers ──────────────────────────────────────────

    def _transition(self, plan_id: str, new_status: str) -> None:
        self.metadata.execute(
            "UPDATE plans SET status = ?, updated_at = ? WHERE plan_id = ?",
            [new_status, _now_iso(), plan_id],
        )

    def _update_steps(self, plan_id: str, steps: list[dict[str, Any]]) -> None:
        self.metadata.execute(
            "UPDATE plans SET steps_json = ?, updated_at = ? WHERE plan_id = ?",
            [json.dumps(steps), _now_iso(), plan_id],
        )

    def _normalize_steps(self, steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for i, step in enumerate(steps):
            raw = dict(step)
            step_ir = from_legacy_step(i, raw)
            normalized.append({
                "index": step_ir.index,
                "step_type": step_ir.step_type,
                "params": dict(step_ir.params),
                "dependencies": list(step_ir.dependencies),
                "estimated_cost": raw.get("estimated_cost"),
                "status": raw.get("status", "pending"),
            })
        return normalized

    def _plan_step_irs(self, steps: list[dict[str, Any]]) -> list[AnalysisStepIR]:
        return [from_legacy_step(step["index"], step) for step in steps]

    def _row_to_plan(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "plan_id": row["plan_id"],
            "session_id": row["session_id"],
            "status": row["status"],
            "steps": json.loads(row["steps_json"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def _validate_steps(self, plan: dict[str, Any]) -> PlanValidationResult:
        issues: list[PlanValidationIssue] = []
        cost_estimates: list[CostEstimate] = []
        plan_id = str(plan["plan_id"])
        session_id = str(plan["session_id"])
        steps = plan["steps"]
        step_irs = self._plan_step_irs(steps)

        for step in step_irs:
            if step.step_type not in VALID_STEP_TYPES:
                issues.append(
                    PlanValidationIssue(
                        code="unknown_step_type",
                        category="step_type",
                        step_index=step.index,
                        message=f"Step {step.index}: unknown step_type '{step.step_type}'",
                        detail={"step_type": step.step_type},
                    )
                )

        for step in step_irs:
            for dep in step.dependencies:
                if dep < 0 or dep >= len(steps):
                    issues.append(
                        PlanValidationIssue(
                            code="dependency_out_of_range",
                            category="dependency",
                            step_index=step.index,
                            message=f"Step {step.index}: dependency {dep} out of range",
                            detail={"dependency_index": dep},
                        )
                    )
                elif dep >= step.index:
                    issues.append(
                        PlanValidationIssue(
                            code="dependency_forward_reference",
                            category="dependency",
                            step_index=step.index,
                            message=(
                                f"Step {step.index}: dependency {dep} is forward "
                                "(must be earlier step)"
                            ),
                            detail={"dependency_index": dep},
                        )
                    )

        if not issues and not self._is_acyclic(steps):
            issues.append(
                PlanValidationIssue(
                    code="dependency_cycle",
                    category="dependency",
                    message="Plan has circular dependencies",
                )
            )

        for step in step_irs:
            if step.step_type == "compare_metric":
                missing = [
                    key for key in ("metric_name", "table_name") if not step.params.get(key)
                ]
                if missing:
                    issues.append(
                        PlanValidationIssue(
                            code="missing_required_param",
                            category="params",
                            step_index=step.index,
                            message=(
                                f"Step {step.index}: compare_metric requires "
                                "'metric_name' and 'table_name' params"
                            ),
                            detail={"required_params": ["metric_name", "table_name"], "missing_params": missing},
                        )
                    )
            elif step.step_type == "profile_table":
                if not step.params.get("table_name"):
                    issues.append(
                        PlanValidationIssue(
                            code="missing_required_param",
                            category="params",
                            step_index=step.index,
                            message=f"Step {step.index}: profile_table requires 'table_name' param",
                            detail={"required_params": ["table_name"], "missing_params": ["table_name"]},
                        )
                    )
            elif step.step_type == "sample_rows":
                if not step.params.get("table_name"):
                    issues.append(
                        PlanValidationIssue(
                            code="missing_required_param",
                            category="params",
                            step_index=step.index,
                            message=f"Step {step.index}: sample_rows requires 'table_name' param",
                            detail={"required_params": ["table_name"], "missing_params": ["table_name"]},
                        )
                    )

        for step in step_irs:
            issues.extend(self._validate_step_semantics(step))
            issues.extend(self._validate_step_governance(step, session_id))
            issues.extend(self._validate_step_routing(step))
            cost_estimates.append(
                self.cost_model.estimate_step(
                    step,
                    analytics_engine=self.analytics,
                    query_router=self.query_router,
                )
            )

        budget_result = self._validate_budget(plan_id, session_id, cost_estimates)
        issues.extend(self._budget_issues(budget_result))

        return PlanValidationResult(
            plan_id=plan_id,
            issues=issues,
            cost_estimates=budget_result.cost_estimates,
        )

    def _validate_step_semantics(self, step: AnalysisStepIR) -> list[PlanValidationIssue]:
        issues: list[PlanValidationIssue] = []
        if step.step_type != "compare_metric":
            return issues

        metric_name = str(step.params.get("metric_name", "")).strip()
        if not metric_name:
            return issues

        resolved_metric = self.semantic_resolver.resolve_metric(metric_name)
        if resolved_metric is None:
            issues.append(
                PlanValidationIssue(
                    code="semantic_metric_not_found",
                    category="semantic",
                    step_index=step.index,
                    message=(
                        f"Step {step.index}: metric '{metric_name}' is not published "
                        "or does not exist"
                    ),
                    detail={"metric_name": metric_name},
                )
            )
            return issues

        requested_dimensions = step.params.get("dimensions")
        if isinstance(requested_dimensions, list):
            unsupported = [
                str(dimension)
                for dimension in requested_dimensions
                if str(dimension) not in resolved_metric.dimensions
            ]
            if unsupported:
                issues.append(
                    PlanValidationIssue(
                        code="semantic_dimension_not_supported",
                        category="semantic",
                        step_index=step.index,
                        message=(
                            f"Step {step.index}: metric '{metric_name}' does not support "
                            f"dimensions {unsupported}"
                        ),
                        detail={
                            "metric_name": metric_name,
                            "unsupported_dimensions": unsupported,
                            "supported_dimensions": list(resolved_metric.dimensions),
                        },
                    )
                )

        return issues

    def _validate_step_governance(
        self, step: AnalysisStepIR, session_id: str,
    ) -> list[PlanValidationIssue]:
        if self.governance is None:
            return []

        params = dict(step.params)
        table_name = self._table_name_for_step(step)
        if table_name and not params.get("table_name"):
            params["table_name"] = table_name

        result = self.governance.check_step(
            session_id,
            step.step_type,
            params=params if params else None,
            tables=[table_name] if table_name else None,
        )

        issues: list[PlanValidationIssue] = []
        for decision in result.get("decisions", []):
            effect = decision.get("effect", "block")
            issues.append(
                PlanValidationIssue(
                    code=str(decision.get("code", "governance_decision")),
                    category="governance",
                    severity="error" if effect == "block" else "warn",
                    step_index=step.index,
                    message=f"Step {step.index}: {decision['message']}",
                    detail=decision,
                )
            )

        decision_messages = {decision["message"] for decision in result.get("decisions", [])}
        for warning in result.get("warnings", []):
            if warning["message"] in decision_messages:
                continue
            issues.append(
                PlanValidationIssue(
                    code="quality_warning",
                    category="governance",
                    severity="warn",
                    step_index=step.index,
                    message=f"Step {step.index}: {warning['message']}",
                    detail=warning,
                )
            )
        for violation in result.get("violations", []):
            if violation["message"] in decision_messages:
                continue
            issues.append(
                PlanValidationIssue(
                    code="quality_blocker",
                    category="governance",
                    severity="error",
                    step_index=step.index,
                    message=f"Step {step.index}: {violation['message']}",
                    detail=violation,
                )
            )
        return issues

    def _validate_step_routing(self, step: AnalysisStepIR) -> list[PlanValidationIssue]:
        if self.query_router is None:
            return []

        table_name = self._table_name_for_step(step)
        if table_name is None:
            return []

        native_table_name = self._routing_table_name(table_name)
        try:
            self.query_router.resolve_tables([native_table_name])
        except KeyError as error:
            return [
                PlanValidationIssue(
                    code="routing_table_unresolved",
                    category="routing",
                    severity="warn",
                    step_index=step.index,
                    message=(
                        f"Step {step.index}: routing could not resolve table "
                        f"'{native_table_name}'; validation will rely on default analytics fallback"
                    ),
                    detail={"table_name": table_name, "native_table_name": native_table_name, "error": str(error)},
                )
            ]
        except ValueError as error:
            return [
                PlanValidationIssue(
                    code="routing_engine_unavailable",
                    category="routing",
                    severity="warn",
                    step_index=step.index,
                    message=(
                        f"Step {step.index}: routing could not find a bound engine for "
                        f"'{native_table_name}'; validation will rely on default analytics fallback"
                    ),
                    detail={"table_name": table_name, "native_table_name": native_table_name, "error": str(error)},
                )
            ]
        return []

    def _validate_budget(
        self, plan_id: str, session_id: str, cost_estimates: list[CostEstimate],
    ) -> BudgetCheckResult:
        row = self.metadata.query_one(
            "SELECT budget_json FROM sessions WHERE session_id = ?",
            [session_id],
        )
        if row is None:
            raise KeyError(f"Unknown session: {session_id}")

        budget = json.loads(row["budget_json"])
        max_rows = budget.get("max_rows_scanned", inf)
        return self.cost_model.check_budget(plan_id, max_rows, cost_estimates)

    @staticmethod
    def _budget_issues(budget_result: BudgetCheckResult) -> list[PlanValidationIssue]:
        issues: list[PlanValidationIssue] = []
        if not budget_result.within_budget:
            issues.append(
                PlanValidationIssue(
                    code="budget_rows_exceeded",
                    category="budget",
                    message=(
                        f"Plan {budget_result.plan_id}: estimated rows "
                        f"{budget_result.total_estimated_rows} exceed "
                        f"budget max_rows_scanned={budget_result.budget_max_rows}"
                    ),
                    detail=budget_result.to_dict(),
                )
            )
        if budget_result.unknown_subjects:
            issues.append(
                PlanValidationIssue(
                    code="budget_estimate_unknown",
                    category="budget",
                    severity="warn",
                    message=(
                        f"Plan {budget_result.plan_id}: budget estimate is incomplete for steps "
                        f"{budget_result.unknown_subjects}"
                    ),
                    detail=budget_result.to_dict(),
                )
            )
        return issues

    @staticmethod
    def _table_name_for_step(step: AnalysisStepIR) -> str | None:
        return CostModel._table_name_for_step(step)

    @staticmethod
    def _routing_table_name(table_name: str) -> str:
        return CostModel._routing_table_name(table_name)

    @staticmethod
    def _is_acyclic(steps: list[dict[str, Any]]) -> bool:
        """Check if the dependency graph is a DAG."""
        visited: set[int] = set()
        in_stack: set[int] = set()

        def dfs(node: int) -> bool:
            if node in in_stack:
                return False  # cycle
            if node in visited:
                return True
            visited.add(node)
            in_stack.add(node)
            for dep in steps[node]["dependencies"]:
                if not dfs(dep):
                    return False
            in_stack.discard(node)
            return True

        return all(dfs(i) for i in range(len(steps)))

    @staticmethod
    def _topological_sort(steps: list[dict[str, Any]]) -> list[int]:
        """Return step indices in dependency-respecting order."""
        in_degree = [0] * len(steps)
        dependents: dict[int, list[int]] = {i: [] for i in range(len(steps))}
        for step in steps:
            for dep in step["dependencies"]:
                dependents[dep].append(step["index"])
                in_degree[step["index"]] += 1

        queue = [i for i in range(len(steps)) if in_degree[i] == 0]
        order: list[int] = []
        while queue:
            node = queue.pop(0)
            order.append(node)
            for child in dependents[node]:
                in_degree[child] -= 1
                if in_degree[child] == 0:
                    queue.append(child)

        return order
