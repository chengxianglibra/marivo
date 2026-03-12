from __future__ import annotations

import json
import os
from enum import Enum
from typing import Any

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

from app.mcp_client import OmniDBApiClient


class ResponseFormat(str, Enum):
    JSON = "json"
    MARKDOWN = "markdown"


class MCPBaseModel(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")


class HealthInput(MCPBaseModel):
    response_format: ResponseFormat = Field(
        default=ResponseFormat.JSON,
        description="Output format: 'json' for structured data or 'markdown' for a concise human-readable summary.",
    )


class CatalogInput(MCPBaseModel):
    response_format: ResponseFormat = Field(
        default=ResponseFormat.JSON,
        description="Output format: 'json' for structured data or 'markdown' for a concise human-readable summary.",
    )


class CreateSessionInput(MCPBaseModel):
    goal: str = Field(..., min_length=5, description="Analysis goal, for example 'Investigate the recent watch time decline'.")
    constraints: dict[str, Any] = Field(default_factory=dict, description="Optional analysis constraints forwarded to the FastAPI service.")
    budget: dict[str, Any] | None = Field(default=None, description="Optional execution budget forwarded to the FastAPI service.")
    policy: dict[str, Any] | None = Field(default=None, description="Optional policy overrides forwarded to the FastAPI service.")
    response_format: ResponseFormat = Field(
        default=ResponseFormat.JSON,
        description="Output format: 'json' for structured data or 'markdown' for a concise human-readable summary.",
    )


class RunStepInput(MCPBaseModel):
    session_id: str = Field(..., min_length=5, description="Session identifier returned by omnidb_create_session.")
    step_type: str = Field(
        ...,
        description="Step to run. Supported values: compare_watch_time, analyze_qoe, analyze_ads, analyze_recommendation, synthesize_findings.",
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.JSON,
        description="Output format: 'json' for structured data or 'markdown' for a concise human-readable summary.",
    )


class WorkflowInput(MCPBaseModel):
    session_id: str = Field(..., min_length=5, description="Session identifier returned by omnidb_create_session.")
    response_format: ResponseFormat = Field(
        default=ResponseFormat.JSON,
        description="Output format: 'json' for structured data or 'markdown' for a concise human-readable summary.",
    )


class EvidenceInput(MCPBaseModel):
    session_id: str = Field(..., min_length=5, description="Session identifier returned by omnidb_create_session.")
    response_format: ResponseFormat = Field(
        default=ResponseFormat.JSON,
        description="Output format: 'json' for structured data or 'markdown' for a concise human-readable summary.",
    )


# ── New MCP input models (Phase 4) ──────────────────────────────────

class ListSourcesInput(MCPBaseModel):
    response_format: ResponseFormat = Field(
        default=ResponseFormat.JSON,
        description="Output format: 'json' for structured data or 'markdown' for a concise human-readable summary.",
    )


class SearchCatalogInput(MCPBaseModel):
    query: str = Field(..., min_length=1, description="Search term to match against entity/metric names, display names, and descriptions.")
    type: str | None = Field(default=None, description="Optional filter: 'entity', 'metric', or 'asset'.")
    response_format: ResponseFormat = Field(
        default=ResponseFormat.JSON,
        description="Output format: 'json' for structured data or 'markdown' for a concise human-readable summary.",
    )


class ResolveTermInput(MCPBaseModel):
    name: str = Field(..., min_length=1, description="Business term to resolve, e.g. 'watch_time'.")
    response_format: ResponseFormat = Field(
        default=ResponseFormat.JSON,
        description="Output format: 'json' for structured data or 'markdown' for a concise human-readable summary.",
    )


class PlannerContextInput(MCPBaseModel):
    session_id: str = Field(..., min_length=5, description="Session identifier returned by omnidb_create_session.")
    response_format: ResponseFormat = Field(
        default=ResponseFormat.JSON,
        description="Output format: 'json' for structured data or 'markdown' for a concise human-readable summary.",
    )


class DraftPlanInput(MCPBaseModel):
    session_id: str = Field(..., min_length=5, description="Session identifier returned by omnidb_create_session.")
    steps: list[dict[str, Any]] = Field(
        ...,
        description="List of plan steps. Each step: {step_type, params?, dependencies?}.",
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.JSON,
        description="Output format: 'json' for structured data or 'markdown' for a concise human-readable summary.",
    )


class ValidatePlanInput(MCPBaseModel):
    session_id: str = Field(..., min_length=5, description="Session identifier.")
    plan_id: str = Field(..., min_length=5, description="Plan identifier returned by omnidb_draft_plan.")
    response_format: ResponseFormat = Field(
        default=ResponseFormat.JSON,
        description="Output format: 'json' for structured data or 'markdown' for a concise human-readable summary.",
    )


class ExecutePlanInput(MCPBaseModel):
    session_id: str = Field(..., min_length=5, description="Session identifier.")
    plan_id: str = Field(..., min_length=5, description="Plan identifier. Must be in 'approved' status.")
    response_format: ResponseFormat = Field(
        default=ResponseFormat.JSON,
        description="Output format: 'json' for structured data or 'markdown' for a concise human-readable summary.",
    )


mcp = FastMCP("omnidb_mcp", json_response=True)


def get_client() -> OmniDBApiClient:
    return OmniDBApiClient(
        base_url=os.getenv("OMNIDB_API_BASE_URL", "http://127.0.0.1:8000"),
        timeout=float(os.getenv("OMNIDB_API_TIMEOUT", "30")),
    )


def format_tool_response(
    response_format: ResponseFormat,
    summary: str,
    data: dict[str, Any],
    markdown: str,
) -> dict[str, Any]:
    if response_format == ResponseFormat.MARKDOWN:
        return {"summary": summary, "markdown": markdown, "data": data}
    return {"summary": summary, "data": data}


def render_catalog_markdown(data: dict[str, Any]) -> str:
    metrics = "\n".join(f"- `{metric['id']}`: {metric['definition']}" for metric in data.get("metrics", []))
    assets = "\n".join(
        f"- `{asset['id']}` ({asset['kind']}, rows={asset['row_count']})" for asset in data.get("assets", [])
    )
    return (
        "# OmniDB catalog\n\n"
        f"- Engine: `{data.get('engine', 'unknown')}`\n"
        f"- Metrics: {len(data.get('metrics', []))}\n"
        f"- Assets: {len(data.get('assets', []))}\n\n"
        "## Metrics\n"
        f"{metrics or '- None'}\n\n"
        "## Assets\n"
        f"{assets or '- None'}"
    )


def render_step_markdown(data: dict[str, Any]) -> str:
    observations = data.get("observations", [])
    observation_lines = []
    for observation in observations[:5]:
        slice_info = observation.get("subject", {}).get("slice", {})
        payload = observation.get("payload", {})
        observation_lines.append(
            "- "
            f"{observation.get('type')} for "
            f"{slice_info.get('platform')} {slice_info.get('app_version')} "
            f"{slice_info.get('network_type')} {slice_info.get('content_type')}: "
            f"{json.dumps(payload, ensure_ascii=False, sort_keys=True)}"
        )
    return (
        f"# Step result: {data.get('step_type', 'unknown')}\n\n"
        f"{data.get('summary', 'No summary available.')}\n\n"
        "## Key observations\n"
        f"{chr(10).join(observation_lines) if observation_lines else '- No observations returned'}"
    )


def render_workflow_markdown(data: dict[str, Any]) -> str:
    claims = "\n".join(f"- {claim['text']} (confidence={claim['confidence']})" for claim in data.get("claims", []))
    recommendations = "\n".join(
        f"- [{recommendation['priority']}] {recommendation['action_text']}" for recommendation in data.get("recommendations", [])
    )
    return (
        "# Watch time drop workflow\n\n"
        f"{data.get('final_summary', 'No final summary available.')}\n\n"
        "## Claims\n"
        f"{claims or '- None'}\n\n"
        "## Recommendations\n"
        f"{recommendations or '- None'}"
    )


def render_evidence_markdown(data: dict[str, Any]) -> str:
    claims = "\n".join(f"- {claim['text']} (confidence={claim['confidence']})" for claim in data.get("claims", []))
    edges = "\n".join(
        f"- {edge['from_node_type']}:{edge['from_node_id']} -> {edge['edge_type']} -> {edge['to_node_type']}:{edge['to_node_id']}"
        for edge in data.get("edges", [])[:10]
    )
    return (
        "# Evidence graph\n\n"
        f"- Observations: {len(data.get('observations', []))}\n"
        f"- Claims: {len(data.get('claims', []))}\n"
        f"- Recommendations: {len(data.get('recommendations', []))}\n\n"
        "## Claims\n"
        f"{claims or '- None'}\n\n"
        "## Sample edges\n"
        f"{edges or '- None'}"
    )


# ── Existing tools ───────────────────────────────────────────────────

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
    data = await get_client().run_step(params.session_id, params.step_type)
    summary = data.get("summary", f"Ran step {params.step_type} for session {params.session_id}.")
    return format_tool_response(params.response_format, summary, data, render_step_markdown(data))


@mcp.tool(
    name="omnidb_run_watch_time_workflow",
    annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True},
)
async def omnidb_run_watch_time_workflow(params: WorkflowInput) -> dict[str, Any]:
    """Run the end-to-end watch-time-drop workflow for an existing OmniDB session."""
    data = await get_client().run_watch_time_workflow(params.session_id)
    summary = data.get("final_summary", f"Ran watch-time workflow for session {params.session_id}.")
    return format_tool_response(params.response_format, summary, data, render_workflow_markdown(data))


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


# ── New tools (Phase 4) ─────────────────────────────────────────────

@mcp.tool(
    name="omnidb_list_sources",
    annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
)
async def omnidb_list_sources(params: ListSourcesInput) -> dict[str, Any]:
    """List registered catalog sources in OmniDB."""
    data = await get_client().list_sources()
    summary = f"Found {len(data)} registered source(s)."
    lines = "\n".join(f"- `{s['source_id']}` ({s['source_type']}): {s['display_name']}" for s in data)
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
    lines = "\n".join(f"- [{r['type']}] `{r['name']}`: {r.get('display_name', '')}" for r in data)
    markdown = f"# Catalog search: {params.query}\n\n{lines or '- No results'}"
    return format_tool_response(params.response_format, summary, {"results": data}, markdown)


@mcp.tool(
    name="omnidb_resolve_term",
    annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
)
async def omnidb_resolve_term(params: ResolveTermInput) -> dict[str, Any]:
    """Resolve a business term (e.g. 'watch_time') to its semantic definition and physical assets."""
    data = await get_client().resolve_term(params.name)
    obj = data.get("semantic_object", {})
    assets = data.get("physical_assets", [])
    asset_lines = "\n".join(f"  - `{a['fqn']}` (synced: {a.get('synced_at', 'N/A')})" for a in assets)
    markdown = (
        f"# Resolved: {params.name}\n\n"
        f"- Type: `{data.get('resolved_type', 'unknown')}`\n"
        f"- Name: `{obj.get('name', 'N/A')}`\n"
        f"- Display: {obj.get('display_name', 'N/A')}\n"
        f"- Status: `{obj.get('status', 'N/A')}`\n\n"
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
    metrics = "\n".join(f"- `{m['name']}`: {m.get('definition_sql', '')}" for m in data.get("metrics", []))
    entities = "\n".join(f"- `{e['name']}` (keys: {e.get('keys', [])})" for e in data.get("entities", []))
    steps = ", ".join(f"`{s}`" for s in data.get("available_step_types", []))
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
        f"  {s['index']}. `{s['step_type']}` (deps: {s.get('dependencies', [])})"
        for s in data.get("steps", [])
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
    errors = "\n".join(f"- {e}" for e in data.get("errors", []))
    markdown = f"# Plan validation: {status}\n\n{errors or '- No errors'}"
    summary = f"Plan validation: {status}."
    return format_tool_response(params.response_format, summary, data, markdown)


@mcp.tool(
    name="omnidb_execute_plan",
    annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True},
)
async def omnidb_execute_plan(params: ExecutePlanInput) -> dict[str, Any]:
    """Execute an approved plan. Runs all steps in dependency order."""
    data = await get_client().execute_plan(params.session_id, params.plan_id)
    results_md = "\n".join(
        f"  {r['index']}. `{r['step_type']}`: {r.get('summary', '')}"
        for r in data.get("step_results", [])
    )
    markdown = (
        f"# Plan execution: {data.get('status', '?')}\n\n"
        f"- Steps completed:\n{results_md or '  - None'}"
    )
    summary = f"Plan execution {data.get('status', '?')}: {len(data.get('step_results', []))} steps completed."
    return format_tool_response(params.response_format, summary, data, markdown)


# ── New tools (Phase 5) ─────────────────────────────────────────────

class SubmitJobInput(MCPBaseModel):
    session_id: str = Field(..., min_length=5, description="Session identifier.")
    job_type: str = Field(..., description="Job type: 'step', 'workflow', or 'plan'.")
    payload: dict[str, Any] = Field(default_factory=dict, description="Job payload, e.g. {step_type, params} or {plan_id}.")
    response_format: ResponseFormat = Field(
        default=ResponseFormat.JSON,
        description="Output format: 'json' for structured data or 'markdown' for a concise human-readable summary.",
    )


class GetJobInput(MCPBaseModel):
    job_id: str = Field(..., min_length=5, description="Job identifier returned by omnidb_submit_job.")
    response_format: ResponseFormat = Field(
        default=ResponseFormat.JSON,
        description="Output format: 'json' for structured data or 'markdown' for a concise human-readable summary.",
    )


class ListApprovalsInput(MCPBaseModel):
    session_id: str | None = Field(default=None, description="Optional session ID filter.")
    status: str | None = Field(default=None, description="Optional status filter: 'pending', 'approved', 'rejected'.")
    response_format: ResponseFormat = Field(
        default=ResponseFormat.JSON,
        description="Output format: 'json' for structured data or 'markdown' for a concise human-readable summary.",
    )


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
        f"- `{a['request_id']}`: rec=`{a['rec_id']}` status=`{a['status']}`"
        for a in data
    )
    markdown = f"# Approval requests\n\n{lines or '- No approval requests found'}"
    summary = f"Found {len(data)} approval request(s)."
    return format_tool_response(params.response_format, summary, {"approvals": data}, markdown)


def main() -> None:
    mcp.run(transport=os.getenv("OMNIDB_MCP_TRANSPORT", "stdio"))


if __name__ == "__main__":
    main()
