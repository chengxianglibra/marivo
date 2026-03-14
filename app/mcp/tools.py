from __future__ import annotations

import os
from typing import Any

from mcp.server.fastmcp import FastMCP

from app.mcp.common import format_tool_response, get_client
from app.mcp.models import (
    CatalogInput,
    CreateSessionInput,
    DraftPlanInput,
    EvidenceInput,
    ExecutePlanInput,
    GetJobInput,
    HealthInput,
    ListApprovalsInput,
    ListSourcesInput,
    PlannerContextInput,
    ResolveTermInput,
    RunStepInput,
    SearchCatalogInput,
    SubmitJobInput,
    ValidatePlanInput,
)
from app.mcp.renderers import (
    render_catalog_markdown,
    render_evidence_markdown,
    render_step_markdown,
)


def register_tools(mcp: FastMCP) -> None:
    @mcp.tool(
        name="omnidb_get_health",
        annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    )
    async def omnidb_get_health(params: HealthInput) -> dict[str, Any]:
        """Check that the local OmniDB FastAPI service is reachable and report its configured database path."""
        data = await get_client().get_health()
        summary = f"FastAPI service is {data['status']} at {os.getenv('OMNIDB_API_BASE_URL', 'http://127.0.0.1:8000')}."
        markdown = f"# OmniDB health\n\n- Status: `{data['status']}`\n- DB path: `{data['db_path']}`"
        return format_tool_response(params.response_format, summary, data, markdown)

    @mcp.tool(
        name="omnidb_get_catalog",
        annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    )
    async def omnidb_get_catalog(params: CatalogInput) -> dict[str, Any]:
        """Fetch the semantic catalog exposed by the local OmniDB FastAPI service."""
        data = await get_client().get_catalog()
        summary = f"Catalog returned {len(data.get('metrics', []))} metrics and {len(data.get('assets', []))} assets."
        return format_tool_response(params.response_format, summary, data, render_catalog_markdown(data))

    @mcp.tool(
        name="omnidb_create_session",
        annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True},
    )
    async def omnidb_create_session(params: CreateSessionInput) -> dict[str, Any]:
        """Create a new analysis session in the local OmniDB FastAPI service."""
        data = await get_client().create_session(
            goal=params.goal,
            constraints=params.constraints,
            budget=params.budget,
            policy=params.policy,
        )
        summary = f"Created session {data['session_id']} for goal: {data['goal']}"
        markdown = (
            "# OmniDB session created\n\n"
            f"- Session ID: `{data['session_id']}`\n"
            f"- Goal: {data['goal']}\n"
            f"- Status: `{data['status']}`"
        )
        return format_tool_response(params.response_format, summary, data, markdown)

    @mcp.tool(
        name="omnidb_run_step",
        annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True},
    )
    async def omnidb_run_step(params: RunStepInput) -> dict[str, Any]:
        """Run one supported analysis step for an existing OmniDB session."""
        data = await get_client().run_step(params.session_id, params.step_type, params=params.params)
        summary = data.get("summary", f"Ran step {params.step_type} for session {params.session_id}.")
        return format_tool_response(params.response_format, summary, data, render_step_markdown(data))

    @mcp.tool(
        name="omnidb_get_evidence",
        annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    )
    async def omnidb_get_evidence(params: EvidenceInput) -> dict[str, Any]:
        """Fetch the evidence graph, including observations, claims, edges, and recommendations, for an OmniDB session."""
        data = await get_client().get_evidence(params.session_id)
        summary = (
            f"Evidence graph contains {len(data.get('observations', []))} observations, "
            f"{len(data.get('claims', []))} claims, and {len(data.get('recommendations', []))} recommendations."
        )
        return format_tool_response(params.response_format, summary, data, render_evidence_markdown(data))

    @mcp.tool(
        name="omnidb_list_sources",
        annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    )
    async def omnidb_list_sources(params: ListSourcesInput) -> dict[str, Any]:
        """List registered catalog sources in OmniDB."""
        data = await get_client().list_sources()
        summary = f"Found {len(data)} registered source(s)."
        lines = "\n".join(f"- `{source['source_id']}` ({source['source_type']}): {source['display_name']}" for source in data)
        markdown = f"# OmniDB sources\n\n{lines or '- No sources registered'}"
        return format_tool_response(params.response_format, summary, {"sources": data}, markdown)

    @mcp.tool(
        name="omnidb_search_catalog",
        annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    )
    async def omnidb_search_catalog(params: SearchCatalogInput) -> dict[str, Any]:
        """Search the OmniDB semantic catalog for entities, metrics, and assets by keyword."""
        data = await get_client().search_catalog(params.query, object_type=params.type)
        summary = f"Search for '{params.query}' returned {len(data)} result(s)."
        lines = "\n".join(f"- [{row['type']}] `{row['name']}`: {row.get('display_name', '')}" for row in data)
        markdown = f"# Catalog search: {params.query}\n\n{lines or '- No results'}"
        return format_tool_response(params.response_format, summary, {"results": data}, markdown)

    @mcp.tool(
        name="omnidb_resolve_term",
        annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    )
    async def omnidb_resolve_term(params: ResolveTermInput) -> dict[str, Any]:
        """Resolve a business term (e.g. 'watch_time') to its semantic definition and physical assets."""
        data = await get_client().resolve_term(params.name)
        semantic_object = data.get("semantic_object", {})
        assets = data.get("physical_assets", [])
        asset_lines = "\n".join(f"  - `{asset['fqn']}` (synced: {asset.get('synced_at', 'N/A')})" for asset in assets)
        markdown = (
            f"# Resolved: {params.name}\n\n"
            f"- Type: `{data.get('resolved_type', 'unknown')}`\n"
            f"- Name: `{semantic_object.get('name', 'N/A')}`\n"
            f"- Display: {semantic_object.get('display_name', 'N/A')}\n"
            f"- Status: `{semantic_object.get('status', 'N/A')}`\n\n"
            "## Physical assets\n"
            f"{asset_lines or '- No mapped assets'}"
        )
        summary = f"Resolved '{params.name}' as {data.get('resolved_type', 'unknown')} with {len(assets)} physical asset(s)."
        return format_tool_response(params.response_format, summary, data, markdown)

    @mcp.tool(
        name="omnidb_get_planner_context",
        annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    )
    async def omnidb_get_planner_context(params: PlannerContextInput) -> dict[str, Any]:
        """Get the full planner context bundle for an OmniDB session — metrics, entities, step types, and policies."""
        data = await get_client().get_planner_context(params.session_id)
        metrics = "\n".join(f"- `{metric['name']}`: {metric.get('definition_sql', '')}" for metric in data.get("metrics", []))
        entities = "\n".join(f"- `{entity['name']}` (keys: {entity.get('keys', [])})" for entity in data.get("entities", []))
        steps = ", ".join(f"`{step}`" for step in data.get("available_step_types", []))
        markdown = (
            f"# Planner context for session {params.session_id}\n\n"
            "## Metrics\n"
            f"{metrics or '- None'}\n\n"
            "## Entities\n"
            f"{entities or '- None'}\n\n"
            f"## Available steps\n{steps or '- None'}"
        )
        summary = (
            f"Planner context: {len(data.get('metrics', []))} metrics, "
            f"{len(data.get('entities', []))} entities, "
            f"{len(data.get('available_step_types', []))} step types."
        )
        return format_tool_response(params.response_format, summary, data, markdown)

    @mcp.tool(
        name="omnidb_draft_plan",
        annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True},
    )
    async def omnidb_draft_plan(params: DraftPlanInput) -> dict[str, Any]:
        """Draft an analysis plan for a session. Steps are executed in dependency order."""
        data = await get_client().draft_plan(params.session_id, params.steps)
        steps_md = "\n".join(
            f"  {step['index']}. `{step['step_type']}` (deps: {step.get('dependencies', [])})"
            for step in data.get("steps", [])
        )
        markdown = (
            f"# Plan {data.get('plan_id', '?')}\n\n"
            f"- Status: `{data.get('status', 'unknown')}`\n"
            f"- Steps:\n{steps_md or '  - None'}"
        )
        summary = f"Drafted plan {data.get('plan_id', '?')} with {len(data.get('steps', []))} steps."
        return format_tool_response(params.response_format, summary, data, markdown)

    @mcp.tool(
        name="omnidb_validate_plan",
        annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    )
    async def omnidb_validate_plan(params: ValidatePlanInput) -> dict[str, Any]:
        """Validate a plan's step types, dependencies, and parameters."""
        data = await get_client().validate_plan(params.session_id, params.plan_id)
        status = "valid" if data.get("valid") else "invalid"
        errors = "\n".join(f"- {error}" for error in data.get("errors", []))
        markdown = f"# Plan validation: {status}\n\n{errors or '- No errors'}"
        summary = f"Plan validation: {status}."
        return format_tool_response(params.response_format, summary, data, markdown)

    @mcp.tool(
        name="omnidb_execute_plan",
        annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True},
    )
    async def omnidb_execute_plan(params: ExecutePlanInput) -> dict[str, Any]:
        """Execute an approved plan. Runs all steps in dependency order."""
        data = await get_client().execute_plan(
            params.session_id,
            params.plan_id,
            continue_on_failure=params.continue_on_failure,
        )
        results_md = "\n".join(
            f"  {result['index']}. `{result['step_type']}`: {result.get('summary', '')}"
            for result in data.get("step_results", [])
        )
        markdown = (
            f"# Plan execution: {data.get('status', '?')}\n\n"
            f"- Steps completed:\n{results_md or '  - None'}"
        )
        summary = f"Plan execution {data.get('status', '?')}: {len(data.get('step_results', []))} steps completed."
        return format_tool_response(params.response_format, summary, data, markdown)

    @mcp.tool(
        name="omnidb_submit_job",
        annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True},
    )
    async def omnidb_submit_job(params: SubmitJobInput) -> dict[str, Any]:
        """Submit an async job (step, workflow, or plan execution) and return immediately."""
        data = await get_client().submit_job(params.session_id, params.job_type, params.payload)
        markdown = (
            f"# Job submitted\n\n"
            f"- Job ID: `{data.get('job_id', '?')}`\n"
            f"- Type: `{data.get('job_type', '?')}`\n"
            f"- Status: `{data.get('status', '?')}`"
        )
        summary = f"Submitted job {data.get('job_id', '?')} ({data.get('job_type', '?')})."
        return format_tool_response(params.response_format, summary, data, markdown)

    @mcp.tool(
        name="omnidb_get_job",
        annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    )
    async def omnidb_get_job(params: GetJobInput) -> dict[str, Any]:
        """Get the status and result of an async job."""
        data = await get_client().get_job(params.job_id)
        markdown = (
            f"# Job {data.get('job_id', '?')}\n\n"
            f"- Type: `{data.get('job_type', '?')}`\n"
            f"- Status: `{data.get('status', '?')}`\n"
            f"- Submitted: {data.get('submitted_at', 'N/A')}\n"
            f"- Completed: {data.get('completed_at', 'N/A')}"
        )
        summary = f"Job {data.get('job_id', '?')} status: {data.get('status', '?')}."
        return format_tool_response(params.response_format, summary, data, markdown)

    @mcp.tool(
        name="omnidb_list_approvals",
        annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    )
    async def omnidb_list_approvals(params: ListApprovalsInput) -> dict[str, Any]:
        """List approval requests, optionally filtered by session or status."""
        data = await get_client().list_approvals(session_id=params.session_id, status=params.status)
        lines = "\n".join(
            f"- `{approval['request_id']}`: rec=`{approval['rec_id']}` status=`{approval['status']}`"
            for approval in data
        )
        markdown = f"# Approval requests\n\n{lines or '- No approval requests found'}"
        summary = f"Found {len(data)} approval request(s)."
        return format_tool_response(params.response_format, summary, {"approvals": data}, markdown)
