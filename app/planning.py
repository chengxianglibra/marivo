"""PlanningService — CRUD and execution for typed analysis plans.

A plan is a sequence of steps with dependencies, validation, and cost
estimation.  Plans follow a lifecycle:

    draft → approved → executing → completed
                                 → failed

Validation auto-approves plans that pass with no governance/budget warnings.
Plans with warnings land in ``validated`` and require explicit approval:

    draft → validated → approved → executing → completed

Each step in a plan has:
    step_type, params, dependencies (list of step indices), estimated_cost, status
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from math import inf
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from app.analysis_core.ir import (
    AnalysisRequest,
    AnalysisStepIR,
    ExecutionPlanIR,
    ExecutionTargetIR,
    PolicyTransformIR,
    ResolvedEntityIR,
    ResolvedMetricIR,
    SemanticResolutionIR,
    from_legacy_step,
    request_from_legacy_session,
)
from app.analysis_core.step_runners import SUPPORTED_STEP_TYPES
from app.execution.costing import CostModel
from app.observability import MetricsCollector, observability_context
from app.runtime_contracts import (
    BudgetCheckResult,
    CostEstimate,
    PlanValidationIssue,
    PlanValidationResult,
)
from app.semantic_runtime import SemanticResolver, SemanticRuntimeRepository
from app.storage.analytics import AnalyticsEngine
from app.storage.metadata import MetadataStore
from app.time_scope import (
    normalize_aggregate_query_request,
    normalize_metric_query_request,
    scope_predicate_contains_time_condition,
)

if TYPE_CHECKING:
    from app.governance import GovernanceService
    from app.routing import QueryRouter


# Valid step types (must match SemanticLayerService.run_step dispatcher)
VALID_STEP_TYPES = frozenset(SUPPORTED_STEP_TYPES)
METRIC_QUERY_REQUIRED_PARAMS = ("table", "metric", "time_scope")
AGGREGATE_QUERY_REQUIRED_PARAMS = ("table", "measures", "time_scope")

PLAN_STATUS_TRANSITIONS = {
    "draft": {"validated", "deleted"},
    "validated": {"approved", "draft", "deleted"},
    "approved": {"executing", "deleted", "draft"},
    "executing": {"completed", "failed"},
    "completed": set(),
    "failed": {"draft"},
    "partial": {"draft"},
}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class PlanningService:
    """CRUD, validation, and execution for analysis plans."""

    def __init__(
        self,
        metadata: MetadataStore,
        analytics_engine: AnalyticsEngine | None = None,
        query_router: QueryRouter | None = None,
        governance: GovernanceService | None = None,
        semantic_resolver: SemanticResolver | None = None,
        semantic_repository: SemanticRuntimeRepository | None = None,
        cost_model: CostModel | None = None,
        metrics: MetricsCollector | None = None,
    ) -> None:
        self.metadata = metadata
        self.analytics = analytics_engine
        self.query_router = query_router
        self.governance = governance
        self.semantic_repository = semantic_repository or SemanticRuntimeRepository(
            metadata,
            resolver=semantic_resolver,
        )
        self.semantic_resolver = self.semantic_repository.resolver
        self.cost_model = cost_model or CostModel(
            analytics_engine=analytics_engine,
            query_router=query_router,
        )
        self.metrics = metrics

    # ── CRUD ──────────────────────────────────────────────────────

    def draft_plan(self, session_id: str, steps: list[dict[str, Any]]) -> dict[str, Any]:
        """Create a new plan in 'draft' status.

        Each step is: {step_type, params?, dependencies?}
        """
        start = time.perf_counter()
        with observability_context(
            session_id=session_id, execution_stage="planner", planner_id="draft_plan"
        ):
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
            result = self.get_plan(plan_id)
        if self.metrics is not None:
            self.metrics.record_execution_stage(
                "planner_draft",
                (time.perf_counter() - start) * 1000,
                planner="draft_plan",
            )
        return result

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

    def patch_plan_incremental(
        self,
        plan_id: str,
        *,
        add_steps: list[dict[str, Any]] | None = None,
        modify_steps: list[dict[str, Any]] | None = None,
        skip_steps: list[int] | None = None,
    ) -> dict[str, Any]:
        """Apply an incremental patch to a plan and re-validate.

        Unlike :meth:`patch_plan` (which replaces all steps), this method
        applies targeted mutations — appending new steps, updating params on
        existing steps, or marking steps as skipped — then immediately
        re-validates (and may auto-approve) the resulting plan.

        The plan may be in any status except ``executing`` or ``completed``.
        Plans in ``validated``, ``approved``, ``failed``, or ``partial`` status
        are reset to ``draft`` before the patch is applied.

        Args:
            plan_id: The plan to patch.
            add_steps: Step dicts to append. Each must include a valid ``step_type``.
            modify_steps: List of ``{index, params}`` dicts. The ``params`` are
                merged (not replaced) into the existing step's params.
            skip_steps: Indices of steps to mark as ``skipped``.

        Returns:
            Updated plan dict merged with the validation result under key
            ``"validation"``.

        Raises:
            KeyError: Plan not found.
            ValueError: Plan is executing/completed, index out of bounds,
                or invalid step_type in add_steps.
        """
        add_steps = add_steps or []
        modify_steps = modify_steps or []
        skip_steps = skip_steps or []

        plan = self.get_plan(plan_id)
        status = plan["status"]

        if status in ("executing", "completed"):
            raise ValueError(
                f"Cannot patch a plan in '{status}' status; "
                "only draft, validated, approved, failed, or partial plans can be patched."
            )

        steps: list[dict[str, Any]] = list(plan.get("steps", []))

        # Pre-validate before any mutation
        for idx in skip_steps:
            if idx < 0 or idx >= len(steps):
                raise ValueError(
                    f"skip_steps index {idx} is out of bounds (plan has {len(steps)} steps)"
                )

        for op in modify_steps:
            idx = op["index"]
            if idx < 0 or idx >= len(steps):
                raise ValueError(
                    f"modify_steps index {idx} is out of bounds (plan has {len(steps)} steps)"
                )
            if steps[idx].get("status") == "skipped":
                raise ValueError(f"Cannot modify step at index {idx}: step is already skipped")

        for i, new_step in enumerate(add_steps):
            step_type = new_step.get("step_type")
            if step_type not in VALID_STEP_TYPES:
                raise ValueError(
                    f"add_steps[{i}] has invalid step_type '{step_type}'. "
                    f"Valid types: {sorted(VALID_STEP_TYPES)}"
                )

        # Reset to draft if needed
        if status != "draft":
            self._transition(plan_id, "draft")

        # Apply skip_steps
        for idx in skip_steps:
            steps[idx]["status"] = "skipped"

        # Apply modify_steps (merge params)
        for op in modify_steps:
            idx = op["index"]
            steps[idx].setdefault("params", {}).update(op["params"])

        # Apply add_steps
        start = len(steps)
        for i, new_step in enumerate(add_steps):
            steps.append(
                {
                    "index": start + i,
                    "step_type": new_step["step_type"],
                    "params": dict(new_step.get("params") or {}),
                    "dependencies": list(new_step.get("dependencies") or []),
                    "status": "pending",
                    "estimated_cost": None,
                }
            )

        self._update_steps(plan_id, steps)

        validation = self.validate_plan(plan_id)
        updated_plan = self.get_plan(plan_id)
        return {**updated_plan, "validation": validation}

    def delete_plan(self, plan_id: str) -> dict[str, Any]:
        plan = self.get_plan(plan_id)
        if plan["status"] in ("executing",):
            raise ValueError("Cannot delete a plan that is currently executing")
        self.metadata.execute("DELETE FROM plans WHERE plan_id = ?", [plan_id])
        return {"plan_id": plan_id, "status": "deleted"}

    # ── Validation ────────────────────────────────────────────────

    def validate_plan(self, plan_id: str) -> dict[str, Any]:
        """Validate step types and dependency graph.

        If validation passes with no warnings (governance, budget, quality),
        the plan is auto-approved (draft → approved) so it can be executed
        immediately.  If there are warnings that merit human review, the plan
        transitions to ``validated`` and requires an explicit
        ``approve_plan()`` call.
        """
        start = time.perf_counter()
        with observability_context(
            plan_id=plan_id, execution_stage="planner", planner_id="validate_plan"
        ):
            plan = self.get_plan(plan_id)
            if plan["status"] != "draft":
                raise ValueError(
                    f"Can only validate plans in 'draft' status, got '{plan['status']}'"
                )

            validation = self._validate_steps(plan)
            if validation.valid:
                if self._needs_explicit_approval(validation):
                    self._transition(plan_id, "validated")
                else:
                    self._transition(plan_id, "approved")
            result = validation.to_dict()
            needs_approval = self._needs_explicit_approval(validation)
            # M-12: auto_approved is a system decision field. When True, Factum has
            # already transitioned the plan to 'approved'; agents MUST check this field
            # before attempting an explicit POST /plans/{id}/approve call.
            result["auto_approved"] = validation.valid and not needs_approval
            if validation.valid and needs_approval:
                result["approval_required"] = True
                result["approval_reasons"] = [
                    {
                        "code": issue.code,
                        "category": issue.category,
                        "severity": issue.severity,
                        "message": issue.message,
                    }
                    for issue in validation.issues
                    if issue.code in self._APPROVAL_GATE_CODES
                    or (issue.category == "governance" and issue.severity == "error")
                ]
            else:
                result["approval_required"] = False
                result["approval_reasons"] = []
        if self.metrics is not None:
            self.metrics.record_execution_stage(
                "planner_validate",
                (time.perf_counter() - start) * 1000,
                planner="validate_plan",
            )
        return result

    # Issue codes that require explicit human approval before execution.
    _APPROVAL_GATE_CODES: frozenset[str] = frozenset(
        {
            # Budget hard-block
            "budget_rows_exceeded",
            # Governance
            "quality_blocker",
        }
    )

    @staticmethod
    def _needs_explicit_approval(validation: PlanValidationResult) -> bool:
        """Return True when a validated plan should wait for human approval.

        Triggers (by issue code):
        - ``budget_rows_exceeded`` — estimated scan exceeds session budget
        - ``quality_blocker`` — governance quality rule violation
        - any governance decision with ``effect=block``

        Informational warnings (``budget_estimate_unknown``, soft governance
        signals) do **not** block execution.
        """
        for issue in validation.issues:
            if issue.code in PlanningService._APPROVAL_GATE_CODES:
                return True
            if issue.category == "governance" and issue.severity == "error":
                return True
        return False

    def approve_plan(self, plan_id: str) -> dict[str, Any]:
        """Explicitly approve a plan that was not auto-approved.

        Plans that passed validation without governance/budget warnings are
        auto-approved during ``validate_plan()``.  This method is only needed
        for plans that landed in ``validated`` status due to warnings that
        require human review.

        Also accepts ``approved`` as a no-op so callers don't need to check.
        """
        plan = self.get_plan(plan_id)
        if plan["status"] == "approved":
            return plan
        if plan["status"] != "validated":
            raise ValueError(
                f"Can only approve plans in 'validated' status, got '{plan['status']}'"
            )
        self._transition(plan_id, "approved")
        return self.get_plan(plan_id)

    # ── Execution ─────────────────────────────────────────────────

    def execute_plan(
        self,
        plan_id: str,
        service: Any,
        *,
        continue_on_failure: bool = False,
    ) -> dict[str, Any]:
        """Execute an approved plan by running steps in dependency order.

        Args:
            service: SemanticLayerService instance for running steps
            continue_on_failure: When True, failed steps are recorded but
                execution continues for independent steps.  Steps that
                depend on a failed step are automatically marked
                ``skipped``.  The plan's final status is ``partial``
                when some (but not all) steps succeeded.
        """
        total_start = time.perf_counter()
        with observability_context(
            plan_id=plan_id, execution_stage="planner", planner_id="execute_plan"
        ):
            plan = self.get_plan(plan_id)
            if plan["status"] != "approved":
                raise ValueError(
                    f"Can only execute plans in 'approved' status, got '{plan['status']}'"
                )

            self._transition(plan_id, "executing")
            steps = plan["steps"]
            compile_start = time.perf_counter()
            with observability_context(
                plan_id=plan_id, execution_stage="compiler", compiler_id="execution_plan_ir_v1"
            ):
                plan_ir = self._build_execution_plan_ir(plan)
            if self.metrics is not None:
                self.metrics.record_execution_stage(
                    "compiler_build_plan_ir",
                    (time.perf_counter() - compile_start) * 1000,
                    compiler="execution_plan_ir_v1",
                )
            session_id = plan["session_id"]

        # Build execution order (topological sort)
        execution_order = self._topological_sort(steps)
        step_results: list[dict[str, Any]] = []
        failed_indices: set[int] = set()

        def _is_blocked_by_failure(step_def: dict[str, Any]) -> bool:
            """Check if any transitive dependency has failed."""
            for dep_idx in step_def.get("dependencies", []):
                if dep_idx in failed_indices:
                    return True
            return False

        try:
            for idx in execution_order:
                step = steps[idx]
                step_ir = plan_ir.steps[idx]

                # Skip steps whose dependencies failed
                if continue_on_failure and _is_blocked_by_failure(step):
                    step["status"] = "skipped"
                    step["error"] = "Skipped due to failed dependency"
                    failed_indices.add(idx)
                    self._update_steps(plan_id, steps)
                    step_results.append(
                        {
                            "index": idx,
                            "step_type": step_ir.step_type,
                            "status": "skipped",
                            "summary": "Skipped due to failed dependency",
                        }
                    )
                    continue

                execution_target = plan_ir.execution_target_for_step(step_ir.index)
                estimate = self.cost_model.estimate_step(
                    step_ir,
                    analytics_engine=self.analytics,
                    query_router=self.query_router,
                    execution_target=execution_target,
                )
                step["estimated_cost"] = estimate.estimated_rows
                step["estimated_cost_detail"] = self.cost_model.serialize_estimate(estimate)
                # Update step status to running
                step["status"] = "running"
                self._update_steps(plan_id, steps)

                try:
                    params = step_ir.params
                    started = datetime.now(UTC)
                    result = service.run_step(
                        session_id, step_ir.step_type, params=params if params else None
                    )
                    duration_ms = (datetime.now(UTC) - started).total_seconds() * 1000

                    step["status"] = "completed"
                    step["result_summary"] = result.get("summary", "")
                    step["result"] = self._snapshot_step_result(step_ir.step_type, result)
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
                except Exception as e:
                    if not continue_on_failure:
                        step["status"] = "failed"
                        step["error"] = str(e)
                        self._update_steps(plan_id, steps)
                        self._transition(plan_id, "failed")
                        raise
                    # Record failure and continue
                    step["status"] = "failed"
                    step["error"] = str(e)
                    failed_indices.add(idx)
                    self._update_steps(plan_id, steps)
                    step_results.append(
                        {
                            "index": idx,
                            "step_type": step_ir.step_type,
                            "status": "failed",
                            "error": str(e),
                        }
                    )

            # Determine final plan status
            if failed_indices:
                completed_count = sum(1 for s in steps if s.get("status") == "completed")
                final_status = "partial" if completed_count > 0 else "failed"
            else:
                final_status = "completed"
            self._transition(plan_id, final_status)
        except Exception:
            # Only reached when continue_on_failure is False and a step failed
            raise
        finally:
            if self.metrics is not None:
                self.metrics.record_execution_stage(
                    "planner_execute",
                    (time.perf_counter() - total_start) * 1000,
                    planner="execute_plan",
                    compiler="execution_plan_ir_v1",
                )

        return {
            "plan_id": plan_id,
            "status": final_status,
            "step_results": step_results,
        }

    def get_execution_plan_ir(self, plan_id: str) -> ExecutionPlanIR:
        plan = self.get_plan(plan_id)
        return self._build_execution_plan_ir(plan)

    def _build_execution_plan_ir(self, plan: dict[str, Any]) -> ExecutionPlanIR:
        step_irs = self._plan_step_irs(plan["steps"])
        request = self._build_analysis_request(plan, step_irs)
        semantic_resolutions = [self._resolve_step_semantics(step) for step in step_irs]
        return ExecutionPlanIR(
            plan_id=plan["plan_id"],
            session_id=plan["session_id"],
            status=plan["status"],
            request=request,
            steps=step_irs,
            semantic_resolutions=semantic_resolutions,
            execution_targets=[
                self._resolve_execution_target(step, semantic_resolution, request)
                for step, semantic_resolution in zip(step_irs, semantic_resolutions)
            ],
            policy_transforms=self._request_policy_transforms(request),
        )

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
        plan_ir = self._build_execution_plan_ir(plan)
        steps = plan["steps"]
        for step in steps:
            step_ir = plan_ir.steps[step["index"]]
            execution_target = plan_ir.execution_target_for_step(step_ir.index)
            estimate = self.cost_model.estimate_step(
                step_ir,
                analytics_engine=analytics_engine,
                query_router=self.query_router,
                execution_target=execution_target,
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
        plan_ir = self._build_execution_plan_ir(plan)
        if plan_ir.request.session_id != session_id:
            raise KeyError(f"Session '{session_id}' does not own plan '{plan_id}'")
        max_rows = plan_ir.request.budget.get("max_rows_scanned", inf)
        estimates: list[CostEstimate] = []
        for step in plan["steps"]:
            step_ir = plan_ir.steps[step["index"]]
            execution_target = plan_ir.execution_target_for_step(step_ir.index)
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
                    step_ir,
                    analytics_engine=self.analytics,
                    query_router=self.query_router,
                    execution_target=execution_target,
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
            normalized.append(
                {
                    "index": step_ir.index,
                    "step_type": step_ir.step_type,
                    "params": dict(step_ir.params),
                    "dependencies": list(step_ir.dependencies),
                    "estimated_cost": raw.get("estimated_cost"),
                    "status": raw.get("status", "pending"),
                }
            )
        return normalized

    @staticmethod
    def _preview_list(items: Any, limit: int = 10) -> tuple[list[Any], bool]:
        if not isinstance(items, list):
            return [], False
        return items[:limit], len(items) > limit

    def _snapshot_step_result(self, step_type: str, result: dict[str, Any]) -> dict[str, Any]:
        snapshot = dict(result)
        limit = 10

        def _truncate_rows_field(field_name: str) -> None:
            preview, truncated = self._preview_list(snapshot.get(field_name), limit=limit)
            if field_name in snapshot:
                snapshot[field_name] = preview
            if truncated:
                snapshot[f"{field_name}_truncated"] = True

        if step_type in {"metric_query", "aggregate_query", "attribute_change", "sample_rows"}:
            _truncate_rows_field("rows")
            _truncate_rows_field("observations")
            _truncate_rows_field("contributions")
            return json.loads(json.dumps(snapshot, default=str))

        if step_type == "profile_table":
            profile = snapshot.get("profile")
            if isinstance(profile, dict):
                profile_snapshot = dict(profile)
                columns = profile_snapshot.get("columns")
                if isinstance(columns, list):
                    profile_snapshot["columns"] = columns[:limit]
                    if len(columns) > limit:
                        profile_snapshot["columns_truncated"] = True
                snapshot["profile"] = profile_snapshot
            return json.loads(json.dumps(snapshot, default=str))

        return json.loads(json.dumps(snapshot, default=str))

    def _plan_step_irs(self, steps: list[dict[str, Any]]) -> list[AnalysisStepIR]:
        return [from_legacy_step(step["index"], step) for step in steps]

    def _build_analysis_request(
        self,
        plan: dict[str, Any],
        step_irs: list[AnalysisStepIR],
    ) -> AnalysisRequest:
        row = self.metadata.query_one(
            """
            SELECT session_id, goal, constraints_json, budget_json, policy_json
            FROM sessions
            WHERE session_id = ?
            """,
            [plan["session_id"]],
        )
        if row is None:
            raise KeyError(f"Unknown session: {plan['session_id']}")
        return request_from_legacy_session(
            {
                "session_id": row["session_id"],
                "goal": row["goal"],
                "constraints": json.loads(row["constraints_json"]),
                "budget": json.loads(row["budget_json"]),
                "policy": json.loads(row["policy_json"]),
            },
            plan_id=plan["plan_id"],
            steps=step_irs,
        )

    def _resolve_step_semantics(self, step: AnalysisStepIR) -> SemanticResolutionIR:
        requested_dimensions = (
            list(step.semantic_intent.dimensions) if step.semantic_intent is not None else []
        )
        resolved_metrics: list[ResolvedMetricIR] = []
        for metric_name in step.metric_names():
            resolved_metric = self.semantic_repository.resolve_metric(metric_name)
            if resolved_metric is None:
                continue
            resolved_metrics.append(
                ResolvedMetricIR(
                    name=resolved_metric.name,
                    grain=resolved_metric.grain,
                    measure_type=resolved_metric.measure_type,
                    dimensions=list(resolved_metric.dimensions),
                    allowed_dimensions=list(resolved_metric.allowed_dimensions),
                    lineage=list(resolved_metric.lineage),
                    quality_expectations=dict(resolved_metric.quality_expectations),
                    metadata=dict(resolved_metric.metadata),
                )
            )

        resolved_entities: list[ResolvedEntityIR] = []
        entity_name = step.params.get("entity_name")
        if entity_name:
            resolved_entity = self.semantic_repository.resolve_entity(str(entity_name))
            if resolved_entity is not None:
                resolved_entities.append(
                    ResolvedEntityIR(
                        name=resolved_entity.name,
                        keys=list(resolved_entity.keys),
                        level=resolved_entity.level,
                        join_constraints=dict(resolved_entity.join_constraints),
                        upstream_dependencies=list(resolved_entity.upstream_dependencies),
                        lineage=list(resolved_entity.lineage),
                        quality_expectations=dict(resolved_entity.quality_expectations),
                        metadata=dict(resolved_entity.metadata),
                    )
                )

        supported_dimensions: list[str] = []
        legal_grains: list[str] = []
        quality_expectations: dict[str, Any] = {}
        if resolved_metrics:
            primary_metric = resolved_metrics[0]
            supported_dimensions = list(
                primary_metric.allowed_dimensions or primary_metric.dimensions
            )
            if primary_metric.grain:
                legal_grains.append(primary_metric.grain)
            quality_expectations.update(primary_metric.quality_expectations)
        if resolved_entities and not quality_expectations:
            quality_expectations.update(resolved_entities[0].quality_expectations)

        compatible_dimensions = (
            [dimension for dimension in requested_dimensions if dimension in supported_dimensions]
            if requested_dimensions and supported_dimensions
            else list(supported_dimensions or requested_dimensions)
        )

        return SemanticResolutionIR(
            step_index=step.index,
            requested_metrics=step.metric_names(),
            requested_dimensions=requested_dimensions,
            supported_dimensions=supported_dimensions,
            compatible_dimensions=compatible_dimensions,
            legal_grains=legal_grains,
            source_table=step.table_name(),
            date_column=step.semantic_intent.date_column
            if step.semantic_intent is not None
            else None,
            metrics=resolved_metrics,
            entities=resolved_entities,
            quality_expectations=quality_expectations,
        )

    def _resolve_execution_target(
        self,
        step: AnalysisStepIR,
        semantic_resolution: SemanticResolutionIR,
        request: AnalysisRequest,
    ) -> ExecutionTargetIR:
        from app.routing import RoutingIntent

        routing_intent = RoutingIntent(
            step_type=step.step_type,
            metric_names=list(semantic_resolution.requested_metrics or step.metric_names()),
            requested_dimensions=list(semantic_resolution.requested_dimensions),
            compatible_dimensions=list(semantic_resolution.compatible_dimensions),
            legal_grains=list(semantic_resolution.legal_grains),
            policy_hints=self._routing_policy_hints(request),
        )
        table_name = step.table_name()
        _ARTIFACT_ONLY_STEPS = frozenset({"synthesize_findings", "correlate_metrics"})
        if table_name is None:
            is_artifact_only = step.step_type in _ARTIFACT_ONLY_STEPS
            return ExecutionTargetIR(
                step_index=step.index,
                engine_type="heuristic" if is_artifact_only else None,
                engine_locality="artifact_only" if is_artifact_only else "unknown",
                routing_strategy="artifact_only" if is_artifact_only else None,
                routing_detail={"intent": routing_intent.to_dict()},
            )

        routing_table_name = step.routing_table_name()
        target = ExecutionTargetIR(
            step_index=step.index,
            table_names=[table_name],
            routing_table_names=[routing_table_name] if routing_table_name else [],
            engine_type=self._default_engine_type(),
            engine_locality="default_analytics",
            routing_strategy="no_router",
            routing_detail={"intent": routing_intent.to_dict()},
        )
        if self.query_router is None or routing_table_name is None:
            return target

        try:
            route = self.query_router.resolve_tables(
                [routing_table_name],
                routing_intent=routing_intent,
            )
        except KeyError as error:
            target.routing_strategy = "fallback_missing_table"
            target.routing_error = str(error)
            target.routing_reason = "routing could not resolve the requested semantic source table"
            return target
        except ValueError as error:
            target.routing_strategy = "fallback_no_common_engine"
            target.routing_error = str(error)
            target.routing_reason = "routing could not find a capability-compatible bound engine"
            return target

        target.engine_id = route.engine_id
        target.engine_type = self._engine_type_for_id(
            route.engine_id
        ) or self._analytics_engine_type(route.engine)
        target.engine_locality = "bound_engine"
        target.routing_strategy = (
            "semantic_bound_route"
            if route.routing_detail.get("intent") is not None
            else "bound_route"
        )
        target.qualified_names = dict(route.qualified_names)
        target.routing_reason = route.selection_reason
        target.routing_detail = dict(route.routing_detail)
        target.capability_profile = (
            route.capability_profile.to_dict() if route.capability_profile is not None else {}
        )
        return target

    @staticmethod
    def _routing_policy_hints(request: AnalysisRequest) -> list[str]:
        hints: list[str] = []
        if request.policy.get("aggregate_only"):
            hints.append("aggregate_only")
        return hints

    @staticmethod
    def _request_policy_transforms(request: AnalysisRequest) -> list[PolicyTransformIR]:
        transforms: list[PolicyTransformIR] = []
        if request.constraints:
            transforms.append(
                PolicyTransformIR(
                    transform_type="session_constraints",
                    source="session",
                    target="request",
                    detail=dict(request.constraints),
                )
            )
        if request.budget:
            transforms.append(
                PolicyTransformIR(
                    transform_type="budget_guard",
                    source="session_budget",
                    target="plan",
                    detail=dict(request.budget),
                )
            )
        if request.policy:
            transforms.append(
                PolicyTransformIR(
                    transform_type="session_policy",
                    source="session_policy",
                    target="plan",
                    detail=dict(request.policy),
                )
            )
        return transforms

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
        plan_ir = self._build_execution_plan_ir(plan)
        request = plan_ir.request
        steps = plan["steps"]
        step_irs = plan_ir.steps

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
            if step.step_type == "metric_query":
                missing = [key for key in METRIC_QUERY_REQUIRED_PARAMS if not step.params.get(key)]
                if missing:
                    issues.append(
                        PlanValidationIssue(
                            code="missing_required_param",
                            category="params",
                            step_index=step.index,
                            message=(
                                f"Step {step.index}: metric_query requires "
                                "'table', 'metric', and 'time_scope' params"
                            ),
                            detail={
                                "required_params": list(METRIC_QUERY_REQUIRED_PARAMS),
                                "missing_params": missing,
                            },
                        )
                    )
                issues.extend(self._validate_typed_step_contract(step))
                issues.extend(self._validate_scope_predicate(step))
            elif step.step_type == "aggregate_query":
                missing = [
                    key for key in AGGREGATE_QUERY_REQUIRED_PARAMS if not step.params.get(key)
                ]
                if missing:
                    issues.append(
                        PlanValidationIssue(
                            code="missing_required_param",
                            category="params",
                            step_index=step.index,
                            message=(
                                f"Step {step.index}: aggregate_query requires "
                                "'table', 'measures', and 'time_scope' params"
                            ),
                            detail={
                                "required_params": list(AGGREGATE_QUERY_REQUIRED_PARAMS),
                                "missing_params": missing,
                            },
                        )
                    )
                issues.extend(self._validate_typed_step_contract(step))
                issues.extend(self._validate_scope_predicate(step))
            elif step.step_type == "attribute_change":
                missing = [
                    key
                    for key in (
                        "metric_name",
                        "table_name",
                        "period_end",
                        "baseline_start",
                        "baseline_end",
                    )
                    if not step.params.get(key)
                ]
                if missing:
                    issues.append(
                        PlanValidationIssue(
                            code="missing_required_param",
                            category="params",
                            step_index=step.index,
                            message=(
                                f"Step {step.index}: attribute_change requires "
                                "'metric_name', 'table_name', 'period_end', 'baseline_start', "
                                "and 'baseline_end' params"
                            ),
                            detail={
                                "required_params": [
                                    "metric_name",
                                    "table_name",
                                    "period_end",
                                    "baseline_start",
                                    "baseline_end",
                                ],
                                "missing_params": missing,
                            },
                        )
                    )
                candidate_dimensions = step.params.get("candidate_dimensions")
                if not isinstance(candidate_dimensions, list) or not candidate_dimensions:
                    issues.append(
                        PlanValidationIssue(
                            code="attribute_change_missing_candidate_dimensions",
                            category="params",
                            step_index=step.index,
                            message=(
                                f"Step {step.index}: attribute_change requires a non-empty "
                                "'candidate_dimensions' list"
                            ),
                            detail={"missing_param": "candidate_dimensions"},
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
                            detail={
                                "required_params": ["table_name"],
                                "missing_params": ["table_name"],
                            },
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
                            detail={
                                "required_params": ["table_name"],
                                "missing_params": ["table_name"],
                            },
                        )
                    )

        for step in step_irs:
            semantic_resolution = plan_ir.semantic_resolution_for_step(step.index)
            execution_target = plan_ir.execution_target_for_step(step.index)
            issues.extend(self._validate_step_semantics(step, semantic_resolution))
            issues.extend(self._validate_step_governance(step, request, execution_target))
            issues.extend(self._validate_step_routing(step, execution_target))
            cost_estimates.append(
                self.cost_model.estimate_step(
                    step,
                    analytics_engine=self.analytics,
                    query_router=self.query_router,
                    execution_target=execution_target,
                )
            )

        budget_result = self._validate_budget(plan_id, request, cost_estimates)
        issues.extend(self._budget_issues(budget_result))

        return PlanValidationResult(
            plan_id=plan_id,
            issues=issues,
            cost_estimates=budget_result.cost_estimates,
        )

    def _validate_scope_predicate(self, step: AnalysisStepIR) -> list[PlanValidationIssue]:
        scope = step.params.get("scope")
        if not isinstance(scope, dict):
            return []
        predicate = scope.get("predicate")
        if not isinstance(predicate, str) or not predicate.strip():
            return []
        if not scope_predicate_contains_time_condition(predicate):
            return []
        return [
            PlanValidationIssue(
                code="time_predicate_not_allowed_in_scope",
                category="params",
                step_index=step.index,
                message=(
                    f"Step {step.index}: scope.predicate must not contain time-axis predicates. "
                    "Move time conditions into 'time_scope'."
                ),
                detail={"predicate": predicate},
            )
        ]

    def _validate_typed_step_contract(self, step: AnalysisStepIR) -> list[PlanValidationIssue]:
        params = step.params or {}
        try:
            if step.step_type == "metric_query":
                normalize_metric_query_request(params)
            elif step.step_type == "aggregate_query":
                normalize_aggregate_query_request(params)
            else:
                return []
        except ValueError as exc:
            return [
                PlanValidationIssue(
                    code="invalid_step_contract",
                    category="params",
                    step_index=step.index,
                    message=f"Step {step.index}: {exc}",
                    detail={"step_type": step.step_type},
                )
            ]
        return []

    def _validate_correlate_metrics_params(
        self,
        step: AnalysisStepIR,
    ) -> list[PlanValidationIssue]:
        issues: list[PlanValidationIssue] = []
        params = step.params or {}
        has_left = bool(params.get("left_artifact_id") or params.get("left_step_id"))
        has_right = bool(params.get("right_artifact_id") or params.get("right_step_id"))
        if not has_left:
            issues.append(
                PlanValidationIssue(
                    code="correlate_metrics_missing_left",
                    category="semantic",
                    step_index=step.index,
                    message=(
                        f"Step {step.index}: correlate_metrics requires 'left_artifact_id' "
                        "or 'left_step_id'"
                    ),
                    detail={"step_type": "correlate_metrics"},
                )
            )
        if not has_right:
            issues.append(
                PlanValidationIssue(
                    code="correlate_metrics_missing_right",
                    category="semantic",
                    step_index=step.index,
                    message=(
                        f"Step {step.index}: correlate_metrics requires 'right_artifact_id' "
                        "or 'right_step_id'"
                    ),
                    detail={"step_type": "correlate_metrics"},
                )
            )
        for required in (
            "left_value_column",
            "right_value_column",
            "join_on",
            "left_metric",
            "right_metric",
        ):
            if not params.get(required):
                issues.append(
                    PlanValidationIssue(
                        code=f"correlate_metrics_missing_{required}",
                        category="semantic",
                        step_index=step.index,
                        message=f"Step {step.index}: correlate_metrics requires '{required}'",
                        detail={"step_type": "correlate_metrics", "missing_param": required},
                    )
                )
        return issues

    def _validate_step_semantics(
        self,
        step: AnalysisStepIR,
        semantic_resolution: SemanticResolutionIR | None,
    ) -> list[PlanValidationIssue]:
        issues: list[PlanValidationIssue] = []
        if step.step_type == "correlate_metrics":
            return self._validate_correlate_metrics_params(step)
        if step.step_type not in {"metric_query", "attribute_change"}:
            return issues

        metric_name = ""
        if semantic_resolution is not None and semantic_resolution.requested_metrics:
            metric_name = str(semantic_resolution.requested_metrics[0]).strip()
        if not metric_name:
            metric_name = str(
                step.primary_metric_name() or step.params.get("metric_name", "")
            ).strip()
        if not metric_name:
            return issues

        if semantic_resolution is None or not semantic_resolution.metrics:
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

        if step.step_type == "attribute_change":
            return issues

        requested_dimensions = (
            semantic_resolution.requested_dimensions
            if semantic_resolution.requested_dimensions
            else step.params.get("dimensions")
        )
        if isinstance(requested_dimensions, list):
            unsupported = [
                str(dimension)
                for dimension in requested_dimensions
                if str(dimension) not in semantic_resolution.supported_dimensions
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
                            "supported_dimensions": list(semantic_resolution.supported_dimensions),
                        },
                    )
                )

        requested_grain = str(step.params.get("grain", "")).strip()
        if (
            requested_grain
            and semantic_resolution.legal_grains
            and requested_grain not in semantic_resolution.legal_grains
        ):
            issues.append(
                PlanValidationIssue(
                    code="semantic_grain_not_supported",
                    category="semantic",
                    step_index=step.index,
                    message=(
                        f"Step {step.index}: metric '{metric_name}' does not support "
                        f"grain '{requested_grain}'"
                    ),
                    detail={
                        "metric_name": metric_name,
                        "requested_grain": requested_grain,
                        "supported_grains": list(semantic_resolution.legal_grains),
                    },
                )
            )

        return issues

    def _validate_step_governance(
        self,
        step: AnalysisStepIR,
        request: AnalysisRequest,
        execution_target: ExecutionTargetIR | None,
    ) -> list[PlanValidationIssue]:
        if self.governance is None:
            return []

        params = dict(step.params)
        table_name = (
            execution_target.table_names[0]
            if execution_target is not None and execution_target.table_names
            else step.table_name()
        )
        if table_name and not params.get("table_name"):
            params["table_name"] = table_name

        result = self.governance.check_step(
            str(request.session_id),
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

    def _validate_step_routing(
        self,
        step: AnalysisStepIR,
        execution_target: ExecutionTargetIR | None,
    ) -> list[PlanValidationIssue]:
        if self.query_router is None or execution_target is None:
            return []

        if not execution_target.table_names or not execution_target.routing_table_names:
            return []

        if execution_target.engine_locality == "bound_engine":
            return []

        table_name = execution_target.table_names[0]
        native_table_name = execution_target.routing_table_names[0]
        if execution_target.routing_strategy == "fallback_missing_table":
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
                    detail={
                        "table_name": table_name,
                        "native_table_name": native_table_name,
                        "error": execution_target.routing_error,
                        "routing_strategy": execution_target.routing_strategy,
                    },
                )
            ]
        if execution_target.routing_strategy == "fallback_no_common_engine":
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
                    detail={
                        "table_name": table_name,
                        "native_table_name": native_table_name,
                        "error": execution_target.routing_error,
                        "routing_strategy": execution_target.routing_strategy,
                    },
                )
            ]
        return []

    def _validate_budget(
        self,
        plan_id: str,
        request: AnalysisRequest,
        cost_estimates: list[CostEstimate],
    ) -> BudgetCheckResult:
        max_rows = request.budget.get("max_rows_scanned", inf)
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

    def _default_engine_type(self) -> str | None:
        return self._analytics_engine_type(self.analytics) or "duckdb"

    def _engine_type_for_id(self, engine_id: str) -> str | None:
        row = self.metadata.query_one(
            "SELECT engine_type FROM engines WHERE engine_id = ?",
            [engine_id],
        )
        if row is None:
            return None
        return str(row["engine_type"])

    @staticmethod
    def _analytics_engine_type(engine: AnalyticsEngine | None) -> str | None:
        if engine is None:
            return None
        class_name = engine.__class__.__name__.lower()
        if "duckdb" in class_name:
            return "duckdb"
        if "trino" in class_name:
            return "trino"
        return class_name.removesuffix("analyticsengine") or class_name

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
