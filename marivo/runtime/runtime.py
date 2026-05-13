from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from marivo.contracts.aoi_runtime import AoiAtomicRequest
from marivo.contracts.errors import ErrorCode, NotFoundError, ValidationError
from marivo.contracts.ids import ArtifactId, ModelId, SessionId, StepId, UserId
from marivo.contracts.semantic import ModelSummary, SemanticModel
from marivo.contracts.session import SessionEvent, SessionState
from marivo.runtime import intent_execution
from marivo.runtime import session as session_ops

if TYPE_CHECKING:
    from marivo.core.engine import CoreEngine
    from marivo.runtime.ports import RuntimePorts


class MarivoRuntime:
    """Use-case facade for the Marivo platform.

    Intent dispatchers (observe, compare, etc.) delegate to
    runtime/intent_execution.  Session lifecycle methods delegate to
    runtime/session.  Artifact/step I/O methods use ports directly.
    Semantic methods route through runtime/semantic_ops.
    """

    def __init__(
        self,
        ports: RuntimePorts,
        core: CoreEngine,
    ) -> None:
        self._ports = ports
        self._core = core
        self._services: dict[str, Any] = {}
        self._app: Any = None  # set via wire_app()
        self._metadata: Any = None  # set via wire_metadata()
        self._analytics: Any = None  # set via wire_analytics()
        self._evidence_repos: Any = None  # set via wire_evidence_repos()
        self._calendar_data_reader: Any = None  # set via wire_calendar_data_reader()
        self._time_axis_metadata_provider: Any = None  # set via wire_time_axis_metadata_provider()

    def register_service(self, name: str, service: Any) -> None:
        """Register a non-port service for transport-layer access."""
        self._services[name] = service

    def get_service(self, name: str) -> Any:
        """Retrieve a registered service. Raises KeyError if not found."""
        return self._services[name]

    def wire_app(self, app: Any) -> None:
        """Store reference to the FastAPI app for OpenAPI introspection."""
        self._app = app

    def wire_metadata(self, metadata: Any) -> None:
        """Attach the MetadataStore for direct DB access (server mode)."""
        self._metadata = metadata

    def wire_analytics(self, analytics: Any) -> None:
        """Attach the AnalyticsEngine for query execution (server mode)."""
        self._analytics = analytics

    def wire_evidence_repos(self, repos: Any) -> None:
        """Attach the evidence repository dict (server mode)."""
        self._evidence_repos = repos

    def wire_calendar_data_reader(self, reader: Any) -> None:
        """Attach the calendar data reader (server mode)."""
        self._calendar_data_reader = reader

    def wire_time_axis_metadata_provider(self, provider: Any) -> None:
        """Attach the time axis metadata provider (server mode)."""
        self._time_axis_metadata_provider = provider

    @property
    def core(self) -> CoreEngine:
        """Pure computation facade (no I/O)."""
        return self._core

    @property
    def ports(self) -> RuntimePorts:
        """Typed container for all port implementations."""
        return self._ports

    @property
    def semantic_repository(self) -> Any:
        """SemanticRuntimeRepository for semantic ref resolution (all modes)."""
        return self._services.get("semantic_repository")

    @property
    def semantic_resolver(self) -> Any:
        """SemanticRuntimeRepository for semantic resolution (all modes)."""
        return self._services.get("semantic_repository")

    @property
    def metadata(self) -> Any:
        """MetadataStore for direct DB access (server mode)."""
        return self._metadata

    @property
    def analytics(self) -> Any:
        """AnalyticsEngine for query execution (server mode)."""
        return self._analytics

    @property
    def evidence_repos(self) -> Any:
        """Evidence repository dict (server mode)."""
        return self._evidence_repos

    @property
    def calendar_data_reader(self) -> Any:
        """Calendar data reader (server mode)."""
        return self._calendar_data_reader

    @property
    def time_axis_metadata_provider(self) -> Any:
        """Time axis metadata provider (server mode)."""
        return self._time_axis_metadata_provider

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

    def resolve_artifact_by_id(self, session_id: str, artifact_id: str) -> dict[str, Any] | None:
        """Return committed artifact content for a session-scoped artifact_id."""
        return self._ports.artifact_store.resolve_artifact_by_id(
            SessionId(session_id), ArtifactId(artifact_id)
        )

    def commit_artifact_with_extraction(self, *args: Any, **kwargs: Any) -> str:
        """Canonical commit boundary for mandatory-extraction artifacts.

        After a successful commit, appends a ``step_completed`` event to the
        session event log so that session state always reflects step completion
        timestamps.

        When the session store is a ``SqlSessionStore`` and the metadata
        store is available via ``runtime.metadata``, both the artifact commit
        and the event append happen inside a single database transaction,
        guaranteeing atomicity.  Otherwise falls back to separate
        transactions.
        """
        # Extract session_id and step_id for the step_completed event.
        # commit_artifact_with_extraction is called positionally as:
        #   (session_id, step_id, artifact_type, name, content, *, step_type=...)
        # or via kwargs.
        session_id = kwargs.get("session_id") or (args[0] if args else None)
        step_id = kwargs.get("step_id") or (args[1] if len(args) > 1 else None)

        session_store = self._ports.session_store
        metadata = self._metadata

        # Attempt atomic write when both SqlSessionStore and MetadataStore
        # are available (server mode).
        if (
            session_id
            and step_id
            and metadata is not None
            and hasattr(session_store, "append_event_with_connection")
        ):
            with metadata.connect() as con:
                kwargs["con"] = con
                result = self._ports.artifact_store.commit_artifact_with_extraction(*args, **kwargs)
                session_store.append_event_with_connection(
                    SessionId(str(session_id)),
                    SessionEvent(
                        session_id=SessionId(str(session_id)),
                        event_type="step_completed",
                        timestamp=datetime.now(UTC).isoformat(),
                        payload={"step_id": str(step_id)},
                        actor=None,
                    ),
                    con,
                )
                con.commit()
        else:
            result = self._ports.artifact_store.commit_artifact_with_extraction(*args, **kwargs)
            if session_id and step_id:
                session_store.append_event(
                    SessionId(str(session_id)),
                    SessionEvent(
                        session_id=SessionId(str(session_id)),
                        event_type="step_completed",
                        timestamp=datetime.now(UTC).isoformat(),
                        payload={"step_id": str(step_id)},
                        actor=None,
                    ),
                )
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
    # These methods route through runtime/semantic_ops which uses the
    # DataSource port for routing resolution.

    def resolve_metric_execution_context(self, *args: Any, **kwargs: Any) -> Any:
        from marivo.runtime import semantic_ops

        return semantic_ops.resolve_metric_execution_context(self, *args, **kwargs)

    def compile_step(self, *args: Any, **kwargs: Any) -> Any:
        from marivo.runtime import semantic_ops

        return semantic_ops.compile_step_with_feedback(self, *args, **kwargs)

    def resolve_metric_dimensions(self, *args: Any, **kwargs: Any) -> list[str] | None:
        from marivo.runtime import semantic_ops

        return semantic_ops.resolve_metric_dimensions(self, *args, **kwargs)

    def resolve_metric(self, *args: Any, **kwargs: Any) -> Any:
        from marivo.runtime import semantic_ops

        return semantic_ops.resolve_metric(self, *args, **kwargs)

    def resolve_metric_table(self, *args: Any, **kwargs: Any) -> str | None:
        from marivo.runtime import semantic_ops

        return semantic_ops.resolve_metric_table(self, *args, **kwargs)

    def resolve_metric_sql_for_execution(self, *args: Any, **kwargs: Any) -> str:
        from marivo.runtime import semantic_ops

        return semantic_ops.resolve_metric_sql_for_execution(self, *args, **kwargs)

    def resolve_metric_value_sql_for_execution(self, *args: Any, **kwargs: Any) -> str | None:
        from marivo.runtime import semantic_ops

        return semantic_ops.resolve_metric_value_sql_for_execution(self, *args, **kwargs)

    def resolve_scope_constraint_column(self, *args: Any, **kwargs: Any) -> str:
        from marivo.runtime import semantic_ops

        return semantic_ops._resolve_scope_constraint_column(self, *args, **kwargs)

    def resolve_engine_for_session(self, *args: Any, **kwargs: Any) -> Any:
        from marivo.runtime import semantic_ops

        return semantic_ops.resolve_engine_for_session(self, *args, **kwargs)

    def resolve_engine(self, *args: Any, **kwargs: Any) -> Any:
        from marivo.runtime import semantic_ops

        return semantic_ops.resolve_engine(self, *args, **kwargs)

    def resolve_windowed_query_time_axis(self, *args: Any, **kwargs: Any) -> None:
        from marivo.runtime import semantic_ops

        return semantic_ops.resolve_windowed_query_time_axis(self, *args, **kwargs)

    def build_scoped_query(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        from marivo.runtime import semantic_ops

        return semantic_ops.build_scoped_query(self, *args, **kwargs)

    # --- Intent use-cases (delegated to intent_execution) ---

    def observe(self, session_id: str, request: AoiAtomicRequest) -> dict[str, Any]:
        return intent_execution.observe(self, SessionId(session_id), request)

    def compare(self, session_id: str, request: AoiAtomicRequest) -> dict[str, Any]:
        return intent_execution.compare(self, SessionId(session_id), request)

    def decompose(self, session_id: str, request: AoiAtomicRequest) -> dict[str, Any]:
        return intent_execution.decompose(self, SessionId(session_id), request)

    def correlate(self, session_id: str, request: AoiAtomicRequest) -> dict[str, Any]:
        return intent_execution.correlate(self, SessionId(session_id), request)

    def detect(self, session_id: str, request: AoiAtomicRequest) -> dict[str, Any]:
        return intent_execution.detect(self, SessionId(session_id), request)

    def test(self, session_id: str, request: AoiAtomicRequest) -> dict[str, Any]:
        return intent_execution.test(self, SessionId(session_id), request)

    def forecast(self, session_id: str, request: AoiAtomicRequest) -> dict[str, Any]:
        return intent_execution.forecast(self, SessionId(session_id), request)

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
        When owner is not provided, delegates to session_store.list_sessions_paginated
        for paginated listing (server-mode only).
        """
        if owner is not None:
            return session_ops.list_sessions(self, owner)
        return self.ports.session_store.list_sessions_paginated(**kwargs)

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
        delegates to session_ops.get_session_state_view which uses
        evidence_repos from runtime.ports (server mode).
        Without kwargs, delegates to get_session.
        """
        if kwargs:
            return session_ops.get_session_state_view(self, session_id, kwargs)
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

    # --- Evidence context / catalog (delegated to session_ops) ---

    def query_session_state(self, session_id: str, query: dict[str, Any]) -> dict[str, Any]:
        """Return the canonical SessionStateView with a structured query body."""
        return session_ops.query_session_state(self, session_id, query)

    def get_proposition_context(self, session_id: str, proposition_id: str) -> dict[str, Any]:
        """Return PropositionContextView for a proposition."""
        return session_ops.get_proposition_context(self, session_id, proposition_id)

    # --- Semantic model ops ---

    def get_semantic_model(self, selector: Any) -> SemanticModel | None:
        return self._ports.model_store.get(selector)

    def save_semantic_model(self, model: SemanticModel, *, actor: UserId) -> ModelId:
        return self._ports.model_store.save(model, actor=actor)

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
