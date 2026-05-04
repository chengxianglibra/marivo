from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

SurfaceKind = Literal["tool", "resource"]
SupportTier = Literal["p0", "p1", "deferred"]


@dataclass(frozen=True)
class McpSurfaceSpec:
    """Executable inventory entry for one MCP tool or resource."""

    name: str
    kind: SurfaceKind
    tier: SupportTier
    implemented: bool
    http_method: str | None = None
    http_paths: tuple[str, ...] = ()
    notes: str = ""


SURFACE_SPECS: tuple[McpSurfaceSpec, ...] = (
    # ------------------------------------------------------------------
    # Health & Discovery
    # ------------------------------------------------------------------
    McpSurfaceSpec("health_check", "tool", "p0", True, "GET", ("/health",)),
    McpSurfaceSpec("get_catalog", "tool", "p1", True, "GET", ("/catalog",)),
    McpSurfaceSpec("list_openapi_paths", "tool", "p0", True, "GET", ("/openapi/index",)),
    McpSurfaceSpec(
        "get_openapi_schema",
        "tool",
        "p0",
        True,
        "GET",
        ("/openapi/schemas/{schema_name}",),
    ),
    McpSurfaceSpec(
        "get_openapi_fragment",
        "tool",
        "p0",
        True,
        "GET",
        ("/openapi/fragment",),
    ),
    McpSurfaceSpec(
        "get_openapi_path_fragment",
        "tool",
        "p1",
        True,
        "GET",
        ("/openapi/paths/{encoded_path}",),
    ),
    # ------------------------------------------------------------------
    # Sessions & Intents
    # ------------------------------------------------------------------
    McpSurfaceSpec("create_session", "tool", "p0", True, "POST", ("/sessions",)),
    McpSurfaceSpec("list_sessions", "tool", "p0", True, "GET", ("/sessions",)),
    McpSurfaceSpec("get_session", "tool", "p0", True, "GET", ("/sessions/{session_id}",)),
    McpSurfaceSpec(
        "terminate_session",
        "tool",
        "p0",
        True,
        "POST",
        ("/sessions/{session_id}/terminate",),
    ),
    McpSurfaceSpec(
        "get_session_state",
        "tool",
        "p0",
        True,
        "GET",
        ("/sessions/{session_id}/state",),
    ),
    McpSurfaceSpec(
        "query_session_state",
        "tool",
        "p0",
        True,
        "POST",
        ("/sessions/{session_id}/state/query",),
    ),
    McpSurfaceSpec(
        "get_proposition_context",
        "tool",
        "p0",
        True,
        "GET",
        ("/sessions/{session_id}/propositions/{proposition_id}/context",),
    ),
    McpSurfaceSpec(
        "observe",
        "tool",
        "p0",
        True,
        "POST",
        ("/sessions/{session_id}/intents/observe",),
    ),
    McpSurfaceSpec(
        "compare",
        "tool",
        "p0",
        True,
        "POST",
        ("/sessions/{session_id}/intents/compare",),
    ),
    McpSurfaceSpec(
        "decompose",
        "tool",
        "p0",
        True,
        "POST",
        ("/sessions/{session_id}/intents/decompose",),
    ),
    McpSurfaceSpec(
        "correlate",
        "tool",
        "p0",
        True,
        "POST",
        ("/sessions/{session_id}/intents/correlate",),
    ),
    McpSurfaceSpec(
        "detect",
        "tool",
        "p0",
        True,
        "POST",
        ("/sessions/{session_id}/intents/detect",),
    ),
    McpSurfaceSpec(
        "test_intent",
        "tool",
        "p0",
        True,
        "POST",
        ("/sessions/{session_id}/intents/test",),
    ),
    McpSurfaceSpec(
        "forecast",
        "tool",
        "p0",
        True,
        "POST",
        ("/sessions/{session_id}/intents/forecast",),
    ),
    McpSurfaceSpec(
        "attribute",
        "tool",
        "p0",
        True,
        "POST",
        ("/sessions/{session_id}/intents/attribute",),
    ),
    McpSurfaceSpec(
        "diagnose",
        "tool",
        "p0",
        True,
        "POST",
        ("/sessions/{session_id}/intents/diagnose",),
    ),
    McpSurfaceSpec(
        "validate",
        "tool",
        "p0",
        True,
        "POST",
        ("/sessions/{session_id}/intents/validate",),
    ),
    # ------------------------------------------------------------------
    # Semantic Models V2 (OSI-aligned)
    # ------------------------------------------------------------------
    McpSurfaceSpec("create_semantic_model", "tool", "p0", True, "POST", ("/semantic-models",)),
    McpSurfaceSpec("list_semantic_models", "tool", "p0", True, "GET", ("/semantic-models",)),
    McpSurfaceSpec("import_osi_document", "tool", "p0", True, "POST", ("/semantic-models/import",)),
    McpSurfaceSpec(
        "get_semantic_model",
        "tool",
        "p0",
        True,
        "GET",
        ("/semantic-models/{model}",),
    ),
    McpSurfaceSpec(
        "update_semantic_model",
        "tool",
        "p0",
        True,
        "PUT",
        ("/semantic-models/{model}",),
    ),
    McpSurfaceSpec(
        "delete_semantic_model",
        "tool",
        "p0",
        True,
        "DELETE",
        ("/semantic-models/{model}",),
    ),
    McpSurfaceSpec(
        "get_semantic_model_readiness",
        "tool",
        "p0",
        True,
        "GET",
        ("/semantic-models/{model}/readiness",),
    ),
    McpSurfaceSpec(
        "create_dataset",
        "tool",
        "p0",
        True,
        "POST",
        ("/semantic-models/{model}/datasets",),
    ),
    McpSurfaceSpec(
        "list_datasets",
        "tool",
        "p0",
        True,
        "GET",
        ("/semantic-models/{model}/datasets",),
    ),
    McpSurfaceSpec(
        "get_dataset",
        "tool",
        "p0",
        True,
        "GET",
        ("/semantic-models/{model}/datasets/{name}",),
    ),
    McpSurfaceSpec(
        "update_dataset",
        "tool",
        "p0",
        True,
        "PUT",
        ("/semantic-models/{model}/datasets/{name}",),
    ),
    McpSurfaceSpec(
        "delete_dataset",
        "tool",
        "p0",
        True,
        "DELETE",
        ("/semantic-models/{model}/datasets/{name}",),
    ),
    McpSurfaceSpec(
        "create_relationship",
        "tool",
        "p0",
        True,
        "POST",
        ("/semantic-models/{model}/relationships",),
    ),
    McpSurfaceSpec(
        "list_relationships",
        "tool",
        "p0",
        True,
        "GET",
        ("/semantic-models/{model}/relationships",),
    ),
    McpSurfaceSpec(
        "get_relationship",
        "tool",
        "p0",
        True,
        "GET",
        ("/semantic-models/{model}/relationships/{name}",),
    ),
    McpSurfaceSpec(
        "update_relationship",
        "tool",
        "p0",
        True,
        "PUT",
        ("/semantic-models/{model}/relationships/{name}",),
    ),
    McpSurfaceSpec(
        "delete_relationship",
        "tool",
        "p0",
        True,
        "DELETE",
        ("/semantic-models/{model}/relationships/{name}",),
    ),
    McpSurfaceSpec(
        "create_metric",
        "tool",
        "p0",
        True,
        "POST",
        ("/semantic-models/{model}/metrics",),
    ),
    McpSurfaceSpec(
        "list_metrics",
        "tool",
        "p0",
        True,
        "GET",
        ("/semantic-models/{model}/metrics",),
    ),
    McpSurfaceSpec(
        "get_metric",
        "tool",
        "p0",
        True,
        "GET",
        ("/semantic-models/{model}/metrics/{name}",),
    ),
    McpSurfaceSpec(
        "update_metric",
        "tool",
        "p0",
        True,
        "PUT",
        ("/semantic-models/{model}/metrics/{name}",),
    ),
    McpSurfaceSpec(
        "delete_metric",
        "tool",
        "p0",
        True,
        "DELETE",
        ("/semantic-models/{model}/metrics/{name}",),
    ),
    # ------------------------------------------------------------------
    # Governance
    # ------------------------------------------------------------------
    McpSurfaceSpec("create_policy", "tool", "p1", True, "POST", ("/policies",)),
    McpSurfaceSpec("list_policies", "tool", "p1", True, "GET", ("/policies",)),
    McpSurfaceSpec("get_policy", "tool", "p1", True, "GET", ("/policies/{policy_id}",)),
    McpSurfaceSpec("update_policy", "tool", "p1", True, "PUT", ("/policies/{policy_id}",)),
    McpSurfaceSpec("delete_policy", "tool", "p1", True, "DELETE", ("/policies/{policy_id}",)),
    McpSurfaceSpec("create_quality_rule", "tool", "p1", True, "POST", ("/quality-rules",)),
    McpSurfaceSpec("list_quality_rules", "tool", "p1", True, "GET", ("/quality-rules",)),
    McpSurfaceSpec(
        "delete_quality_rule", "tool", "p1", True, "DELETE", ("/quality-rules/{rule_id}",)
    ),
    McpSurfaceSpec("governance_check", "tool", "p1", True, "POST", ("/governance/check",)),
    # ------------------------------------------------------------------
    # Jobs
    # ------------------------------------------------------------------
    McpSurfaceSpec("submit_job", "tool", "p1", True, "POST", ("/jobs",)),
    McpSurfaceSpec("list_jobs", "tool", "p1", True, "GET", ("/jobs",)),
    McpSurfaceSpec("get_job", "tool", "p1", True, "GET", ("/jobs/{job_id}",)),
    McpSurfaceSpec("cancel_job", "tool", "p1", True, "POST", ("/jobs/{job_id}/cancel",)),
    # ------------------------------------------------------------------
    # Datasources
    # ------------------------------------------------------------------
    McpSurfaceSpec("list_datasources", "tool", "p1", True, "GET", ("/datasources",)),
    McpSurfaceSpec("create_datasource", "tool", "p1", True, "POST", ("/datasources",)),
    McpSurfaceSpec("get_datasource", "tool", "p1", True, "GET", ("/datasources/{datasource_id}",)),
    McpSurfaceSpec(
        "update_datasource", "tool", "p1", True, "PUT", ("/datasources/{datasource_id}",)
    ),
    McpSurfaceSpec(
        "delete_datasource", "tool", "p1", True, "DELETE", ("/datasources/{datasource_id}",)
    ),
    McpSurfaceSpec(
        "browse_schemas",
        "tool",
        "p1",
        True,
        "GET",
        ("/datasources/{datasource_id}/browse/schemas",),
    ),
    McpSurfaceSpec(
        "browse_tables",
        "tool",
        "p1",
        True,
        "GET",
        ("/datasources/{datasource_id}/browse/tables",),
    ),
    McpSurfaceSpec(
        "browse_columns",
        "tool",
        "p1",
        True,
        "GET",
        ("/datasources/{datasource_id}/browse/columns",),
    ),
    McpSurfaceSpec(
        "preview_table",
        "tool",
        "p1",
        True,
        "GET",
        ("/datasources/{datasource_id}/catalog/preview",),
    ),
    # ------------------------------------------------------------------
    # Resources
    # ------------------------------------------------------------------
    McpSurfaceSpec("marivo://server/config", "resource", "p1", True),
    McpSurfaceSpec(
        "marivo://sessions/{session_id}/state",
        "resource",
        "p0",
        True,
        "GET",
        ("/sessions/{session_id}/state",),
    ),
    McpSurfaceSpec(
        "marivo://sessions/{session_id}/propositions/{proposition_id}/context",
        "resource",
        "p0",
        True,
        "GET",
        ("/sessions/{session_id}/propositions/{proposition_id}/context",),
    ),
    McpSurfaceSpec(
        "marivo://semantic/{family}",
        "resource",
        "p0",
        True,
        "GET",
        (
            "/semantic-models",
            "/semantic-models/{model}/datasets",
            "/semantic-models/{model}/relationships",
            "/semantic-models/{model}/metrics",
        ),
    ),
)


def get_surface_specs() -> tuple[McpSurfaceSpec, ...]:
    return SURFACE_SPECS


def get_surface_spec(name: str) -> McpSurfaceSpec:
    for spec in SURFACE_SPECS:
        if spec.name == name:
            return spec
    raise KeyError(f"Unknown MCP surface {name!r}.")


def get_implemented_specs(kind: SurfaceKind | None = None) -> tuple[McpSurfaceSpec, ...]:
    return tuple(
        spec for spec in SURFACE_SPECS if spec.implemented and (kind is None or spec.kind == kind)
    )


def get_tier_specs(
    tier: SupportTier,
    *,
    kind: SurfaceKind | None = None,
    implemented_only: bool = False,
) -> tuple[McpSurfaceSpec, ...]:
    return tuple(
        spec
        for spec in SURFACE_SPECS
        if spec.tier == tier
        and (kind is None or spec.kind == kind)
        and (not implemented_only or spec.implemented)
    )
