from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.contracts.errors import ErrorCode, NotFoundError, ValidationError
from app.contracts.ids import ModelId, SessionId, StepId, UserId
from app.contracts.semantic import ModelSummary, SemanticModel
from app.contracts.session import SessionState
from app.runtime import intent_execution
from app.runtime import session as session_ops

if TYPE_CHECKING:
    from app.core.engine import CoreEngine
    from app.runtime.ports import RuntimePorts
    from app.service import SemanticLayerService


class MarivoRuntime:
    """Use-case facade for the Marivo platform.

    Intent dispatchers (observe, compare, etc.) delegate to
    runtime/intent_execution.  Session lifecycle methods delegate to
    runtime/session.  Artifact/step I/O methods use ports directly.
    Semantic methods route through runtime/semantic_ops.
    Ghost methods (query_session_state, get_proposition_context,
    discover_catalog) still use _svc pending Task 17 audit.
    """

    def __init__(
        self,
        ports: RuntimePorts,
        core: CoreEngine,
    ) -> None:
        self._ports = ports
        self._core = core
        self._svc: SemanticLayerService | None = None  # set via wire_svc()
        self._semantic_v2_svc: Any = None  # set via wire_semantic_v2_svc()
        self._datasource_svc: Any = None  # set via wire_datasource_svc()
        self._app: Any = None  # set via wire_app()

    def wire_svc(self, svc: SemanticLayerService) -> None:
        """Attach the backing service for ghost methods.

        Retained for ghost methods (query_session_state,
        get_proposition_context, discover_catalog) and for
        semantic_ops internals that still reference runtime.svc.
        Will be removed after Task 17.
        """
        self._svc = svc

    def wire_semantic_v2_svc(self, svc: Any) -> None:
        """Attach the SemanticModelV2Service for V2 CRUD operations."""
        self._semantic_v2_svc = svc

    def wire_datasource_svc(self, svc: Any) -> None:
        """Attach the DatasourceService for datasource operations."""
        self._datasource_svc = svc

    def wire_app(self, app: Any) -> None:
        """Store reference to the FastAPI app for OpenAPI introspection."""
        self._app = app

    @property
    def core(self) -> CoreEngine:
        """Pure computation facade (no I/O)."""
        return self._core

    @property
    def ports(self) -> RuntimePorts:
        """Typed container for all port implementations."""
        return self._ports

    @property
    def semantic_v2_svc(self) -> Any:
        """SemanticModelV2Service for V2 CRUD operations."""
        return self._semantic_v2_svc

    @property
    def datasource_svc(self) -> Any:
        """DatasourceService for datasource operations."""
        return self._datasource_svc

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _require_svc(self, method_name: str) -> SemanticLayerService:
        """Return self._svc, asserting it has been wired."""
        assert self._svc is not None, f"Runtime.{method_name} requires _svc (not yet wired)"
        return self._svc

    # ------------------------------------------------------------------
    # Artifact / Step I/O  (ports-direct)
    # ------------------------------------------------------------------

    def resolve_artifact_for_ref(self, session_id: str, step_id: str) -> dict[str, Any] | None:
        """Return the content of the most recent committed artifact for a step ref."""
        return self._ports.artifact_store.resolve_artifact_for_ref(
            SessionId(session_id), StepId(step_id)
        )

    def resolve_artifact_id_for_step(self, session_id: str, step_id: str) -> str | None:
        """Return the artifact_id of the most recent committed artifact for a step."""
        result = self._ports.artifact_store.resolve_artifact_id_for_step(
            SessionId(session_id), StepId(step_id)
        )
        return str(result) if result is not None else None

    def resolve_artifact_with_id(
        self, session_id: str, step_id: str
    ) -> tuple[str, dict[str, Any]] | None:
        """Return (artifact_id, content) for the most recent committed artifact."""
        result = self._ports.artifact_store.resolve_artifact_with_id(
            SessionId(session_id), StepId(step_id)
        )
        if result is None:
            return None
        artifact_id, content = result
        return str(artifact_id), content

    def commit_artifact_with_extraction(self, *args: Any, **kwargs: Any) -> str:
        """Canonical commit boundary for mandatory-extraction artifacts."""
        result = self._ports.artifact_store.commit_artifact_with_extraction(*args, **kwargs)
        return str(result)

    def insert_step(self, *args: Any, **kwargs: Any) -> None:
        """Insert a step record."""
        self._ports.step_store.insert_step(*args, **kwargs)

    def insert_artifact(self, *args: Any, **kwargs: Any) -> str:
        """Insert a raw artifact (no extraction boundary)."""
        result = self._ports.artifact_store.insert_artifact(*args, **kwargs)
        return str(result)

    # ------------------------------------------------------------------
    # Semantic / Routing I/O  (via semantic_ops)
    # ------------------------------------------------------------------
    # These methods route through runtime/semantic_ops which internally
    # uses runtime.svc for semantic_repository and related internals.
    # The svc dependency will be cleaned up in Task 17.

    def resolve_metric_execution_context(self, *args: Any, **kwargs: Any) -> Any:
        from app.runtime import semantic_ops

        return semantic_ops.resolve_metric_execution_context(self, *args, **kwargs)

    def compile_step(self, *args: Any, **kwargs: Any) -> Any:
        from app.runtime import semantic_ops

        return semantic_ops.compile_step_with_feedback(self, *args, **kwargs)

    def resolve_metric_dimensions(self, *args: Any, **kwargs: Any) -> list[str] | None:
        from app.runtime import semantic_ops

        return semantic_ops.resolve_metric_dimensions(self, *args, **kwargs)

    def resolve_metric(self, *args: Any, **kwargs: Any) -> Any:
        from app.runtime import semantic_ops

        return semantic_ops.resolve_metric(self, *args, **kwargs)

    def resolve_metric_table(self, *args: Any, **kwargs: Any) -> str | None:
        from app.runtime import semantic_ops

        return semantic_ops.resolve_metric_table(self, *args, **kwargs)

    def resolve_metric_sql_for_execution(self, *args: Any, **kwargs: Any) -> str:
        from app.runtime import semantic_ops

        return semantic_ops.resolve_metric_sql_for_execution(self, *args, **kwargs)

    def resolve_metric_value_sql_for_execution(self, *args: Any, **kwargs: Any) -> str | None:
        from app.runtime import semantic_ops

        return semantic_ops.resolve_metric_value_sql_for_execution(self, *args, **kwargs)

    def resolve_scope_constraint_column(self, *args: Any, **kwargs: Any) -> str:
        from app.runtime import semantic_ops

        return semantic_ops._resolve_scope_constraint_column(self, *args, **kwargs)

    def resolve_engine_for_session(self, *args: Any, **kwargs: Any) -> Any:
        from app.runtime import semantic_ops

        return semantic_ops.resolve_engine_for_session(self, *args, **kwargs)

    def resolve_engine(self, *args: Any, **kwargs: Any) -> Any:
        from app.runtime import semantic_ops

        return semantic_ops.resolve_engine(self, *args, **kwargs)

    def resolve_windowed_query_time_axis(self, *args: Any, **kwargs: Any) -> None:
        from app.runtime import semantic_ops

        return semantic_ops.resolve_windowed_query_time_axis(self, *args, **kwargs)

    def build_scoped_query(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        from app.runtime import semantic_ops

        return semantic_ops.build_scoped_query(self, *args, **kwargs)

    # --- Intent use-cases (delegated to intent_execution) ---

    @property
    def svc(self) -> SemanticLayerService:
        """Return the backing service, asserting it has been wired.

        Retained for semantic_ops and ghost method access.
        Will be removed after Task 17.
        """
        assert self._svc is not None, "MarivoRuntime.svc accessed before wiring"
        return self._svc

    def observe(self, session_id: str, params: dict[str, Any]) -> dict[str, Any]:
        return intent_execution.observe(self, SessionId(session_id), params)

    def compare(self, session_id: str, params: dict[str, Any]) -> dict[str, Any]:
        return intent_execution.compare(self, SessionId(session_id), params)

    def decompose(self, session_id: str, params: dict[str, Any]) -> dict[str, Any]:
        return intent_execution.decompose(self, SessionId(session_id), params)

    def correlate(self, session_id: str, params: dict[str, Any]) -> dict[str, Any]:
        return intent_execution.correlate(self, SessionId(session_id), params)

    def detect(self, session_id: str, params: dict[str, Any]) -> dict[str, Any]:
        return intent_execution.detect(self, SessionId(session_id), params)

    def test(self, session_id: str, params: dict[str, Any]) -> dict[str, Any]:
        return intent_execution.test(self, SessionId(session_id), params)

    def forecast(self, session_id: str, params: dict[str, Any]) -> dict[str, Any]:
        return intent_execution.forecast(self, SessionId(session_id), params)

    def attribute(self, session_id: str, params: dict[str, Any]) -> dict[str, Any]:
        return intent_execution.attribute(self, SessionId(session_id), params)

    def diagnose(self, session_id: str, params: dict[str, Any]) -> dict[str, Any]:
        return intent_execution.diagnose(self, SessionId(session_id), params)

    def validate(self, session_id: str, params: dict[str, Any]) -> dict[str, Any]:
        return intent_execution.validate(self, SessionId(session_id), params)

    # --- Session lifecycle (delegates to runtime/session) ---

    def create_session(
        self, goal: str, actor: UserId | None = None, **kwargs: Any
    ) -> SessionState | dict[str, Any]:
        """Create a new session, returning the rebuilt SessionState.

        Delegates to runtime/session.
        """
        return session_ops.create_session(self, goal, actor, **kwargs)

    def list_sessions(
        self, owner: UserId | None = None, **kwargs: Any
    ) -> list[SessionState] | dict[str, Any]:
        """Return sessions, optionally filtered by *owner*.

        When owner is provided, delegates to session_ops.
        Falls back to svc when owner is not provided (legacy
        pagination-based listing via MCP API).
        """
        if owner is not None:
            return session_ops.list_sessions(self, owner)
        return self._require_svc("list_sessions").list_sessions(**kwargs)

    def get_session(self, session_id: SessionId) -> SessionState | dict[str, Any]:
        """Get session state by ID.  Raises NotFoundError if not found.

        Delegates to runtime/session.
        """
        return session_ops.get_session(self, session_id)

    def terminate_session(
        self,
        session_id: SessionId,
        actor: UserId,
        terminal_reason: str = "user_closed",
    ) -> None:
        """Terminate a session.  Raises NotFoundError/ForbiddenError/ValidationError.

        Delegates to runtime/session.
        """
        session_ops.terminate_session(self, session_id, actor, terminal_reason=terminal_reason)

    def get_session_state(
        self, session_id: SessionId, **kwargs: Any
    ) -> SessionState | dict[str, Any]:
        """Get session state view by ID.

        When kwargs are provided (structured query from the API),
        delegates to svc because the port-based path returns a flat
        SessionState which lacks proposition/finding/gap context.
        Without kwargs, delegates to get_session.
        Will be properly repointed in Task 14.
        """
        if kwargs:
            return self._require_svc("get_session_state").get_session_state(str(session_id), kwargs)
        return self.get_session(session_id)

    def get_session_runtime_status(self, session_id: SessionId) -> dict[str, Any]:
        """Return session-level operator runtime status."""
        return session_ops.get_session_runtime_status(self, session_id)

    def get_artifact_runtime_status(
        self, session_id: SessionId, artifact_id: str
    ) -> dict[str, Any]:
        """Return artifact-level operator runtime status."""
        return session_ops.get_artifact_runtime_status(self, session_id, artifact_id)

    def get_proposition_runtime_status(
        self, session_id: SessionId, proposition_id: str
    ) -> dict[str, Any]:
        """Return proposition-level operator runtime status."""
        return session_ops.get_proposition_runtime_status(self, session_id, proposition_id)

    # --- Ghost methods (still use _svc, pending Task 17 audit) ---

    def query_session_state(self, session_id: str, query: dict[str, Any]) -> dict[str, Any]:
        """Return the canonical SessionStateView with a structured query body."""
        return self._require_svc("query_session_state").query_session_state(session_id, query)

    def get_proposition_context(self, session_id: str, proposition_id: str) -> dict[str, Any]:
        """Return PropositionContextView for a proposition."""
        return self._require_svc("get_proposition_context").get_proposition_context(
            session_id, proposition_id
        )

    def discover_catalog(self) -> dict[str, Any]:
        """Return the API catalog of entities, models, and datasources."""
        return self._require_svc("discover_catalog").discover_catalog()

    # --- Semantic model ops ---

    def get_semantic_model(self, selector: Any) -> SemanticModel | None:
        return self._ports.model_store.get(selector)

    def save_semantic_model(self, model: SemanticModel, *, actor: UserId) -> ModelId:
        return self._ports.model_store.save(model, actor=actor, expected_revision=None)

    def list_semantic_models(self, query: Any) -> list[ModelSummary]:
        return self._ports.model_store.list(query)

    # --- Datasource ops ---

    # --- OpenAPI introspection ---

    _SCHEMA_REF_PREFIX = "#/components/schemas/"
    _ALLOWED_EXPANDS = frozenset({"request", "response", "schemas"})
    _HTTP_METHODS = ("get", "put", "post", "delete", "options", "head", "patch", "trace")
    _MAX_EXPANSION_DEPTH = 5

    def _require_app(self, method_name: str) -> Any:
        assert self._app is not None, f"Runtime.{method_name} requires _app (not yet wired)"
        return self._app

    def _get_openapi_spec(self) -> dict[str, Any]:
        app = self._require_app("_get_openapi_spec")
        spec = app.openapi()
        if not isinstance(spec, dict):
            msg = "FastAPI OpenAPI schema is not a JSON object."
            raise NotFoundError(ErrorCode.NOT_FOUND, msg)
        return spec

    def _get_component_schemas(self, spec: dict[str, Any]) -> dict[str, Any]:
        components = spec.get("components")
        if not isinstance(components, dict):
            return {}
        schemas = components.get("schemas")
        if not isinstance(schemas, dict):
            return {}
        return schemas

    @staticmethod
    def _collect_schema_refs(value: Any, refs: set[str]) -> None:
        if isinstance(value, dict):
            ref = value.get("$ref")
            if isinstance(ref, str) and ref.startswith(MarivoRuntime._SCHEMA_REF_PREFIX):
                refs.add(ref.removeprefix(MarivoRuntime._SCHEMA_REF_PREFIX))
            for nested in value.values():
                MarivoRuntime._collect_schema_refs(nested, refs)
            return
        if isinstance(value, list):
            for nested in value:
                MarivoRuntime._collect_schema_refs(nested, refs)

    @staticmethod
    def _expand_schema_refs(
        component_schemas: dict[str, Any],
        root_refs: Any,
        depth: int,
    ) -> dict[str, Any]:
        expanded: dict[str, Any] = {}
        frontier = set(root_refs)
        for _ in range(depth):
            if not frontier:
                break
            next_frontier: set[str] = set()
            for schema_name in sorted(frontier):
                if schema_name in expanded:
                    continue
                schema = component_schemas.get(schema_name)
                if not isinstance(schema, dict):
                    msg = f"OpenAPI schema references missing component schema {schema_name!r}."
                    raise NotFoundError(ErrorCode.NOT_FOUND, msg)
                expanded[schema_name] = schema
                discovered_refs: set[str] = set()
                MarivoRuntime._collect_schema_refs(schema, discovered_refs)
                next_frontier.update(discovered_refs)
            frontier = next_frontier.difference(expanded)
        return {name: expanded[name] for name in sorted(expanded)}

    def list_openapi_paths(self) -> dict[str, Any]:
        spec = self._get_openapi_spec()
        paths = spec.get("paths", {})
        component_schemas = self._get_component_schemas(spec)

        path_entries: list[dict[str, Any]] = []
        for path in sorted(paths):
            path_item = paths[path]
            if not isinstance(path_item, dict):
                continue
            operations: list[dict[str, Any]] = []
            for method in self._HTTP_METHODS:
                operation = path_item.get(method)
                if not isinstance(operation, dict):
                    continue
                tags = operation.get("tags")
                operations.append(
                    {
                        "method": method,
                        "operation_id": operation.get("operationId"),
                        "summary": operation.get("summary"),
                        "tags": tags if isinstance(tags, list) else [],
                    }
                )
            path_entries.append(
                {
                    "path": path,
                    "operations": operations,
                }
            )

        return {
            "openapi": spec.get("openapi"),
            "info": spec.get("info"),
            "paths": path_entries,
            "schemas": sorted(component_schemas),
        }

    def get_openapi_schema(self, schema_name: str, depth: int = 1) -> dict[str, Any]:
        spec = self._get_openapi_spec()
        component_schemas = self._get_component_schemas(spec)
        component_schema = component_schemas.get(schema_name)
        if not isinstance(component_schema, dict):
            msg = f"OpenAPI schema {schema_name!r} not found."
            raise NotFoundError(ErrorCode.NOT_FOUND, msg)

        schema_refs: set[str] = set()
        self._collect_schema_refs(component_schema, schema_refs)
        schema_refs.discard(schema_name)
        return {
            "schema_name": schema_name,
            "depth": depth,
            "schema": component_schema,
            "schemas": self._expand_schema_refs(component_schemas, schema_refs, depth),
        }

    def get_openapi_fragment(
        self,
        path: str,
        operation: str | None = None,
        expand: list[str] | None = None,
        depth: int = 1,
    ) -> dict[str, Any]:
        spec = self._get_openapi_spec()
        expand_values: set[str] = set()
        if expand:
            for item in expand:
                for token in item.split(","):
                    normalized = token.strip()
                    if normalized:
                        expand_values.add(normalized)
        invalid = sorted(expand_values - self._ALLOWED_EXPANDS)
        if invalid:
            allowed = ", ".join(sorted(self._ALLOWED_EXPANDS))
            rejected = ", ".join(invalid)
            msg = f"Invalid expand values: {rejected}. Allowed values: {allowed}."
            raise ValidationError(ErrorCode.VALIDATION, msg)

        paths = spec.get("paths", {})
        path_item = paths.get(path)
        if not isinstance(path_item, dict):
            msg = f"OpenAPI path {path!r} not found."
            raise NotFoundError(ErrorCode.NOT_FOUND, msg)

        fragment: dict[str, Any]
        schema_source: Any
        if operation is None:
            if "request" in expand_values or "response" in expand_values:
                msg = "'operation' is required when expand includes 'request' or 'response'."
                raise ValidationError(ErrorCode.VALIDATION, msg)
            fragment = {"path_item": path_item}
            schema_source = path_item
        else:
            operation_fragment = path_item.get(operation)
            if not isinstance(operation_fragment, dict):
                msg = f"OpenAPI operation {operation!r} not found for path."
                raise NotFoundError(ErrorCode.NOT_FOUND, msg)
            fragment = {"operation": operation_fragment}
            if "request" in expand_values and "requestBody" in operation_fragment:
                fragment["request_body"] = operation_fragment["requestBody"]
            if "response" in expand_values and "responses" in operation_fragment:
                fragment["responses"] = operation_fragment["responses"]
            schema_source = fragment

        if "schemas" in expand_values:
            schema_refs: set[str] = set()
            self._collect_schema_refs(schema_source, schema_refs)
            fragment["schemas"] = self._expand_schema_refs(
                self._get_component_schemas(spec), schema_refs, depth
            )

        return {
            "path": path,
            "operation": operation,
            "expand": sorted(expand_values),
            "depth": depth,
            "fragment": fragment,
        }

    def get_openapi_path_fragment(
        self,
        path: str,
        expand: list[str] | None = None,
        depth: int = 1,
    ) -> dict[str, Any]:
        spec = self._get_openapi_spec()
        expand_values: set[str] = set()
        if expand:
            for item in expand:
                for token in item.split(","):
                    normalized = token.strip()
                    if normalized:
                        expand_values.add(normalized)
        invalid = sorted(expand_values - self._ALLOWED_EXPANDS)
        if invalid:
            allowed = ", ".join(sorted(self._ALLOWED_EXPANDS))
            rejected = ", ".join(invalid)
            msg = f"Invalid expand values: {rejected}. Allowed values: {allowed}."
            raise ValidationError(ErrorCode.VALIDATION, msg)

        paths = spec.get("paths", {})
        path_item = paths.get(path)
        if not isinstance(path_item, dict):
            msg = f"OpenAPI path {path!r} not found."
            raise NotFoundError(ErrorCode.NOT_FOUND, msg)

        result: dict[str, Any] = {
            "path": path,
            "expand": sorted(expand_values),
            "depth": depth,
            "path_item": path_item,
        }
        if "schemas" in expand_values:
            schema_refs: set[str] = set()
            self._collect_schema_refs(path_item, schema_refs)
            result["schemas"] = self._expand_schema_refs(
                self._get_component_schemas(spec), schema_refs, depth
            )
        return result
