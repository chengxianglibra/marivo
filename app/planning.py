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
from typing import Any
from uuid import uuid4

from app.analysis_core.ir import AnalysisStepIR, ExecutionPlanIR, from_legacy_step
from app.analysis_core.step_runners import SUPPORTED_STEP_TYPES
from app.storage.metadata import MetadataStore


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

    def __init__(self, metadata: MetadataStore) -> None:
        self.metadata = metadata

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

        errors: list[str] = []
        steps = plan["steps"]
        step_irs = self._plan_step_irs(steps)

        # Check step types
        for step in step_irs:
            if step.step_type not in VALID_STEP_TYPES:
                errors.append(f"Step {step.index}: unknown step_type '{step.step_type}'")

        # Check dependencies are valid indices and acyclic
        for step in step_irs:
            for dep in step.dependencies:
                if dep < 0 or dep >= len(steps):
                    errors.append(f"Step {step.index}: dependency {dep} out of range")
                elif dep >= step.index:
                    errors.append(f"Step {step.index}: dependency {dep} is forward (must be earlier step)")

        # Check for cycles using topological sort
        if not errors:
            if not self._is_acyclic(steps):
                errors.append("Plan has circular dependencies")

        # Validate parameterized steps have required params
        for step in step_irs:
            if step.step_type == "compare_metric":
                if not step.params.get("metric_name") or not step.params.get("table_name"):
                    errors.append(f"Step {step.index}: compare_metric requires 'metric_name' and 'table_name' params")
            elif step.step_type == "profile_table":
                if not step.params.get("table_name"):
                    errors.append(f"Step {step.index}: profile_table requires 'table_name' param")
            elif step.step_type == "sample_rows":
                if not step.params.get("table_name"):
                    errors.append(f"Step {step.index}: sample_rows requires 'table_name' param")

        if errors:
            return {"plan_id": plan_id, "valid": False, "errors": errors}

        # Transition to validated
        self._transition(plan_id, "validated")
        return {"plan_id": plan_id, "valid": True, "errors": []}

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
                # Update step status to running
                step["status"] = "running"
                self._update_steps(plan_id, steps)

                params = step_ir.params
                result = service.run_step(session_id, step_ir.step_type, params=params if params else None)

                step["status"] = "completed"
                step["result_summary"] = result.get("summary", "")
                self._update_steps(plan_id, steps)
                step_results.append({"index": idx, "step_type": step_ir.step_type, "summary": result.get("summary", "")})

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
        """Estimate cost for each step using table row counts as a proxy.

        Updates the plan's steps in-place with estimated_cost fields.
        """
        plan = self.get_plan(plan_id)
        steps = plan["steps"]

        for step in steps:
            params = step.get("params", {})
            table_name = params.get("table_name", "")

            if table_name:
                try:
                    row_count = analytics_engine.table_row_count(table_name)
                    step["estimated_cost"] = row_count
                except Exception:
                    step["estimated_cost"] = None
            elif step["step_type"] in ("compare_watch_time", "analyze_qoe", "analyze_ads", "analyze_recommendation"):
                # Default tables for known step types
                default_tables = {
                    "compare_watch_time": "analytics.watch_events",
                    "analyze_qoe": "analytics.player_qoe",
                    "analyze_ads": "analytics.ad_events",
                    "analyze_recommendation": "analytics.recommendation_events",
                }
                try:
                    row_count = analytics_engine.table_row_count(default_tables[step["step_type"]])
                    step["estimated_cost"] = row_count
                except Exception:
                    step["estimated_cost"] = None
            elif step["step_type"] == "synthesize_findings":
                step["estimated_cost"] = 0  # heuristic, no scan
            else:
                step["estimated_cost"] = None

        self._update_steps(plan_id, steps)
        total = sum(s.get("estimated_cost") or 0 for s in steps)
        return {"plan_id": plan_id, "total_estimated_cost": total, "steps": steps}

    def check_budget(self, plan_id: str, session_id: str) -> dict[str, Any]:
        """Check if plan total cost fits within session budget."""
        plan = self.get_plan(plan_id)
        total = sum(s.get("estimated_cost") or 0 for s in plan["steps"])

        row = self.metadata.query_one("SELECT budget_json FROM sessions WHERE session_id = ?", [session_id])
        if row is None:
            raise KeyError(f"Unknown session: {session_id}")
        budget = json.loads(row["budget_json"])
        max_rows = budget.get("max_rows_scanned", float("inf"))

        return {
            "plan_id": plan_id,
            "total_estimated_cost": total,
            "budget_max_rows": max_rows,
            "within_budget": total <= max_rows,
        }

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
